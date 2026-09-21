"""
端到端 Benchmark 的事件归类与结果结构（纯逻辑，不触网，便于离线单测）。

e2e runner 通过真实 HTTP + WebSocket 收集到的 monitor_event 事件流后，
交由本模块把原始事件流归并成“每个用例一条”的客观结果：
- 是否跑到终态（task_result / error / task_cancelled）；
- 工具调用次数（网页 / 学术 / Python）；
- 最终答案、错误信息、工作区产物数。

归类器对未知事件类型、缺字段、乱序都保持健壮，不因为多一个前端新事件就崩。
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# 终态事件
EVENT_RESULT = "task_result"
EVENT_ERROR = "error"
EVENT_CANCELLED = "task_cancelled"

# 工具事件名（与 app/api/monitor.py 上报的中文名保持一致）
WEB_TOOL = "网络搜索工具"
ACADEMIC_TOOL = "学术文献检索"
PYTHON_TOOL = "Python代码执行工具"


@dataclass
class CaseResult:
    case_id: str
    name: str
    # completed / failed / cancelled / timeout / unknown
    status: str = "unknown"
    success: bool = False
    latency_seconds: Optional[float] = None
    tool_calls: int = 0
    web_calls: int = 0
    academic_calls: int = 0
    python_calls: int = 0
    final_answer: str = ""
    errors: list[str] = field(default_factory=list)
    artifact_count: int = 0
    event_count: int = 0


def _event_name(ev: dict[str, Any]) -> str:
    """monitor_event 的业务事件名放在 event 字段；兼容直接给业务事件的情况。"""
    if not isinstance(ev, dict):
        return ""
    if ev.get("type") == "monitor_event":
        return str(ev.get("event") or "")
    return str(ev.get("event") or ev.get("type") or "")


def _event_data(ev: dict[str, Any]) -> dict:
    if isinstance(ev, dict) and isinstance(ev.get("data"), dict):
        return ev["data"]
    return {}


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """
    把一条任务的事件流归并为客观指标（不做超时判定，超时由 runner 用 status 覆盖）。

    :return: dict，含 status / final_answer / 各类计数 / errors
    """
    status = "unknown"
    final_answer = ""
    errors: list[str] = []
    tool_calls = web_calls = academic_calls = python_calls = 0

    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        name = _event_name(ev)
        data = _event_data(ev)

        if name == "tool_start":
            tool_calls += 1
            tool_name = ""
            args = data.get("args") if isinstance(data.get("args"), dict) else {}
            if isinstance(data.get("tool_name"), str):
                tool_name = data["tool_name"]
            elif isinstance(args.get("tool_name"), str):
                tool_name = args["tool_name"]
            if tool_name == WEB_TOOL:
                web_calls += 1
            elif tool_name == ACADEMIC_TOOL:
                academic_calls += 1
            elif tool_name == PYTHON_TOOL:
                python_calls += 1
        elif name == EVENT_RESULT:
            status = "completed"
            final_answer = str(data.get("result") or "")
        elif name == EVENT_CANCELLED:
            # 取消后若又出现最终结果，以最终结果为准；否则标记 cancelled
            if status != "completed":
                status = "cancelled"
        elif name == EVENT_ERROR:
            if status != "completed":
                status = "failed"
            msg = str(ev.get("message") or data or "任务执行异常")
            errors.append(msg)

    return {
        "status": status,
        "final_answer": final_answer,
        "tool_calls": tool_calls,
        "web_calls": web_calls,
        "academic_calls": academic_calls,
        "python_calls": python_calls,
        "errors": errors,
    }


def build_case_result(
    case: dict[str, Any],
    events: list[dict[str, Any]],
    latency_seconds: float = 0.0,
    *,
    timed_out: bool = False,
    artifact_count: int = 0,
) -> CaseResult:
    """结合事件归类与超时/产物信息，构造单条用例结果。"""
    summary = summarize_events(events)
    status = summary["status"]
    if timed_out and status not in ("completed",):
        status = "timeout"

    success = status == "completed" and bool(summary["final_answer"].strip())

    return CaseResult(
        case_id=str(case.get("id", "")),
        name=str(case.get("name", "")),
        status=status,
        success=success,
        latency_seconds=round(latency_seconds, 2),
        tool_calls=summary["tool_calls"],
        web_calls=summary["web_calls"],
        academic_calls=summary["academic_calls"],
        python_calls=summary["python_calls"],
        final_answer=summary["final_answer"],
        errors=summary["errors"],
        artifact_count=artifact_count,
        event_count=len(events or []),
    )


def build_report(
    results: list[CaseResult],
    *,
    mode: str = "e2e",
    git_commit: str = "",
    model: str = "",
    config: Optional[dict] = None,
    generated_at: str = "",
) -> dict[str, Any]:
    """聚合所有用例为可落盘的 benchmark 报告 dict。"""
    total = len(results)
    passed = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if r.status == "failed")
    cancelled = sum(1 for r in results if r.status == "cancelled")
    timeout = sum(1 for r in results if r.status == "timeout")
    unknown = total - passed - failed - cancelled - timeout

    return {
        "generated_at": generated_at,
        "mode": mode,
        "git_commit": git_commit,
        "model": model,
        "config": config or {},
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "cancelled": cancelled,
            "timeout": timeout,
            "unknown": unknown,
            "pass_rate": round(passed / total * 100, 1) if total else 0.0,
        },
        "cases": [asdict(r) for r in results],
    }
