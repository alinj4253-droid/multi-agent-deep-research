"""
Agent 单次任务执行结果。

run_deep_agent 的执行契约：
- 实时过程事件仍通过 monitor / WebSocket 推送；
- 最终结果通过返回值 AgentRunResult 交给调用方（API 后台任务 / 测试 / E2E）；
- 普通失败：先 emit error，再向上抛出异常（后台 Task 因此可被判定为失败）；
- 取消：先 emit cancelled，再向上抛出 asyncio.CancelledError。
失败不构造 status="failed" 的"正常返回值"，避免上层误判任务成功。
"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class AgentRunResult:
    session_id: str
    final_answer: str
    status: Literal["completed"] = "completed"
    artifacts: list[str] = field(default_factory=list)
