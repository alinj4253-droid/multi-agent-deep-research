"""
Phase 1 测试：同一 thread_id 的任务生命周期串行化。

覆盖 TaskManager 的不变量：同一 thread 任意时刻最多一个活跃 Runtime；
旧任务未在 timeout 内退出时不得并发启动新任务；不同 thread 可并发。
异步测试统一用 asyncio.run（项目未安装 pytest-asyncio）。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.runtime.task_manager import TaskManager, cancel_and_wait_task


def run(coro):
    return asyncio.run(coro)


async def _quick():
    return "done"


async def _cancellable(started: asyncio.Event, cancelled: asyncio.Event):
    try:
        started.set()
        await asyncio.sleep(30)
    except asyncio.CancelledError:
        cancelled.set()
        raise


async def _slow_cancel(started: asyncio.Event):
    """收到取消后仍需约 0.3s 收尾才真正退出的任务，模拟 timeout 内退不出来。"""
    started.set()
    try:
        await asyncio.Event().wait()  # 阻塞直到被取消
    except asyncio.CancelledError:
        # 抵消可能叠加的多次取消请求，允许执行收尾等待，随后显式结束
        t = asyncio.current_task()
        while t.cancelling():
            t.uncancel()
        await asyncio.sleep(0.3)
        raise asyncio.CancelledError


def test_no_old_task_creates():
    async def case():
        mgr = TaskManager()
        task, started = await mgr.start("t1", _quick)
        assert started is True
        await asyncio.sleep(0)
        return task

    task = run(case())
    assert task.done()


def test_finished_old_task_allows_new():
    async def case():
        mgr = TaskManager()
        first, _ = await mgr.start("t1", _quick)
        await first  # 等其结束
        second, started = await mgr.start("t1", _quick)
        assert started is True
        return second

    assert run(case()).done()


def test_running_old_task_is_cancelled_before_new():
    async def case():
        mgr = TaskManager()
        started = asyncio.Event()
        cancelled = asyncio.Event()
        old, _ = await mgr.start(
            "t1", lambda: _cancellable(started, cancelled)
        )
        await started.wait()
        new, started_ok = await mgr.start("t1", _quick, cancel_timeout=2.0)
        return old, new, started_ok, cancelled

    old, new, started_ok, cancelled = run(case())
    assert started_ok is True          # 旧任务快速响应取消 -> 允许建新任务
    assert cancelled.is_set()          # 旧任务确实收到取消
    assert old.done() and old.cancelled()
    assert new is not old


def test_old_task_not_exiting_blocks_new():
    async def case():
        mgr = TaskManager()
        started = asyncio.Event()
        old, _ = await mgr.start("t1", lambda: _slow_cancel(started))
        await started.wait()
        # 旧任务取消后需要约 0.3s 才退出，timeout 仅 0.05s -> 新任务不得启动
        new, started_ok = await mgr.start("t1", _quick, cancel_timeout=0.05)
        # 等待旧任务收尾结束，避免悬挂（它最终会响应取消而终止）
        await asyncio.gather(old, return_exceptions=True)
        return old, new, started_ok

    old, new, started_ok = run(case())
    assert started_ok is False
    assert new is None
    assert old.done()                 # 旧任务随后也完成了收尾并终止，无悬挂


def test_different_threads_run_concurrently():
    async def case():
        mgr = TaskManager()
        s1, c1 = asyncio.Event(), asyncio.Event()
        s2, c2 = asyncio.Event(), asyncio.Event()
        t1, ok1 = await mgr.start("a", lambda: _cancellable(s1, c1))
        t2, ok2 = await mgr.start("b", lambda: _cancellable(s2, c2))
        await asyncio.gather(s1.wait(), s2.wait())
        active = mgr.active_count()
        # 清理两个仍在 sleep 的任务
        for t in (t1, t2):
            t.cancel()
        await asyncio.gather(t1, t2, return_exceptions=True)
        return ok1, ok2, active

    ok1, ok2, active = run(case())
    assert ok1 and ok2
    assert active == 2                # 两个不同 thread 可同时活跃


def test_finished_task_is_cleaned_up():
    async def case():
        mgr = TaskManager()
        task, _ = await mgr.start("t1", _quick)
        await task
        await asyncio.sleep(0)       # 等 done_callback 执行
        return mgr.get("t1")

    assert run(case()) is None       # 结束后登记被清理


def test_cancel_returns_states():
    async def case():
        mgr = TaskManager()
        # 无任务 -> not_found
        nf = await mgr.cancel("nobody")
        started = asyncio.Event()
        cancelled = asyncio.Event()
        task, _ = await mgr.start("t1", lambda: _cancellable(started, cancelled))
        await started.wait()
        state = await mgr.cancel("t1", timeout=1.0)
        return nf, state, task

    nf, state, task = run(case())
    assert nf == "not_found"
    assert state == "cancelled"
    assert task.cancelled()


def test_cancel_and_wait_helper_on_done_task():
    async def case():
        task = asyncio.create_task(_quick())
        await task
        return await cancel_and_wait_task(task)

    assert run(case()) is True
