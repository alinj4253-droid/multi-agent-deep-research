"""
网络搜索工具模块（双搜索引擎 + 自动降级）

封装 internet_search 工具，供网络检索子智能体检索互联网公开信息。

设计：
1. 主搜索引擎 Tavily（面向 AI 优化，结果质量高）
2. 备用搜索引擎 DuckDuckGo（免费、无需 API Key）
3. Tavily 额度用完 / 请求失败时自动降级到 DuckDuckGo，保证系统可用性
4. LRU 语义缓存：相同查询直接返回缓存，减少 API 调用
5. 关键词重排序：对搜索结果二次排序，提升 Top-K 相关性
"""

import os
import re
from collections import OrderedDict
from typing import Literal

from dotenv import load_dotenv
from langchain_core.tools import tool
from tavily import TavilyClient

from app.api.monitor import monitor
from app.tools.ddg_search import duckduckgo_search

load_dotenv()


# TavilyClient 是实际访问搜索服务的客户端；模块级复用可避免每次工具调用重复初始化
# 允许 key 缺失：此时直接走 DuckDuckGo 降级路径
_tavily_key = os.getenv("TAVILY_API_KEY")
tavily_client = TavilyClient(api_key=_tavily_key) if _tavily_key else None


# ============================================================
# 语义缓存实现：LRU 缓存，最多保存 50 条最近查询结果
# ============================================================
class SearchCache:
    """基于 LRU 的搜索结果缓存，避免重复调用搜索 API"""

    def __init__(self, max_size: int = 50):
        self.cache = OrderedDict()
        self.max_size = max_size

    def _normalize_query(self, query: str) -> str:
        """标准化查询：小写 + 去除多余空格，提高缓存命中率"""
        return re.sub(r"\s+", " ", query.strip().lower())

    def get(self, query: str):
        key = self._normalize_query(query)
        if key in self.cache:
            # 移到末尾，表示最近使用
            self.cache.move_to_end(key)
            return self.cache[key]
        return None

    def set(self, query: str, result):
        key = self._normalize_query(query)
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = result
        # 超出上限时淘汰最久未使用的
        while len(self.cache) > self.max_size:
            self.cache.popitem(last=False)


search_cache = SearchCache(max_size=50)


# ============================================================
# 结果重排序：基于关键词匹配度打分
# ============================================================
def rerank_results(query: str, results: list) -> list:
    """
    对搜索结果做轻量级重排序

    根据查询关键词在 title 和 content 中的出现次数打分，
    得分越高说明与查询越相关，排在越前面。
    两个搜索引擎返回的字段（title/content）一致，可复用同一套逻辑。
    """
    if not results or len(results) <= 1:
        return results

    # 提取查询中的关键词（按空格/标点分词，过滤短词）
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
            # 标题中出现权重更高
            if kw in title:
                score += 10
            # 正文中出现权重稍低
            score += content.count(kw) * 2

        return score

    # 按得分降序排序
    return sorted(results, key=score_item, reverse=True)


def _search_with_tavily(
    query: str,
    topic: str,
    max_results: int,
    include_raw_content: bool,
) -> dict:
    """调用 Tavily 搜索，失败时抛出异常交由上层降级"""
    if tavily_client is None:
        raise RuntimeError("Tavily API Key 未配置")

    result = tavily_client.search(
        query=query,
        topic=topic,
        max_results=max_results,
        include_raw_content=include_raw_content,
    )
    result["engine"] = "tavily"
    return result


def _search_with_fallback(
    query: str,
    max_results: int,
) -> dict:
    """
    带自动降级的搜索：
    先尝试 Tavily，遇到额度不足 / 网络异常时切换到 DuckDuckGo
    """
    try:
        result = _search_with_tavily(
            query=query,
            topic="general",
            max_results=max_results,
            include_raw_content=False,
        )
        return result
    except Exception as tavily_error:
        # 降级：Tavily 不可用（额度用完、网络故障、key 缺失等）时切换到 DuckDuckGo
        error_msg = str(tavily_error)
        monitor.report_tool(
            tool_name="搜索引擎降级",
            args={
                "from": "tavily",
                "to": "duckduckgo",
                "reason": error_msg[:120],
            },
        )
        print(f"[Search] Tavily 不可用（{error_msg[:80]}），降级到 DuckDuckGo")
        return duckduckgo_search(query=query, max_results=max_results)


# @tool 会把函数签名和 docstring 暴露给 DeepAgents，模型据此决定是否调用以及如何填参
@tool
def internet_search(
    query: str,
    topic: Literal["news", "finance", "general"] = "general",
    max_results: int = 5,
    include_raw_content: bool = False,
):
    """
    根据用户问题检索互联网公开信息（Tavily 主搜索，DuckDuckGo 自动降级备用）

    注意：本工具只用于外部公开网页、新闻、政策等信息，不用于查询私有知识库
    :param query: 搜索关键词或自然语言问题
    :param topic: 搜索主题，可选 news、finance、general
    :param max_results: 返回的最大结果数
    :param include_raw_content: 是否返回网页原文内容
    :return: 结构化搜索结果（经过重排序优化，含 engine 字段标识实际使用的搜索引擎）
    """
    # 工具埋点：只要工具被调用，前端就能看到本次搜索参数
    monitor.report_tool(
        tool_name="网络搜索工具",
        args={
            "query": query,
            "topic": topic,
            "max_results": max_results,
        },
    )

    # 1. 先查缓存，命中则直接返回（缓存对两个引擎都生效）
    cached = search_cache.get(query)
    if cached is not None:
        monitor.report_tool(
            tool_name="语义缓存命中",
            args={"query": query, "engine": cached.get("engine", "unknown")},
        )
        return cached

    # 2. 未命中缓存：Tavily 主搜索，失败自动降级到 DuckDuckGo
    #    topic=news/finance 是 Tavily 专属能力，DuckDuckGo 不区分，降级时按通用搜索处理
    try:
        if tavily_client is not None and topic in ("news", "finance"):
            # 新闻/财经类查询优先直接走 Tavily；失败同样降级
            try:
                raw_result = _search_with_tavily(
                    query, topic, max_results, include_raw_content
                )
            except Exception:
                raw_result = duckduckgo_search(query=query, max_results=max_results)
        else:
            raw_result = _search_with_fallback(query, max_results)
    except Exception as e:
        # 两个引擎都失败时返回错误信息，而不是让整个任务崩溃
        return {"query": query, "results": [], "error": f"所有搜索引擎均不可用：{str(e)}"}

    # 3. 对搜索结果做重排序优化（两个引擎字段结构一致，逻辑复用）
    if isinstance(raw_result, dict) and "results" in raw_result:
        raw_result["results"] = rerank_results(query, raw_result["results"])

    # 4. 写入缓存（仅缓存有结果的成功查询，避免空结果污染缓存）
    if isinstance(raw_result, dict) and raw_result.get("results"):
        search_cache.set(query, raw_result)

    return raw_result


if __name__ == "__main__":
    from pprint import pprint

    # 本地调试：验证双搜索引擎和降级逻辑
    pprint(
        internet_search.invoke(
            {"query": "2026年3D Gaussian Splatting 最新进展"}
        )
    )
