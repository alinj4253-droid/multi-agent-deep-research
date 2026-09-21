"""
Phase 3 测试：查询结果 LRU 缓存的完整 key 与 TTL

覆盖改造方案 §7：
- key 必须包含所有影响结果的参数（max_results / region / year_from / sources）
- TTL 内命中、TTL 过期视为未命中
- 字面归一（大小写/空白）可命中，语义不同但字面不同不命中
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tools.search_common import SearchCache


def make_web_key(query, region="wt-wt", max_results=5):
    return (SearchCache.normalize_query(query), region, max_results)


def make_academic_key(query, year_from=None, sources=None, per_source=5):
    return (
        SearchCache.normalize_query(query),
        year_from,
        tuple(sorted(sources)) if sources else "all",
        per_source,
    )


def test_same_key_hit():
    c = SearchCache(10, default_ttl=60)
    c.set(make_web_key("3DGS 进展"), "resA", now=0)
    assert c.get(make_web_key("3DGS 进展"), now=10) == "resA"


def test_normalized_query_hit():
    c = SearchCache(10, default_ttl=60)
    c.set(make_web_key("  Gaussian Splatting 最新 "), "res", now=0)
    # 大小写/多余空白归一后应命中
    assert c.get(make_web_key("gaussian   splatting 最新"), now=1) == "res"


def test_different_max_results_not_mismatch():
    c = SearchCache(10, default_ttl=60)
    c.set(make_web_key("q", max_results=5), "five", now=0)
    assert c.get(make_web_key("q", max_results=10), now=1) is None


def test_different_region_not_mismatch():
    c = SearchCache(10, default_ttl=60)
    c.set(make_web_key("q", region="wt-wt"), "global", now=0)
    assert c.get(make_web_key("q", region="cn-zh"), now=1) is None


def test_academic_year_from_distinct():
    c = SearchCache(10, default_ttl=60)
    c.set(make_academic_key("4DGS", year_from=2025), "recent", now=0)
    assert c.get(make_academic_key("4DGS", year_from=2023), now=1) is None
    # 相同 year_from 命中
    assert c.get(make_academic_key("4DGS", year_from=2025), now=1) == "recent"


def test_academic_sources_distinct():
    c = SearchCache(10, default_ttl=60)
    c.set(make_academic_key("q", sources=["arxiv", "openalex"]), "two", now=0)
    assert c.get(make_academic_key("q", sources=["crossref"]), now=1) is None
    # 顺序不同但集合相同应命中
    assert c.get(make_academic_key("q", sources=["openalex", "arxiv"]), now=1) == "two"


def test_academic_per_source_distinct():
    c = SearchCache(10, default_ttl=60)
    c.set(make_academic_key("q", per_source=5), "five", now=0)
    assert c.get(make_academic_key("q", per_source=8), now=1) is None


def test_ttl_expired_misses():
    c = SearchCache(10, default_ttl=100)
    c.set("k", "v", now=0)
    assert c.get("k", now=50) == "v"       # TTL 内命中
    assert c.get("k", now=101) is None    # 过期 -> None
    # 过期后旧条目已被移除
    assert "k" not in c.cache


def test_no_ttl_never_expires():
    c = SearchCache(10)  # 未设 TTL
    c.set("k", "v", now=0)
    assert c.get("k", now=10 ** 9) == "v"
