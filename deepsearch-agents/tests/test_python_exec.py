"""
Phase 2 测试：受控 Python 子进程执行器的生命周期。

覆盖：
- 正常执行返回输出；
- 超时终止子进程；
- Agent 任务取消时显式 kill 子进程、CancelledError 向上传播、临时脚本被删除；
- 两次执行使用不同临时脚本；
- 子进程报错时临时脚本仍被清理。
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
def env(tmp_path):
    work = tmp_path / "session_py"
    work.mkdir()
    sd = set_session_context(str(work))
    sid = set_thread_context("py_thread")
    yield work
    reset_session_context(sd, sid)
    pet.reset_session_call_count()


def _temp_files(work: Path):
    return list(work.glob("_exec_*.py")) + list(work.glob("_temp_exec.py"))


def test_normal_execution(env):
    result = asyncio.run(
        pet.execute_python_code.ainvoke({"code": "print('hello', 2+2)", "description": "t"})
    )
    assert "hello 4" in result
    assert _temp_files(env) == []  # 临时脚本已清理


def test_timeout_kills_subprocess(env, monkeypatch):
    captured = {}
    orig = asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        proc = await orig(*args, **kwargs)
        captured["proc"] = proc
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    monkeypatch.setattr(pet, "EXEC_TIMEOUT_SECONDS", 0.5)

    result = asyncio.run(
        pet.execute_python_code.ainvoke(
            {"code": "import time; time.sleep(30)", "description": "long"}
        )
    )
    assert "超时" in result
    assert captured["proc"].returncode is not None  # 子进程已被终止回收
    assert _temp_files(env) == []


def test_cancellation_kills_subprocess_and_propagates(env, monkeypatch):
    captured = {}
    orig = asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        proc = await orig(*args, **kwargs)
        captured["proc"] = proc
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)

    async def scenario():
        task = asyncio.create_task(
            pet.execute_python_code.ainvoke(
                {"code": "import time; time.sleep(30)", "description": "cancel-me"}
            )
        )
        # 等子进程真正启动
        await asyncio.sleep(0.8)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return task

    task = asyncio.run(scenario())
    assert task.cancelled() or task.exception() is None
    # 子进程必须已被显式 kill（returncode 不为 None）
    assert captured["proc"].returncode is not None
    # 临时脚本必须在 finally 中删除
    assert _temp_files(env) == []


def test_distinct_temp_files_across_calls(env, monkeypatch):
    paths = []
    orig = asyncio.create_subprocess_exec

    async def spy(executable, script, *args, **kwargs):
        paths.append(script)
        return await orig(executable, script, *args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)

    async def run_both():
        await pet.execute_python_code.ainvoke({"code": "print(1)", "description": "a"})
        await pet.execute_python_code.ainvoke({"code": "print(2)", "description": "b"})

    asyncio.run(run_both())
    assert len(paths) == 2
    assert paths[0] != paths[1]
    assert Path(paths[0]).name.startswith("_exec_")
    assert _temp_files(env) == []


def test_subprocess_error_still_cleans_temp(env):
    result = asyncio.run(
        pet.execute_python_code.ainvoke({"code": "raise RuntimeError('boom')", "description": "err"})
    )
    assert "代码执行出错" in result or "RuntimeError" in result or "boom" in result
    assert _temp_files(env) == []
