"""
Phase 2 测试：Python 执行预算统一为 per-task（每轮）语义

验证：
- 单轮内达到 12 次后第 13 次被硬限制；
- reset 后同一 thread 新一轮可继续执行；
- 不同 thread 互不影响；reset 不会误清另一个 thread。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.api.context import (
    reset_session_context,
    set_session_context,
    set_thread_context,
)
from app.tools import python_exec_tool as pet


@pytest.fixture
def session_env(tmp_path):
    workdir = tmp_path / "session_budget"
    workdir.mkdir()
    sd = set_session_context(str(workdir))
    sid = set_thread_context("budget_thread")
    yield workdir
    reset_session_context(sd, sid)
    pet.reset_session_call_count()  # 清理全局计数，避免污染其他用例


def run_async(coro):
    return asyncio.run(coro)


def test_thirteenth_call_blocked(session_env):
    pet._session_call_counts["budget_thread"] = pet.MAX_CALLS_PER_SESSION
    result = run_async(
        pet.execute_python_code.ainvoke(
            {"code": "print(1)", "description": "should be blocked"}
        )
    )
    assert "已达工具调用上限" in result


def test_reset_allows_new_task(session_env):
    pet._session_call_counts["budget_thread"] = pet.MAX_CALLS_PER_SESSION
    # 新一轮任务开始时 reset
    pet.reset_session_call_count("budget_thread")
    result = run_async(
        pet.execute_python_code.ainvoke(
            {"code": "print(2 + 3)", "description": "fresh task"}
        )
    )
    assert "5" in result
    assert "已达工具调用上限" not in result


def test_other_thread_not_affected_by_reset(session_env):
    pet._session_call_counts["budget_thread"] = pet.MAX_CALLS_PER_SESSION
    pet._session_call_counts["other_thread"] = 5
    pet.reset_session_call_count("budget_thread")
    assert pet._session_call_counts.get("other_thread") == 5
    result = run_async(
        pet.execute_python_code.ainvoke(
            {"code": "print('ok')", "description": "after reset"}
        )
    )
    assert "ok" in result


def test_module_caps_constant():
    assert pet.MAX_CALLS_PER_SESSION == 12
