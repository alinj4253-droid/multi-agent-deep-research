"""
Benchmark 评测器：对真实业务代码做确定性断言，不依赖网络与 LLM。

每个离线用例返回 (passed: bool, detail: str)。
e2e_llm 类别由 run_benchmark.py 在 --e2e 且有凭据时单独处理，这里不触碰。
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tools.search_common import SearchBudget, SearchCache
from app.utils.path_utils import PathEscapeError, resolve_session_path
from app.utils.validators import InvalidThreadIdError, validate_thread_id


# ---------------- path_safety ----------------

def _path_case(case, root: Path) -> bool:
    kind = case["kind"]
    if kind == "relative_allowed":
        out = resolve_session_path("report.md", root)
        return out == (root / "report.md").resolve()
    if kind in ("traversal_rejected", "absolute_outside_rejected", "drive_rejected"):
        try:
            resolve_session_path(case["input"], root)
            return False  # 未抛异常 = 评测失败
        except PathEscapeError:
            return True
    return False


def eval_path_safety(cases):
    results = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "session_bench"
        root.mkdir()
        for c in cases:
            try:
                ok = _path_case(c, root)
            except Exception as e:  # noqa
                ok = False
                c.setdefault("_err", str(e))
            results.append((c["id"], ok))
    return results


# ---------------- thread_validator ----------------

def eval_validator(cases):
    out = []
    for c in cases:
        if c["kind"] == "valid":
            try:
                validate_thread_id(c["input"])
                ok = True
            except InvalidThreadIdError:
                ok = False
        else:
            try:
                validate_thread_id(c["input"])
                ok = False
            except InvalidThreadIdError:
                ok = True
        out.append((c["id"], ok))
    return out


# ---------------- cache_ttl ----------------

def eval_cache_ttl(cases):
    out = []
    for c in cases:
        kind = c["kind"]
        cache = SearchCache(10, default_ttl=100)
        try:
            if kind == "hit_within_ttl":
                cache.set(("q", 5), "v", now=0)
                ok = cache.get(("q", 5), now=50) == "v"
            elif kind == "expired_miss":
                cache.set("k", "v", now=0)
                ok = cache.get("k", now=101) is None and "k" not in cache.cache
            elif kind == "distinct_max_results":
                cache.set((SearchCache.normalize_query("q"), "wt-wt", 5), "five", now=0)
                ok = cache.get((SearchCache.normalize_query("q"), "wt-wt", 10), now=1) is None
            elif kind == "normalized_hit":
                cache.set(SearchCache.normalize_query("  Hello  World "), "v", now=0)
                ok = cache.get(SearchCache.normalize_query("hello world"), now=1) == "v"
            else:
                ok = False
        except Exception:  # noqa
            ok = False
        out.append((c["id"], ok))
    return out


# ---------------- tool_budget ----------------

def eval_tool_budget(cases):
    out = []
    for c in cases:
        kind = c["kind"]
        try:
            if kind == "web_budget":
                b = SearchBudget(3)
                b.consume("t"); b.consume("t")
                ok = b.remaining("t") == 1
            elif kind == "academic_budget":
                b = SearchBudget(2)
                b.consume("t")
                ok = b.remaining("t") == 1 and b.remaining("t") >= 0
            elif kind == "python_cap":
                from app.api.context import (
                    reset_session_context, set_session_context, set_thread_context,
                )
                from app.tools import python_exec_tool as pet

                async def _run():
                    sd = set_session_context(tempfile.mkdtemp())
                    sid = set_thread_context("bench_py")
                    pet._session_call_counts["bench_py"] = pet.MAX_CALLS_PER_TASK
                    r = await pet.execute_python_code.ainvoke({"code": "print(1)", "description": "x"})
                    reset_session_context(sd, sid)
                    pet.reset_session_call_count()
                    return "已达工具调用上限" in r

                ok = asyncio.run(_run())
            elif kind == "python_reset":
                from app.api.context import (
                    reset_session_context, set_session_context, set_thread_context,
                )
                from app.tools import python_exec_tool as pet

                async def _run():
                    sd = set_session_context(tempfile.mkdtemp())
                    sid = set_thread_context("bench_py2")
                    pet._session_call_counts["bench_py2"] = pet.MAX_CALLS_PER_TASK
                    pet.reset_session_call_count("bench_py2")
                    r = await pet.execute_python_code.ainvoke({"code": "print(2+2)", "description": "x"})
                    reset_session_context(sd, sid)
                    pet.reset_session_call_count()
                    return "4" in r and "已达工具调用上限" not in r

                ok = asyncio.run(_run())
            else:
                ok = False
        except Exception as e:  # noqa
            ok = False
            c["_err"] = str(e)
        out.append((c["id"], ok))
    return out


# ---------------- web_fallback (offline injection) ----------------

def eval_web_fallback(cases):
    from app.tools.web_search_tool import search_with_fallback
    from app.tools.search_common import CircuitBreaker

    out = []

    def ok_fn(name):
        def _fn(query, max_results=5, **kw):
            return {"results": [{"title": name}], "transport": "http"}
        return _fn

    def fail_fn(query, max_results=5, **kw):
        raise RuntimeError("boom")

    for c in cases:
        kind = c["kind"]
        try:
            if kind == "searxng_first":
                r = search_with_fallback("q", searxng_fn=ok_fn("sx"), ddg_fn=fail_fn,
                                         sx_breaker=CircuitBreaker(2, 60),
                                         duck_breaker=CircuitBreaker(3, 60))
                ok = r["provider"].startswith("searxng")
            elif kind == "fallback_to_ddg":
                r = search_with_fallback("q", searxng_fn=fail_fn, ddg_fn=ok_fn("ddg"),
                                         sx_breaker=CircuitBreaker(2, 60),
                                         duck_breaker=CircuitBreaker(3, 60))
                ok = r["provider"] == "duckduckgo"
            elif kind == "both_fail":
                r = search_with_fallback("q", searxng_fn=fail_fn, ddg_fn=fail_fn,
                                         sx_breaker=CircuitBreaker(2, 60),
                                         duck_breaker=CircuitBreaker(3, 60))
                ok = r["results"] == [] and "providers_tried" in r
            elif kind == "breaker_skips":
                sx = CircuitBreaker(2, 60); sx.record_failure(); sx.record_failure()
                called = {"n": 0}

                def counting_sx(query, max_results=5, **kw):
                    called["n"] += 1
                    return {"results": []}

                search_with_fallback("q", searxng_fn=counting_sx, ddg_fn=ok_fn("ddg"),
                                     sx_breaker=sx, duck_breaker=CircuitBreaker(3, 60))
                ok = called["n"] == 0  # 熔断后一次都不调用
            else:
                ok = False
        except Exception:  # noqa
            ok = False
        out.append((c["id"], ok))
    return out
