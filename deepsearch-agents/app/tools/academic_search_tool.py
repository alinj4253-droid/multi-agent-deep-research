"""
学术文献检索工具模块

在 academic_sources（arXiv / OpenAlex / Crossref）之上封装 LangChain 工具，
供"学术文献助手"调用。与网络搜索工具相互独立：
- internet_search      查公开网页、新闻、技术博客等非学术信息
- academic_paper_search 查论文、预印本、文献综述、引用等学术信息

包含与网页搜索一致的三项治理：检索预算（硬限制）、LRU 缓存、过程监控。
"""

from typing import Literal, Optional

from langchain_core.tools import tool

from app.api.context import get_thread_context
from app.api.monitor import monitor
from app.tools.academic_sources import academic_search
from app.tools.search_common import SearchBudget, SearchCache

# 学术检索一次调用即并发查询三源，绝大多数主题 1 次即可；
# 仅在结果明显偏离时允许换词再试 1 次，故硬上限设为 2
academic_budget = SearchBudget(max_per_session=2)
academic_cache = SearchCache(max_size=50)


@tool
def academic_paper_search(
    query: str,
    year_from: Optional[int] = None,
    sources: Optional[list[Literal["arxiv", "openalex", "crossref"]]] = None,
    max_results_per_source: int = 5,
):
    """
    检索学术论文与文献（聚合 arXiv、OpenAlex、Crossref 三个免费学术库）。

    专门用于：查找某主题的代表性论文、了解研究现状与发展脉络、文献综述、
    某方法/模型的提出工作与后续改进、论文发表年份/作者/引用情况/DOI/PDF。
    不用于检索新闻、官网、教程博客等非学术网页（那种需求请用 internet_search）。

    :param query: 学术主题或论文关键词，例如 "3D Gaussian Splatting dynamic scene"；
        只放术语，不要把年份写进检索词（年份用 year_from 表达）
    :param year_from: 可选，仅保留该年份及以后的论文，例如 2024。
        **当需求是"最新进展/近期趋势/某一年"时必须传**：用户点明年份就传该年份，
        只说"最新/近年"就传"当前年份减 2"。传了它排序会改为年份优先（同年内再比引用数），
        否则按被引次数排序，结果会被更早的经典论文占据。
        只有"发展脉络/综述/奠基工作"这类跨年代需求才不传。
    :param sources: 可选，限定数据源；默认同时检索 arxiv/openalex/crossref
    :param max_results_per_source: 每个数据源拉取条数，默认 5（Crossref 内部再做相关性+高被引双路）
    :return: 结构化论文列表（标题/作者/年份/摘要/引用数/期刊会议/DOI/PDF链接/来源）
    """
    thread_id = get_thread_context()

    # 1. 缓存命中（学术查询标准化后命中即返回，不消耗预算）
    cache_key = f"{query.strip().lower()}|{year_from}|{','.join(sources) if sources else 'all'}"
    cached = academic_cache.get(cache_key)
    if cached is not None:
        monitor.report_tool(tool_name="学术检索缓存命中", args={"query": query})
        return cached

    # 2. 检索次数硬预算
    if academic_budget.remaining(thread_id) <= 0:
        monitor.report_tool(tool_name="学术检索次数已达上限", args={"query": query})
        return {
            "query": query,
            "papers": [],
            "budget_exhausted": True,
            "note": (
                f"学术检索次数已达上限（{academic_budget.max_per_session} 次），"
                "禁止再次调用本工具。请立即基于已检索到的论文整理结论返回，"
                "信息缺口直接注明，不要重试。"
            ),
        }

    # 3. 消耗预算并并发检索三源
    used = academic_budget.consume(thread_id)
    monitor.report_tool(
        tool_name="学术文献检索",
        args={"query": query, "year_from": year_from, "search_no": used},
    )
    try:
        result = academic_search(
            query=query,
            sources=sources,
            max_results_per_source=max_results_per_source,
            year_from=year_from,
        )
    except Exception as e:
        return {"query": query, "papers": [], "error": f"学术检索失败：{str(e)}"}

    result["search_no"] = used
    result["remaining_searches"] = academic_budget.remaining(thread_id)

    # 4. 仅缓存有论文的成功查询
    if result.get("papers"):
        academic_cache.set(cache_key, result)

    return result


if __name__ == "__main__":
    from pprint import pprint

    pprint(academic_paper_search.invoke({"query": "3D Gaussian Splatting dynamic scene"}))
