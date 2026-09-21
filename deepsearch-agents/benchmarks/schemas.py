"""
端到端 Benchmark 的事件归类与结果结构（纯逻辑，不触网，便于离线单测）。

e2e runner 通过真实 HTTP + WebSocket 收集到的 monitor_event 事件流后，
交由本模块把原始事件流归并成“每个用例一条”的客观结果：
- 是否跑到终态（task_result / error / task_cancelled）；
- 工具调用次数（网页 / 学术 / Python）；
- 最终答案、错误信息、工作区产物数。

归类器对未知事件类型、缺字段、乱序都保持健壮，不因为多一个前端新事件就崩。

状态枚举（CaseResult.status / summary 计数统一使用，禁止 completed/ok/done 等近义词混用）：
- passed     跑到 task_result 且最终答案非空；
- failed     收到 error 事件，或终态答案为空；
- cancelled  收到 task_cancelled 且没有最终结果；
- timeout    在限定时间内没有收到任何终态；
- unknown    无法归类（默认值）。
"""

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# ---- 统一用例状态枚举（Benchmark 层）----
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_TIMEOUT = "timeout"
STATUS_UNKNOWN = "unknown"

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
    # passed / failed / cancelled / timeout / unknown
    status: str = STATUS_UNKNOWN
    success: bool = False
    latency_seconds: Optional[float] = None
    tool_calls: int = 0
    web_calls: int = 0
    academic_calls: int = 0
    python_calls: int = 0
    final_answer: str = ""
    errors: list[str] = field(default_factory=list)
    expectation_failures: list[str] = field(default_factory=list)
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
    status = STATUS_UNKNOWN
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
            status = STATUS_PASSED
            final_answer = str(data.get("result") or "")
        elif name == EVENT_CANCELLED:
            # 取消后若又出现最终结果，以最终结果为准；否则标记 cancelled
            if status != STATUS_PASSED:
                status = STATUS_CANCELLED
        elif name == EVENT_ERROR:
            if status != STATUS_PASSED:
                status = STATUS_FAILED
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
    """
    结合事件归类与超时/产物信息，构造单条用例结果。

    成功（passed）必须收到 task_result 终态且最终答案非空；
    终态答案为空、error、cancel、timeout、无终态分别归为 failed/cancelled/timeout/unknown，
    success 一律 False。
    """
    summary = summarize_events(events)
    status = summary["status"]
    errors = list(summary["errors"])

    if timed_out and status != STATUS_PASSED:
        status = STATUS_TIMEOUT

    # 终态是 task_result 但答案为空，视为失败（不能把空结果当成功）
    if status == STATUS_PASSED and not summary["final_answer"].strip():
        status = STATUS_FAILED
        errors.append("收到 task_result 但最终答案为空")

    # 确定性 expectations：工具路由 / 调用预算 / 来源链接 / 产物 / 答案子串
    expectation_failures: list[str] = []
    if status == STATUS_PASSED:
        expectation_failures = evaluate_expectations(case, summary, artifact_count)
        if expectation_failures:
            status = STATUS_FAILED
            errors.extend(expectation_failures)

    return CaseResult(
        case_id=str(case.get("id", "")),
        name=str(case.get("name", "")),
        status=status,
        success=status == STATUS_PASSED,
        latency_seconds=round(latency_seconds, 2),
        tool_calls=summary["tool_calls"],
        web_calls=summary["web_calls"],
        academic_calls=summary["academic_calls"],
        python_calls=summary["python_calls"],
        final_answer=summary["final_answer"],
        errors=errors,
        expectation_failures=expectation_failures,
        artifact_count=artifact_count,
        event_count=len(events or []),
    )


def evaluate_expectations(
    case: dict[str, Any],
    summary: dict[str, Any],
    artifact_count: int = 0,
) -> list[str]:
    """
    校验 case 中声明的确定性 expectations（纯函数，离线可测）。

    支持键（均可选，只校验显式声明的项）：
    - min_web_calls / max_web_calls
    - min_academic_calls / max_academic_calls
    - min_python_calls / max_python_calls
    - require_source_url / require_paper_url：最终答案需包含 http(s) 链接
    - require_artifact：工作区至少有 1 个产物文件
    - expected_contains：最终答案需包含的子串（字符串或列表，全部命中才通过）

    :return: 未满足项的人类可读说明列表；空列表代表全部满足。
    """
    expectations = case.get("expectations") or {}
    if not isinstance(expectations, dict):
        return []

    failures: list[str] = []
    answer = summary.get("final_answer", "") or ""

    def _check_counts(prefix: str, actual: int) -> None:
        mn = expectations.get(f"min_{prefix}")
        mx = expectations.get(f"max_{prefix}")
        if mn is not None and actual < mn:
            failures.append(f"min_{prefix} 期望>={mn}，实际 {actual}")
        if mx is not None and actual > mx:
            failures.append(f"max_{prefix} 期望<={mx}，实际 {actual}")

    _check_counts("web_calls", summary.get("web_calls", 0))
    _check_counts("academic_calls", summary.get("academic_calls", 0))
    _check_counts("python_calls", summary.get("python_calls", 0))

    if expectations.get("require_source_url") or expectations.get("require_paper_url"):
        # 来源可以是可点击 URL，也可以是学术论文的标准标识 DOI（如 10.3390/s25196033）；
        # 二者都代表答案给出了可追溯来源，只认 http 会把规范的 DOI 引用误判为缺失。
        has_url = "http://" in answer or "https://" in answer
        has_doi = bool(re.search(r"10\.\d{4,9}/\S+", answer))
        if not has_url and not has_doi:
            failures.append("期望答案包含可追溯来源(URL 或 DOI)，但未检测到")

    if expectations.get("require_artifact") and artifact_count < 1:
        failures.append("期望生成至少 1 个工作区产物，实际为 0")

    expected_contains = expectations.get("expected_contains") or []
    if isinstance(expected_contains, str):
        expected_contains = [expected_contains]
    # 数字答案忽略千分位分隔符（逗号 / 空格），避免把“338,350”误判为不等于 338350；
    # 非数字期望仍按精确子串匹配。
    normalized_answer = re.sub(r"[,\s\u00a0]", "", answer)
    for needle in expected_contains:
        needle_s = str(needle)
        if needle_s.isdigit():
            if needle_s not in normalized_answer:
                failures.append(f"期望答案包含数值 {needle_s}（允许千分位分隔），但未命中")
        elif needle_s not in answer:
            failures.append(f"期望答案包含子串 {needle_s!r}，但未命中")

    return failures


def build_report(
    results: list[CaseResult],
    *,
    benchmark: str = "online-e2e-runtime",
    mode: str = "e2e",
    git_commit: str = "",
    models: Optional[dict] = None,
    metadata: Optional[dict] = None,
    generated_at: str = "",
) -> dict[str, Any]:
    """
    聚合所有用例为可落盘的 benchmark 报告 dict。

    成功判定非常严格：只有 total>0 且 passed==total（failed/cancelled/timeout/unknown
    全部为 0）时 summary.success 才为 True，runner 据此决定退出码。

    models/metadata 由 benchmarks.runtime_config.collect_metadata() 采集，记录真实
    模型、预算、Python 版本与 git SHA；本函数不接触任何密钥。
    """
    total = len(results)
    passed = sum(1 for r in results if r.status == STATUS_PASSED)
    failed = sum(1 for r in results if r.status == STATUS_FAILED)
    cancelled = sum(1 for r in results if r.status == STATUS_CANCELLED)
    timeout = sum(1 for r in results if r.status == STATUS_TIMEOUT)
    unknown = sum(1 for r in results if r.status == STATUS_UNKNOWN)
    success = total > 0 and passed == total

    return {
        "benchmark": benchmark,
        "generated_at": generated_at,
        "mode": mode,
        "git_commit": git_commit,
        "models": models or {},
        "metadata": metadata or {},
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "cancelled": cancelled,
            "timeout": timeout,
            "unknown": unknown,
            "pass_rate": round(passed / total * 100, 1) if total else 0.0,
            "success": success,
        },
        "cases": [asdict(r) for r in results],
    }
