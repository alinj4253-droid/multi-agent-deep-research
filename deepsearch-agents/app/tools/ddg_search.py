"""
DuckDuckGo 备用搜索模块

作为自建 SearXNG 的降级搜索引擎：完全免费、无需 API Key。
返回格式与 SearXNG 对齐（query / results / engine），
使上层的重排序、缓存等逻辑可以无缝复用。
"""

from ddgs import DDGS


def duckduckgo_search(
    query: str,
    max_results: int = 5,
    region: str = "wt-wt",
    safesearch: str = "moderate",
) -> dict:
    """
    使用 DuckDuckGo 检索网页，返回与 Tavily 对齐的结构

    :param query: 搜索关键词或自然语言问题
    :param max_results: 返回的最大结果数
    :param region: 地区，wt-wt 表示全球
    :param safesearch: 安全搜索级别
    :return: {"query", "results": [{"title", "url", "content"}], "engine"}
    """
    results = []
    # DDGS 客户端使用上下文管理器，确保底层连接被正确释放
    # 注意：ddgs 9.x 中 text() 的查询词是位置参数 query（旧版叫 keywords）
    with DDGS() as ddgs:
        raw_results = list(
            ddgs.text(
                query,
                region=region,
                safesearch=safesearch,
                max_results=max_results,
            )
        )

    for item in raw_results:
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("href", ""),
                # Tavily 用 content 字段存摘要，DuckDuckGo 对应 body 字段
                "content": item.get("body", ""),
            }
        )

    return {
        "query": query,
        "results": results,
        "engine": "duckduckgo",
    }


if __name__ == "__main__":
    from pprint import pprint

    pprint(duckduckgo_search("3D Gaussian Splatting 最新进展", max_results=3))
