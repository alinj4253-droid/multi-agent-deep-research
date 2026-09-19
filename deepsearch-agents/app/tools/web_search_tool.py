"""
网络搜索工具模块（DuckDuckGo，免费、无需 API Key）

封装 internet_search 工具，供网络检索子智能体检索互联网公开信息。

包含三项检索优化：
1. 检索次数预算（SearchBudget）：按会话硬性限制对外检索次数，防止子智能体无限搜索，控制延迟与成本
2. LRU 语义缓存：相同查询直接返回缓存，减少重复请求和响应延迟
3. 关键词重排序：根据查询词在标题/摘要中的匹配度对结果二次排序，提升 Top-K 相关性
"""

import re
from collections import OrderedDict
from typing import Literal, Optional

from langchain_core.tools import tool

from app.api.context import get_thread_context
from app.api.monitor import monitor
from app.tools.ddg_search import duckduckgo_search


# ============================================================
# 检索次数预算：按会话限制"实际对外搜索"次数（硬限制）
# ============================================================
class SearchBudget:
    """
    按 thread_id 统计每个研究任务的实际对外检索次数

    提示词对模型的检索次数约束是"软约束"，模型可能不严格遵守；
    本类在工具层提供"硬限制"：达到上限后不再请求搜索引擎，
    直接返回引导模型基于已有结果总结的结构化提示，避免 Agent 陷入检索循环。
    缓存命中不消耗预算（没有产生对外请求）。
    """

    def __init__(self, max_per_session: int = 3):
        self.max_per_session = max_per_session
        self._counts: dict[str, int] = {}

    def reset(self, thread_id: str) -> None:
        """任务开始时重置该会话的计数"""
        self._counts.pop(thread_id, None)

    def remaining(self, thread_id: Optional[str]) -> int:
        """该会话剩余可用检索次数；无会话上下文时不限制"""
        if not thread_id:
            return self.max_per_session
        return max(0, self.max_per_session - self._counts.get(thread_id, 0))

    def consume(self, thread_id: Optional[str]) -> int:
        """消耗一次检索预算，返回消耗后的已用次数"""
        if not thread_id:
            return 0
        count = self._counts.get(thread_id, 0) + 1
        self._counts[thread_id] = count
        # 简单防护：字典过大时清理最早的一批（按插入顺序）
        if len(self._counts) > 200:
            for k in list(self._counts.keys())[:100]:
                self._counts.pop(k, None)
        return count


# 每个研究任务最多实际对外检索 3 次（简单问题通常 1 次即可）
search_budget = SearchBudget(max_per_session=3)


# ============================================================
# 语义缓存：LRU，最多保存 50 条最近查询结果
# ============================================================
class SearchCache:
    """基于 LRU 的搜索结果缓存，避免重复请求搜索引擎"""

    def __init__(self, max_size: int = 50):
        self.cache = OrderedDict()
        self.max_size = max_size

    def _normalize_query(self, query: str) -> str:
        """标准化查询：小写 + 去除多余空格，提高缓存命中率"""
        return re.sub(r"\s+", " ", query.strip().lower())

    def get(self, query: str):
        key = self._normalize_query(query)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None

    def set(self, query: str, result):
        key = self._normalize_query(query)
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = result
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


@tool
def internet_search(
    query: str,
    region: str = "wt-wt",
    max_results: int = 5,
):
    """
    根据用户问题检索互联网公开信息（DuckDuckGo 搜索引擎，免费无需 Key）

    注意：本工具只用于外部公开网页、新闻、学术信息等，不用于查询私有知识库
    :param query: 搜索关键词或自然语言问题
    :param region: 地区，wt-wt 表示全球
    :param max_results: 返回的最大结果数
    :return: 结构化搜索结果（经过重排序优化）
    """
    # 当前会话 ID，用于检索次数预算统计
    thread_id = get_thread_context()

    # 1. 先查缓存，命中直接返回（缓存命中不消耗检索预算）
    cached = search_cache.get(query)
    if cached is not None:
        monitor.report_tool(
            tool_name="语义缓存命中",
            args={"query": query},
        )
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

    # 3. 消耗一次预算，调用 DuckDuckGo 搜索（此处才上报一次工具开始事件）
    used = search_budget.consume(thread_id)
    monitor.report_tool(
        tool_name="网络搜索工具",
        args={
            "query": query,
            "max_results": max_results,
            "search_no": used,
            "remaining": search_budget.remaining(thread_id),
        },
    )
    try:
        result = duckduckgo_search(
            query=query,
            max_results=max_results,
            region=region,
        )
    except Exception as e:
        return {"query": query, "results": [], "error": f"搜索失败：{str(e)}"}

    # 4. 重排序
    if isinstance(result, dict) and "results" in result:
        result["results"] = rerank_results(query, result["results"])
        result["search_no"] = used
        result["remaining_searches"] = search_budget.remaining(thread_id)

    # 5. 仅缓存有结果的成功查询
    if isinstance(result, dict) and result.get("results"):
        search_cache.set(query, result)

    return result


if __name__ == "__main__":
    from pprint import pprint

    pprint(internet_search.invoke({"query": "3D Gaussian Splatting 最新进展"}))
