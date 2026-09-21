"""
真实端到端（HTTP + WebSocket）用例执行客户端。

与离线评测的区别：这里不直接调用 run_deep_agent，而是像前端一样：
1. 先建立 WebSocket：/ws/{thread_id}（避免漏掉任务头几个事件）；
2. 再 POST /api/task 启动后台任务；
3. 持续接收 monitor_event，直到 task_result / error / task_cancelled 或超时；
4. 用 session_created 事件里的工作区路径，调 /api/files 统计产物数。

需要一个已经启动的后端（默认 http://localhost:8001），且服务端配置好 LLM 凭据。
"""

import asyncio
import json
import time
import uuid
from urllib.parse import urlparse

import httpx

from benchmarks.schemas import (
    EVENT_CANCELLED,
    EVENT_ERROR,
    EVENT_RESULT,
    _event_data,
    _event_name,
    build_case_result,
)

_TERMINAL = {EVENT_RESULT, EVENT_ERROR, EVENT_CANCELLED}


def _ws_url(base_url: str, thread_id: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}/ws/{thread_id}"


def build_thread_id(case: dict, run_id: str, thread_prefix: str = "e2e") -> str:
    """
    构造用例的 thread_id：每次运行（run_id）唯一，保证用例间 / 多次运行间会话隔离，
    不与后端历史 checkpoint 串台。
    """
    cid = str(case["id"]).replace("_", "-")
    return f"{thread_prefix}-{cid}-{run_id}"


async def run_case(
    case: dict,
    *,
    base_url: str = "http://localhost:8001",
    timeout: float = 180.0,
    thread_prefix: str = "e2e",
    run_id: "str | None" = None,
) -> object:
    """
    对单个用例走一遍真实 HTTP + WebSocket 链路，返回 CaseResult。

    任何连接/协议层异常都被收敛成 status=failed 的结果，而不是让整个 runner 崩掉。

    用例隔离：thread_id 必须每次运行都唯一。后端按 thread_id 从 SQLite checkpointer
    恢复历史会话，若复用固定 id（旧实现的 e2e-<case>），后一个用例会读到上一次运行的
    对话历史，智能体会“记得”已检索/已计算而跳过工具，导致结果不可复现。因此这里在
    case id 后追加 run_id（缺省时每次调用都生成随机后缀），保证每个用例都是全新会话。
    """
    import websockets  # 延迟导入：离线跑 pytest 时不要求该依赖在场

    suffix = run_id or uuid.uuid4().hex[:8]
    thread_id = build_thread_id(case, suffix, thread_prefix)
    events: list[dict] = []
    workspace_path = ""
    started = time.time()
    timed_out = False

    try:
        async with websockets.connect(_ws_url(base_url, thread_id)) as ws:
            # 连接建立后再启动任务，确保 session_created 等早期事件不丢
            async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as http:
                resp = await http.post(
                    "/api/task",
                    json={"query": case["query"], "thread_id": thread_id},
                )
                if resp.status_code != 200:
                    latency = time.time() - started
                    failed_result = build_case_result(
                        case,
                        [
                                {
                                    "type": "monitor_event",
                                    "event": EVENT_ERROR,
                                    "message": f"POST /api/task 返回 {resp.status_code}: {resp.text[:200]}",
                                    "data": {},
                                }
                        ],
                        latency_seconds=latency,
                    )
                    return failed_result

                deadline = started + timeout
                while True:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        timed_out = True
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 5.0))
                    except asyncio.TimeoutError:
                        # 周期性醒来检查总超时；没有新消息不代表失败
                        continue

                    try:
                        ev = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        # 非 JSON（如心跳 pong）忽略，不崩
                        continue

                    name = _event_name(ev)
                    if name == "session_created":
                        workspace_path = _event_data(ev).get("path", "") or workspace_path

                    if name:
                        events.append(ev)
                    if name in _TERMINAL:
                        break

            latency = time.time() - started

        # 统计工作区产物（best-effort，失败不影响任务成败判定）
        artifact_count = 0
        if workspace_path:
            try:
                async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as http:
                    r = await http.get(
                        "/api/files", params={"path": workspace_path}
                    )
                    if r.status_code == 200:
                        artifact_count = len(r.json().get("files", []))
            except Exception:
                artifact_count = 0

        return build_case_result(
            case,
            events,
            latency_seconds=latency,
            timed_out=timed_out,
            artifact_count=artifact_count,
        )

    except Exception as e:  # 连接失败 / WS 协议错误等
        latency = time.time() - started
        return build_case_result(
            case,
            [
                {
                    "type": "monitor_event",
                    "event": EVENT_ERROR,
                    "message": f"e2e 客户端异常: {type(e).__name__}: {str(e)[:200]}",
                    "data": {},
                }
            ],
            latency_seconds=latency,
        )


async def wait_for_server(base_url: str, timeout: float = 10.0) -> bool:
    """探测后端是否已就绪（/api/threads 可通即可）。"""
    started = time.time()
    async with httpx.AsyncClient(base_url=base_url, timeout=5.0) as http:
        while time.time() - started < timeout:
            try:
                r = await http.get("/api/threads?limit=1")
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(1.0)
    return False
