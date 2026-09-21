"""
网络搜索工具模块（多数据源故障转移，免费、无需付费 API Key）

封装 internet_search 工具，供网络检索子智能体检索互联网公开信息。
数据源优先级：
1. SearXNG（本地自建元搜索引擎，聚合 Google/Bing/DuckDuckGo 等 70+ 源）——主
2. DuckDuckGo（ddgs 非官方接口，零配置）——兜底，SearXNG 不可用时自动降级

包含四项检索治理：
1. 检索次数预算（SearchBudget）：按会话硬性限制对外检索次数，防止无限搜索
2. 查询结果 LRU 缓存（SearchCache）：相同查询与参数直接返回，减少重复请求
3. 熔断器（CircuitBreaker）：数据源连续失败后短时跳过，避免反复等待超时
4. 关键词重排序：按查询词在标题/摘要中的匹配度二次排序，提升 Top-K 相关性
"""

import re
from typing import Callable, Optional

from langchain_core.tools import tool

from app.api.context import get_thread_context
from app.api.monitor import monitor
from app.tools.ddg_search import duckduckgo_search
from app.tools.search_common import (
    CircuitBreaker,
    SearchBudget,
    SearchCache,
)
from app.tools.searxng_search import searxng_search

# 向后兼容：历史代码/测试从本模块导入 SearchBudget / SearchCache
__all__ = [
    "internet_search",
    "SearchBudget",
    "SearchCache",
    "CircuitBreaker",
    "search_with_fallback",
]

# 每个研究任务最多实际对外检索 3 次（简单问题通常 1 次即可）
search_budget = SearchBudget(max_per_session=3)
search_cache = SearchCache(max_size=50, default_ttl=20 * 60)

# SearXNG 失败 2 次即熔断（其内部已含 http/wsl 双通道），冷却 60s；
# DuckDuckGo 作为最后兜底，阈值放宽
searxng_breaker = CircuitBreaker(threshold=2, cooldown=60.0)
ddg_breaker = CircuitBreaker(threshold=3, cooldown=30.0)


# ============================================================
# 结果重排序：基于关键词匹配度打分
# ============================================================
def rerank_results(query: str, results: list) -> list:
    """
    对搜索结果做轻量级重排序

    根据查询关键词在 title 和 content 中的出现次数打分，
    标题命中权重高于摘要命中，得分越高排得越前。
    """
    if not results or len(results) <= 1:
        return results

    keywords = [
        w
        for w in re.split(r"[\s,，。；;：:、？?！!]+", query.lower())
        if len(w) > 1
    ]

    def score_item(item):
        score = 0
        title = (item.get("title") or "").lower()
        content = (item.get("content") or "").lower()
        for kw in keywords:
            if kw in title:
                score += 10
            score += content.count(kw) * 2
        return score

    return sorted(results, key=score_item, reverse=True)


# ============================================================
# 多数据源故障转移（provider 可注入，便于离线单元测试）
# ============================================================
def search_with_fallback(
    query: str,
    max_results: int = 5,
    region: str = "wt-wt",
    *,
    searxng_fn: Callable = searxng_search,
    ddg_fn: Callable = duckduckgo_search,
    sx_breaker: Optional[CircuitBreaker] = None,
    duck_breaker: Optional[CircuitBreaker] = None,
) -> dict:
    """
    依次尝试 SearXNG -> DuckDuckGo，任一成功即返回；熔断器跳过不可用数据源。

    返回在统一结构上额外带 provider 字段，标明实际命中的数据源。
    全部失败时返回带 error 与 providers_tried 的字典（不抛异常，交由上层处理）。
    """
    sx_breaker = sx_breaker or searxng_breaker
    duck_breaker = duck_breaker or ddg_breaker
    errors: list[str] = []

    # 1) 主数据源：SearXNG
    if sx_breaker.allow():
        try:
            result = searxng_fn(query, max_results=max_results)
            if isinstance(result, dict) and result.get("results"):
                sx_breaker.record_success()
                result["provider"] = f"searxng({result.get('transport', 'http')})"
                return result
            sx_breaker.record_failure()
            errors.append("searxng: 空结果")
        except Exception as e:
            sx_breaker.record_failure()
            errors.append(f"searxng: {type(e).__name__}: {str(e)[:80]}")

    # 2) 兜底：DuckDuckGo
    if duck_breaker.allow():
        try:
            result = ddg_fn(query=query, max_results=max_results, region=region)
            if isinstance(result, dict) and result.get("results"):
                duck_breaker.record_success()
                result["provider"] = "duckduckgo"
                return result
            duck_breaker.record_failure()
            errors.append("duckduckgo: 空结果")
        except Exception as e:
            duck_breaker.record_failure()
            errors.append(f"duckduckgo: {type(e).__name__}: {str(e)[:80]}")

    return {
        "query": query,
        "results": [],
        "error": "所有搜索数据源均不可用",
        "providers_tried": errors,
    }


@tool
def internet_search(
    query: str,
    region: str = "wt-wt",
    max_results: int = 5,
):
    """
    根据用户问题检索互联网公开信息（优先本地自建 SearXNG 元搜索，自动降级 DuckDuckGo，
    全程免费、无需付费 API Key）。

    适用：外部公开网页、新闻、技术博客、最新进展、教程文档等非学术信息；
    查找学术论文/文献请改用 academic_paper_search。
    :param query: 搜索关键词或自然语言问题
    :param region: 地区，wt-wt 表示全球
    :param max_results: 返回的最大结果数
    :return: 结构化搜索结果（经过重排序优化，含实际命中的 provider）
    """
    thread_id = get_thread_context()

    # 1. 先查缓存（key 含 query/region/max_results），命中直接返回且不消耗预算
    cache_key = (
        SearchCache.normalize_query(query),
        region,
        max_results,
    )
    cached = search_cache.get(cache_key)
    if cached is not None:
        monitor.report_tool(tool_name="查询缓存命中", args={"query": query})
        return cached

    # 2. 检索次数预算硬限制：达到上限后不再对外请求，引导模型立即总结
    if search_budget.remaining(thread_id) <= 0:
        monitor.report_tool(
            tool_name="检索预算已用完，开始整理结果",
            args={"query": query, "limit": search_budget.max_per_session},
        )
        return {
            "query": query,
            "results": [],
            "budget_exhausted": True,
            "note": (
                f"【硬性停止指令】本任务的联网检索次数（共 {search_budget.max_per_session} 次）"
                "已经全部用完，这不是搜索失败，而是系统设定的硬上限。"
                "禁止再次调用 internet_search，即使更换关键词也不会执行、不会返回任何新结果。"
                "你现在必须立刻结束检索动作，基于本轮已经获得的搜索结果整理要点并返回给主智能体；"
                "若已有信息不足以覆盖某个子问题，直接注明'该点未检索到可靠来源'即可，严禁重试。"
            ),
        }

    # 3. 消耗一次预算，按 SearXNG -> DuckDuckGo 顺序故障转移
    used = search_budget.consume(thread_id)
    provider_label = "元搜索 SearXNG" if searxng_breaker.allow() else "SearXNG 熔断，DuckDuckGo 兜底"
    monitor.report_tool(
        tool_name="网络搜索工具",
        args={
            "query": query,
            "max_results": max_results,
            "search_no": used,
            "remaining": search_budget.remaining(thread_id),
            "preferred": provider_label,
        },
    )
    result = search_with_fallback(query, max_results=max_results, region=region)

    # 4. 重排序
    if isinstance(result, dict) and result.get("results"):
        result["results"] = rerank_results(query, result["results"])
        result["search_no"] = used
        result["remaining_searches"] = search_budget.remaining(thread_id)
        # 5. 仅缓存有结果的成功查询（空结果/错误不缓存）
        search_cache.set(cache_key, result)

    return result


if __name__ == "__main__":
    from pprint import pprint

    pprint(internet_search.invoke({"query": "3D Gaussian Splatting 最新进展"}))
