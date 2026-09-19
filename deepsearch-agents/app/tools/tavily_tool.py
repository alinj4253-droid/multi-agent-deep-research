"""
Tavily 网络搜索工具模块

封装 internet_search 工具，供网络检索子智能体检索互联网公开信息。
新增优化：
1. 语义缓存：相同查询直接返回缓存结果，减少 API 调用
2. 结果重排序：根据关键词匹配度对搜索结果二次排序，提升 Top-K 相关性
"""

import os
import re
from collections import OrderedDict
from typing import Literal

from dotenv import load_dotenv
from langchain_core.tools import tool
from tavily import TavilyClient

from app.api.monitor import monitor

load_dotenv()


# TavilyClient 是实际访问搜索服务的客户端；模块级复用可避免每次工具调用重复初始化
tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))


# ============================================================
# 语义缓存实现：LRU 缓存，最多保存 50 条最近查询结果
# ============================================================
class SearchCache:
    """基于 LRU 的搜索结果缓存，避免重复调用 API"""

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
    得分越高说明与查询越相关，排在越前面
    """
    if not results or len(results) <= 1:
        return results

    # 提取查询中的关键词（按空格分词，过滤短词）
    keywords = [w for w in re.split(r"[\s,，。；;：:、]+", query.lower()) if len(w) > 1]

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
    ranked = sorted(results, key=score_item, reverse=True)
    return ranked


# @tool 会把函数签名和 docstring 暴露给 DeepAgents，模型据此决定是否调用以及如何填参
@tool
def internet_search(
    query: str,
    topic: Literal["news", "finance", "general"] = "general",
    max_results: int = 5,
    include_raw_content: bool = False,
):
    """
    根据用户问题检索互联网公开信息

    注意：本工具只用于外部公开网页、新闻、政策等信息，不用于查询业务数据库或私有知识库
    :param query: 搜索关键词或自然语言问题
    :param topic: 搜索主题，可选 news、finance、general
    :param max_results: 返回的最大结果数
    :param include_raw_content: 是否返回网页原文内容；False 返回摘要，True 尝试返回更完整正文
    :return: Tavily 返回的结构化搜索结果（经过重排序优化）
    """
    # 工具内部埋点比外层 stream 解析更直接：只要工具被调用，前端就能看到本次搜索参数
    monitor.report_tool(
        tool_name="网络搜索工具",
        args={
            "query": query,
            "topic": topic,
            "max_results": max_results,
        },
    )

    # 1. 先查缓存，命中则直接返回
    cached = search_cache.get(query)
    if cached is not None:
        monitor.report_tool(
            tool_name="语义缓存命中",
            args={"query": query},
        )
        return cached

    # 2. 未命中缓存，调用 Tavily API
    raw_result = tavily_client.search(
        query=query,
        topic=topic,
        max_results=max_results,
        include_raw_content=include_raw_content,
    )

    # 3. 对搜索结果做重排序优化
    if isinstance(raw_result, dict) and "results" in raw_result:
        raw_result["results"] = rerank_results(query, raw_result["results"])

    # 4. 写入缓存
    search_cache.set(query, raw_result)

    return raw_result


if __name__ == "__main__":
    from pprint import pprint

    # 本地调试入口：直接运行本文件可验证 TAVILY_API_KEY 和 Tavily API 是否可用
    pprint(
        internet_search.invoke(
            {"query": "2026年3D Gaussian Splatting 最新进展"}
        )
    )
