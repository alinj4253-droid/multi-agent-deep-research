"""
Phase 5 / Phase 6 测试：

Phase 5 —— 熔断只反映“数据源可用性”，不反映“结果质量”：
- HTTP 200 空结果（数据源健康、无匹配）不计熔断，返回 no_results 而非“数据源不可用”；
- 抛异常（网络/超时/解析）才 record_failure；
- 一个健康空 + 一个故障，仍表达为 no_results（并附带故障说明）。

Phase 6 —— 缓存载荷与任务运行时元数据分离：
- 缓存对象不含 search_no / remaining_searches / cache_hit；
- 命中时深拷贝并用“当前任务”的预算重装饰，跨任务不串号；
- 修改返回对象不污染缓存。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.context import reset_thread_context, set_thread_context
from app.tools import web_search_tool as wst
from app.tools.search_common import (
    CircuitBreaker,
    decorate_runtime,
    to_cache_payload,
)
from app.tools.web_search_tool import search_with_fallback


def _empty(query, max_results=5, **kw):
    return {"engine": "x", "results": [], "no_results": True}


def _boom(query, **kw):
    raise RuntimeError("network down")


def _ok(query, max_results=5, **kw):
    return {"engine": "x", "results": [{"title": "A", "url": "http://a", "content": "a"}]}


def _breakers():
    return CircuitBreaker(threshold=2, cooldown=60), CircuitBreaker(threshold=2, cooldown=60)


# ---------- Phase 5 ----------
class TestEmptyVsFailure:
    def test_both_healthy_empty_is_no_results_not_failure(self):
        sx, duck = _breakers()
        r = search_with_fallback("q", searxng_fn=_empty, ddg_fn=_empty,
                                 sx_breaker=sx, duck_breaker=duck)
        assert r["results"] == []
        assert r.get("no_results") is True
        assert r.get("error") is None
        assert sx.failures == 0 and duck.failures == 0
        assert set(r["healthy_empty"]) == {"searxng", "duckduckgo"}

    def test_one_healthy_empty_one_down_still_no_results(self):
        sx, duck = _breakers()
        r = search_with_fallback("q", searxng_fn=_empty, ddg_fn=_boom,
                                 sx_breaker=sx, duck_breaker=duck)
        assert r.get("no_results") is True
        assert sx.failures == 0
        assert duck.failures == 1
        assert any("duckduckgo" in e for e in r["providers_tried"])

    def test_repeated_empty_does_not_open_breaker(self):
        sx, duck = _breakers()
        for _ in range(5):
            search_with_fallback("q", searxng_fn=_empty, ddg_fn=_empty,
                                 sx_breaker=sx, duck_breaker=duck)
        assert sx.failures == 0 and duck.failures == 0
        assert sx.allow() and duck.allow()

    def test_exception_records_failure_and_opens_breaker(self):
        sx, duck = _breakers()
        for _ in range(2):
            search_with_fallback("q", searxng_fn=_boom, ddg_fn=_boom,
                                 sx_breaker=sx, duck_breaker=duck)
        assert sx.failures >= 2 and sx.is_open
        r = search_with_fallback("q", searxng_fn=_boom, ddg_fn=_boom,
                                 sx_breaker=sx, duck_breaker=duck)
        assert r["error"] == "所有搜索数据源均不可用"

    def test_healthy_empty_then_hit(self):
        sx, duck = _breakers()
        r = search_with_fallback("q", searxng_fn=_empty, ddg_fn=_ok,
                                 sx_breaker=sx, duck_breaker=duck)
        assert r["provider"] == "duckduckgo"
        assert sx.failures == 0


# ---------- Phase 6 ----------
@pytest.fixture
def fake_web(monkeypatch):
    """把 internet_search 底层故障转移替换为注入假数据源的版本（离线确定）。"""
    sx = CircuitBreaker(2, 60)
    duck = CircuitBreaker(2, 60)

    def fake_fallback(query, max_results=5, region="wt-wt", **kwargs):
        return search_with_fallback(
            query,
            max_results=max_results,
            region=region,
            searxng_fn=_ok,
            ddg_fn=_ok,
            sx_breaker=sx,
            duck_breaker=duck,
        )

    monkeypatch.setattr(wst, "search_with_fallback", fake_fallback)
    monkeypatch.setattr(wst, "searxng_breaker", sx)
    wst.search_cache.cache.clear()
    wst.search_budget._counts.clear()
    yield


class TestCacheMetadataSeparation:
    def test_payload_helpers_strip_and_redecorate(self):
        result = {"results": [1, 2], "search_no": 3, "remaining_searches": 0,
                  "cache_hit": False, "provider": "x"}
        payload = to_cache_payload(result)
        assert "search_no" not in payload
        assert "remaining_searches" not in payload
        assert "cache_hit" not in payload
        assert payload["results"] == [1, 2]

        hit = decorate_runtime(payload, used=None, remaining=2, cache_hit=True)
        assert hit["cache_hit"] is True
        assert hit["remaining_searches"] == 2
        assert hit["search_no"] is None

        miss = decorate_runtime(payload, used=1, remaining=2, cache_hit=False)
        assert miss["search_no"] == 1 and miss["cache_hit"] is False

    def test_cached_object_has_no_task_metadata(self, fake_web):
        tok = set_thread_context("cache_thread_A")
        try:
            first = wst.internet_search.invoke({"query": "unique query alpha"})
            assert first["cache_hit"] is False
            assert first["search_no"] == 1

            key = (wst.SearchCache.normalize_query("unique query alpha"), "wt-wt", 5)
            stored = wst.search_cache.get(key)
            assert stored is not None
            for meta in ("search_no", "remaining_searches", "cache_hit"):
                assert meta not in stored

            second = wst.internet_search.invoke({"query": "unique query alpha"})
            assert second["cache_hit"] is True
            assert second["search_no"] is None
            assert second["remaining_searches"] == 2  # 命中不耗预算
        finally:
            reset_thread_context(tok)

    def test_cross_task_metadata_isolation(self, fake_web):
        q = "shared query beta"
        tokA = set_thread_context("iso_A")
        try:
            wst.internet_search.invoke({"query": q})
        finally:
            reset_thread_context(tokA)

        tokB = set_thread_context("iso_B")
        try:
            wst.search_budget.reset("iso_B")
            rB = wst.internet_search.invoke({"query": q})
            assert rB["cache_hit"] is True
            assert rB["remaining_searches"] == 3  # 新任务满额，非 A 残留的 2
            assert rB["search_no"] is None
        finally:
            reset_thread_context(tokB)

    def test_returned_copy_does_not_corrupt_cache(self, fake_web):
        tok = set_thread_context("iso_copy")
        try:
            wst.internet_search.invoke({"query": "copy query gamma"})
            hit = wst.internet_search.invoke({"query": "copy query gamma"})
            hit["results"].append({"title": "MUTATED"})
            hit["injected"] = True
            again = wst.internet_search.invoke({"query": "copy query gamma"})
            assert "injected" not in again
            assert all(r.get("title") != "MUTATED" for r in again["results"])
        finally:
            reset_thread_context(tok)


# ---------- Phase 6（学术检索同源治理） ----------
class TestAcademicCacheMetadata:
    def test_academic_cache_payload_excludes_task_metadata(self, monkeypatch):
        from app.tools import academic_search_tool as ast

        def fake_academic_search(query, sources=None, max_results_per_source=5, year_from=None):
            return {
                "query": query,
                "papers": [{"title": "Paper One", "year": 2026, "source": "arxiv"}],
                "sources": ["arxiv"],
            }

        monkeypatch.setattr(ast, "academic_search", fake_academic_search)
        ast.academic_cache.cache.clear()
        ast.academic_budget._counts.clear()

        tok = set_thread_context("acad_cache_A")
        try:
            first = ast.academic_paper_search.invoke({"query": "data fusion autonomous driving"})
            assert first["cache_hit"] is False
            assert first["search_no"] == 1

            # 缓存载荷不得含任务元数据
            key = (
                ast.SearchCache.normalize_query("data fusion autonomous driving"),
                None, "all", 5,
            )
            stored = ast.academic_cache.get(key)
            assert stored is not None
            for meta in ("search_no", "remaining_searches", "cache_hit"):
                assert meta not in stored

            second = ast.academic_paper_search.invoke(
                {"query": "data fusion autonomous driving"}
            )
            assert second["cache_hit"] is True
            assert second["search_no"] is None
        finally:
            reset_thread_context(tok)
