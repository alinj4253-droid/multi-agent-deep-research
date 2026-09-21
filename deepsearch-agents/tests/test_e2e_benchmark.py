"""
Phase 4 测试：端到端 Benchmark 的事件归类 + 真实 HTTP/WebSocket 链路冒烟。

- 前半部分对 benchmarks/schemas 的纯归类逻辑做离线断言（不触网）；
- 后半部分用 Starlette TestClient 走真实 /api/task + /ws/{id} + TaskManager + monitor，
  但把 run_deep_agent 替换成确定性 fake（不调 LLM），验证 e2e 链路本身正确。
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.schemas import build_case_result, build_report, summarize_events


def _ev(event, message="", data=None):
    return {
        "type": "monitor_event",
        "event": event,
        "message": message,
        "data": data or {},
    }


class TestSummarizeEvents:
    def test_completed_with_tool_counts(self):
        events = [
            _ev("session_created", data={"path": "/tmp/s"}),
            _ev("tool_start", data={"tool_name": "网络搜索工具", "args": {}}),
            _ev("tool_start", data={"tool_name": "学术文献检索", "args": {}}),
            _ev("tool_start", data={"tool_name": "Python代码执行工具", "args": {}}),
            _ev("task_result", data={"result": "最终研究结论"}),
        ]
        r = summarize_events(events)
        assert r["status"] == "passed"
        assert r["tool_calls"] == 3
        assert r["web_calls"] == 1
        assert r["academic_calls"] == 1
        assert r["python_calls"] == 1
        assert r["final_answer"] == "最终研究结论"

    def test_error_marks_failed(self):
        r = summarize_events([_ev("tool_start", data={"tool_name": "网络搜索工具"}),
                              _ev("error", message="执行主智能体时发生异常：boom")])
        assert r["status"] == "failed"
        assert any("boom" in e for e in r["errors"])

    def test_cancelled(self):
        r = summarize_events([_ev("task_cancelled")])
        assert r["status"] == "cancelled"

    def test_unknown_when_no_terminal(self):
        r = summarize_events([_ev("tool_start", data={"tool_name": "x"})])
        assert r["status"] == "unknown"

    def test_malformed_events_do_not_crash(self):
        r = summarize_events([
            None,
            "not-a-dict",
            {},
            {"type": "monitor_event"},
            {"type": "pong"},
            _ev("tool_start", data=None),
            _ev("task_result", data="bad-data"),
        ])
        # 最后一条 task_result 数据畸形但事件有效，事件层判为 passed（答案为空）；
        # 空答案会在 build_case_result 层改判 failed。
        assert r["status"] == "passed"
        assert r["final_answer"] == ""

    def test_cache_hit_still_counts_as_route(self):
        # 缓存命中仍代表走了对应专家路由，应计入 web/academic
        r = summarize_events([
            _ev("tool_start", data={"tool_name": "学术检索缓存命中"}),
            _ev("tool_start", data={"tool_name": "查询缓存命中"}),
            _ev("task_result", data={"result": "x"}),
        ])
        assert r["academic_calls"] == 1 and r["web_calls"] == 1

    def test_budget_exhausted_is_not_a_retrieval(self):
        # “预算已用完/次数已达上限”是空操作提示，不计为真实检索
        r = summarize_events([
            _ev("tool_start", data={"tool_name": "学术检索次数已达上限"}),
            _ev("tool_start", data={"tool_name": "检索预算已用完，开始整理结果"}),
            _ev("task_result", data={"result": "x"}),
        ])
        assert r["academic_calls"] == 0 and r["web_calls"] == 0

    def test_result_after_cancelled_wins(self):
        r = summarize_events([_ev("task_cancelled"), _ev("task_result", data={"result": "x"})])
        assert r["status"] == "passed"


class TestBuildCaseResult:
    def test_success_requires_nonempty_answer(self):
        case = {"id": "c1", "name": "n"}
        ok = build_case_result(case, [_ev("task_result", data={"result": "有内容"})],
                               latency_seconds=1.2)
        assert ok.success and ok.status == "passed"
        empty = build_case_result(case, [_ev("task_result", data={"result": "  "})],
                                  latency_seconds=0.1)
        assert empty.success is False

    def test_timeout_overrides_nonterminal(self):
        r = build_case_result({"id": "c"}, [_ev("tool_start", data={})],
                              latency_seconds=99, timed_out=True)
        assert r.status == "timeout" and r.success is False


def _passed_case(cid="a"):
    return build_case_result({"id": cid}, [_ev("task_result", data={"result": "ok"})], 1)


class TestBuildReport:
    def test_aggregation(self):
        results = [
            _passed_case("a"),
            build_case_result({"id": "b"}, [_ev("error", message="e")], 1),
            build_case_result({"id": "c"}, [_ev("task_cancelled")], 1),
            build_case_result({"id": "d"}, [], 1, timed_out=True),
        ]
        report = build_report(results, git_commit="abc123",
                              models={"main": "m", "fast": "m"},
                              generated_at="2026-09-21T00:00:00")
        s = report["summary"]
        assert s["total"] == 4 and s["passed"] == 1 and s["failed"] == 1
        assert s["cancelled"] == 1 and s["timeout"] == 1
        assert report["git_commit"] == "abc123"
        assert len(report["cases"]) == 4
        # 存在 failed/cancelled/timeout 时整体不算成功
        assert s["success"] is False

    def test_all_passed_is_success(self):
        results = [_passed_case("a"), _passed_case("b"), _passed_case("c")]
        s = build_report(results)["summary"]
        assert s["success"] is True
        assert s["passed"] == 3 and s["total"] == 3

    @pytest.mark.parametrize(
        "results",
        [
            # 1 failed
            lambda: [_passed_case("a"),
                     build_case_result({"id": "b"}, [_ev("error", message="e")], 1)],
            # 1 cancelled
            lambda: [_passed_case("a"),
                     build_case_result({"id": "b"}, [_ev("task_cancelled")], 1)],
            # 1 timeout
            lambda: [_passed_case("a"),
                     build_case_result({"id": "b"}, [], 1, timed_out=True)],
            # 1 unknown（无终态且未超时）
            lambda: [_passed_case("a"),
                     build_case_result({"id": "b"}, [_ev("tool_start", data={})], 1)],
        ],
    )
    def test_any_non_passed_status_fails(self, results):
        s = build_report(results())["summary"]
        assert s["success"] is False
        assert s["passed"] < s["total"]

    def test_empty_case_set_is_not_success(self):
        s = build_report([])["summary"]
        assert s["total"] == 0 and s["passed"] == 0
        assert s["success"] is False


# ---------- Phase 4：确定性 expectations ----------
class TestExpectations:
    def _passed_summary(self, answer="结论", web=0, academic=0, python=0):
        return {
            "status": "passed",
            "final_answer": answer,
            "tool_calls": web + academic + python,
            "web_calls": web,
            "academic_calls": academic,
            "python_calls": python,
            "errors": [],
        }

    def test_no_expectations_passes(self):
        from benchmarks.schemas import evaluate_expectations
        assert evaluate_expectations({}, self._passed_summary()) == []

    def test_common_question_must_not_search(self):
        from benchmarks.schemas import evaluate_expectations
        # 常识题却调用了 1 次网页检索 -> max_web_calls=0 不满足
        case = {"expectations": {"max_web_calls": 0, "max_academic_calls": 0,
                                 "max_python_calls": 0}}
        failures = evaluate_expectations(case, self._passed_summary(web=1))
        assert any("max_web_calls" in f for f in failures)

    def test_web_min_max_and_source(self):
        from benchmarks.schemas import evaluate_expectations
        case = {"expectations": {"min_web_calls": 1, "max_web_calls": 3,
                                 "require_source_url": True}}
        ok = evaluate_expectations(
            case, self._passed_summary(answer="见 https://example.com/a", web=2))
        assert ok == []
        # 没有来源链接 -> 失败
        miss = evaluate_expectations(case, self._passed_summary(answer="无链接", web=2))
        assert any("来源" in f for f in miss)
        # 超过上限 -> 失败
        over = evaluate_expectations(
            case, self._passed_summary(answer="https://e.com", web=4))
        assert any("max_web_calls" in f for f in over)
        # 一次都没搜 -> min 失败
        under = evaluate_expectations(
            case, self._passed_summary(answer="https://e.com", web=0))
        assert any("min_web_calls" in f for f in under)

    def test_academic_paper_url(self):
        from benchmarks.schemas import evaluate_expectations
        case = {"expectations": {"min_academic_calls": 1, "require_paper_url": True}}
        assert evaluate_expectations(
            case, self._passed_summary(answer="arxiv http://arxiv.org/abs/1", academic=1)
        ) == []
        miss = evaluate_expectations(
            case, self._passed_summary(answer="无论文链接", academic=1))
        assert any("来源" in f for f in miss)

    def test_doi_counts_as_source(self):
        from benchmarks.schemas import evaluate_expectations
        url_case = {"expectations": {"require_source_url": True}}
        doi_case = {"expectations": {"require_paper_url": True}}
        # 只有 DOI（无 http）也算可追溯来源
        assert evaluate_expectations(
            url_case, self._passed_summary(answer="见 DOI 10.3390/s25196033", web=1)) == []
        assert evaluate_expectations(
            doi_case, self._passed_summary(answer="论文 10.1609/aaai.v40i5.37332", academic=1)
        ) == []
        # 既无 URL 也无 DOI -> 失败
        miss = evaluate_expectations(
            doi_case, self._passed_summary(answer="只有文字没有任何来源标识", academic=1))
        assert any("来源" in f for f in miss)

    def test_python_expected_contains_and_artifact(self):
        from benchmarks.schemas import evaluate_expectations
        case = {"expectations": {"min_python_calls": 1,
                                 "expected_contains": ["338350"]}}
        assert evaluate_expectations(
            case, self._passed_summary(answer="平方和是 338350", python=1)) == []
        miss = evaluate_expectations(
            case, self._passed_summary(answer="平方和是 338000", python=1))
        assert any("338350" in f for f in miss)

        art = {"expectations": {"require_artifact": True}}
        assert evaluate_expectations(art, self._passed_summary(), artifact_count=1) == []
        art_miss = evaluate_expectations(art, self._passed_summary(), artifact_count=0)
        assert any("产物" in f for f in art_miss)

    def test_numeric_expected_contains_tolerates_separators(self):
        from benchmarks.schemas import evaluate_expectations
        case = {"expectations": {"expected_contains": ["338350"]}}
        # 千分位逗号 / 空格不应让正确数值误判失败
        assert evaluate_expectations(
            case, self._passed_summary(answer="平方和 = 338,350", python=1)) == []
        assert evaluate_expectations(
            case, self._passed_summary(answer="平方和 = 338 350", python=1)) == []
        # 数值确实错误时仍判失败
        miss = evaluate_expectations(
            case, self._passed_summary(answer="平方和 = 338000", python=1))
        assert any("338350" in f for f in miss)
        # 非数字期望保持精确子串匹配
        text_case = {"expectations": {"expected_contains": ["平方和"]}}
        assert evaluate_expectations(
            text_case, self._passed_summary(answer="平方和 = 338350", python=1)) == []

    def test_expectation_failure_marks_case_failed(self):
        # 终态成功但违反 expectation -> build_case_result 必须改判 failed
        case = {"id": "c", "expectations": {"max_web_calls": 0}}
        events = [
            _ev("tool_start", data={"tool_name": "网络搜索工具"}),
            _ev("task_result", data={"result": "答了但乱搜网"}),
        ]
        r = build_case_result(case, events)
        assert r.status == "failed" and r.success is False
        assert r.expectation_failures

    def test_expectation_success_marks_case_passed(self):
        case = {"id": "c", "expectations": {"max_web_calls": 0}}
        r = build_case_result(case, [_ev("task_result", data={"result": "直接回答"})])
        assert r.status == "passed" and r.success is True
        assert r.expectation_failures == []


# ---------- 真实 HTTP + WebSocket 链路（fake agent，不调 LLM） ----------
class TestRealHttpWebSocketSmoke:
    def test_full_task_flow_over_asgi(self, monkeypatch, tmp_path):
        from starlette.testclient import TestClient

        from app.api import server
        from app.api.context import (
            reset_session_context,
            set_session_context,
            set_thread_context,
        )
        from app.api.monitor import monitor

        # 跳过真实 LLM/数据库初始化（本测试只验证 HTTP+WS+任务生命周期+事件推送）
        async def _noop_init():
            return None

        async def _fake_agent(query, session_id):
            workspace = str(server.output_dir / f"session_{session_id}")
            sd = set_session_context(workspace)
            tid = set_thread_context(session_id)
            try:
                monitor.report_session_dir(workspace)
                monitor.report_tool("网络搜索工具", {"query": query})
                monitor.report_task_result("冒烟测试最终答案")
            finally:
                reset_session_context(sd, tid)
            # 与真实契约一致：返回结果对象
            from app.agent.result import AgentRunResult
            return AgentRunResult(session_id=session_id, final_answer="冒烟测试最终答案")

        monkeypatch.setattr(server, "init_main_agent", _noop_init)
        monkeypatch.setattr(server, "close_main_agent", _noop_init)
        monkeypatch.setattr(server, "run_deep_agent", _fake_agent)

        thread_id = "smoke-http-ws-1"
        terminal = None
        with TestClient(server.app) as client:
            with client.websocket_connect(f"/ws/{thread_id}") as ws:
                resp = client.post(
                    "/api/task", json={"query": "冒烟问题", "thread_id": thread_id}
                )
                assert resp.status_code == 200
                assert resp.json()["status"] == "started"

                # 收集事件直到 task_result
                for _ in range(30):
                    msg = ws.receive_json()
                    if msg.get("event") == "task_result":
                        terminal = msg
                        break
                assert terminal is not None
                assert terminal["data"]["result"] == "冒烟测试最终答案"

        # 给后台 done_callback 一点收尾时间后，任务登记应被清理
        assert server.task_manager.get(thread_id) is None or not server.task_manager.is_active(thread_id)


# ---------- Phase 6：E2E 用例隔离（不复用历史 checkpoint） ----------
class TestCaseIsolation:
    def test_thread_id_unique_per_run(self):
        from benchmarks.e2e_client import build_thread_id
        case = {"id": "e2e_02"}
        a = build_thread_id(case, "run-aaaa")
        b = build_thread_id(case, "run-bbbb")
        assert a != b
        assert "e2e-02" in a and a.endswith("run-aaaa")

    def test_thread_ids_distinct_across_cases(self):
        from benchmarks.e2e_client import build_thread_id
        ids = {build_thread_id({"id": cid}, "run-x") for cid in
               ["e2e_01", "e2e_02", "e2e_03", "e2e_04"]}
        assert len(ids) == 4

    def test_thread_id_has_no_path_unsafe_chars(self):
        from benchmarks.e2e_client import build_thread_id
        tid = build_thread_id({"id": "e2e_01"}, "abc12345")
        # 下划线被规范化为连字符，且不含路径穿越字符
        assert "_" not in tid and ".." not in tid and "/" not in tid
