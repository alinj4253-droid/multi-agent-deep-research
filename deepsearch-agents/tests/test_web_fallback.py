"""
网络检索多数据源故障转移单元测试（不依赖外网）

通过注入假的 searxng_fn / ddg_fn 和独立熔断器，验证：
1. SearXNG 正常时优先使用 SearXNG
2. SearXNG 抛异常或返回空时自动降级 DuckDuckGo
3. 两个数据源都失败时返回结构化错误（不抛异常）
4. SearXNG 熔断开路时直接跳过、不再调用它
"""

import pytest

from app.tools.search_common import CircuitBreaker
from app.tools.web_search_tool import search_with_fallback


def _sx_ok(query, max_results=5):
    return {
        "engine": "searxng",
        "transport": "wsl",
        "engines_used": ["google cse", "duckduckgo"],
        "results": [{"title": "SX", "url": "http://a", "content": "x", "score": 1}],
    }


def _ddg_ok(query, max_results=5, region="wt-wt"):
    return {
        "engine": "duckduckgo",
        "results": [{"title": "DDG", "url": "http://b", "content": "y"}],
    }


def _empty(query, **kwargs):
    return {"results": []}


def _boom(query, **kwargs):
    raise RuntimeError("network down")


def _breakers():
    return CircuitBreaker(threshold=2, cooldown=60), CircuitBreaker(threshold=3, cooldown=30)


class TestSearchWithFallback:
    def test_primary_searxng_success(self):
        sx, duck = _breakers()
        r = search_with_fallback("q", searxng_fn=_sx_ok, ddg_fn=_ddg_ok,
                                 sx_breaker=sx, duck_breaker=duck)
        assert r["provider"] == "searxng(wsl)"
        assert r["results"][0]["title"] == "SX"

    def test_fallback_to_ddg_on_exception(self):
        sx, duck = _breakers()
        r = search_with_fallback("q", searxng_fn=_boom, ddg_fn=_ddg_ok,
                                 sx_breaker=sx, duck_breaker=duck)
        assert r["provider"] == "duckduckgo"
        assert r["results"][0]["title"] == "DDG"

    def test_fallback_to_ddg_on_empty_result(self):
        sx, duck = _breakers()
        r = search_with_fallback("q", searxng_fn=_empty, ddg_fn=_ddg_ok,
                                 sx_breaker=sx, duck_breaker=duck)
        assert r["provider"] == "duckduckgo"

    def test_all_sources_fail_returns_structured_error(self):
        sx, duck = _breakers()
        r = search_with_fallback("q", searxng_fn=_boom, ddg_fn=_boom,
                                 sx_breaker=sx, duck_breaker=duck)
        assert r["results"] == []
        assert "error" in r
        # 两个数据源的失败都应被记录
        assert len(r["providers_tried"]) == 2
        assert any("searxng" in e for e in r["providers_tried"])
        assert any("duckduckgo" in e for e in r["providers_tried"])

    def test_open_breaker_skips_searxng(self):
        """SearXNG 熔断开路时，必须直接跳过它、一次都不调用，转用 DuckDuckGo"""
        sx = CircuitBreaker(threshold=1, cooldown=100)
        sx.record_failure()  # 用真实当前时间开路，冷却期内 allow() 为 False
        calls = {"n": 0}

        def sx_track(query, max_results=5):
            calls["n"] += 1
            return _sx_ok(query)

        duck = CircuitBreaker()
        r = search_with_fallback("q", searxng_fn=sx_track, ddg_fn=_ddg_ok,
                                 sx_breaker=sx, duck_breaker=duck)
        assert r["provider"] == "duckduckgo"
        assert calls["n"] == 0

    def test_searxng_success_closes_breaker(self):
        sx, duck = _breakers()
        sx.record_failure(now=0)  # 1 次失败，仍闭合
        r = search_with_fallback("q", searxng_fn=_sx_ok, ddg_fn=_ddg_ok,
                                 sx_breaker=sx, duck_breaker=duck)
        assert r["provider"].startswith("searxng")
        assert sx.failures == 0  # 成功后熔断计数清零
