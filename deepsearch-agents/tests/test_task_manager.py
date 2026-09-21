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


async def _slow_cancel_count(started: asyncio.Event, count: list):
    """同 _slow_cancel，但记录 CancelledError 被投递的次数（观察式应只投递 1 次）。"""
    started.set()
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        count.append(1)
        t = asyncio.current_task()
        while t.cancelling():
            t.uncancel()
        try:
            await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            # 若被二次取消（旧 wait_for 行为），这里会再次收到 CancelledError
            count.append(1)
            while t.cancelling():
                t.uncancel()
            await asyncio.sleep(0.05)
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


# ============================================================
# Phase 5：观察式 timeout（不二次取消）+ cancel 同锁 + 并发一致性
# ============================================================

def test_cancel_and_wait_issues_only_one_cancel():
    """超时返回 False 时只应投递一次取消（旧 asyncio.wait_for 会在超时时再 cancel 一次）。"""
    async def case():
        mgr = TaskManager()
        started = asyncio.Event()
        count: list = []
        task, _ = await mgr.start("t1", lambda: _slow_cancel_count(started, count))
        await started.wait()

        stopped = await cancel_and_wait_task(task, timeout=0.05)
        await asyncio.gather(task, return_exceptions=True)
        return stopped, count

    stopped, count = run(case())
    assert stopped is False          # 0.05s 内退不出来
    assert count == [1]              # 关键：CancelledError 只投递了一次，没有隐式第二次


def test_restart_possible_after_stuck_task_finishes():
    async def case():
        mgr = TaskManager()
        started = asyncio.Event()
        old, _ = await mgr.start("t1", lambda: _slow_cancel(started))
        await started.wait()

        _task, started_ok = await mgr.start("t1", _quick, cancel_timeout=0.02)
        assert started_ok is False   # 旧任务还在收尾，拒绝并发启动

        await asyncio.gather(old, return_exceptions=True)
        await asyncio.sleep(0)       # 等 done_callback 清理登记
        new, started_ok2 = await mgr.start("t1", _quick)
        await asyncio.gather(new, return_exceptions=True)
        return started_ok2

    assert run(case()) is True       # 旧任务结束后同 thread 可重新启动


def test_concurrent_start_and_cancel_end_consistent():
    async def case():
        mgr = TaskManager()
        started, cancelled = asyncio.Event(), asyncio.Event()
        task, _ = await mgr.start("t1", lambda: _cancellable(started, cancelled))
        await started.wait()

        async def replace():
            return await mgr.start("t1", _quick, cancel_timeout=2.0)

        async def cancel_it():
            await asyncio.sleep(0.01)
            return await mgr.cancel("t1", timeout=2.0)

        replace_result, cancel_state = await asyncio.gather(replace(), cancel_it())
        await asyncio.sleep(0.05)

        registered = mgr.get("t1")
        await asyncio.gather(task, return_exceptions=True)
        if replace_result[0] is not None:
            await asyncio.gather(replace_result[0], return_exceptions=True)
        return cancel_state, registered, mgr.active_count(), mgr.is_active("t1")

    cancel_state, registered, active_count, is_active = run(case())
    # 无论 start 与 cancel 谁先拿到锁，最终都不应残留活跃任务或幽灵登记
    assert cancel_state in ("cancelled", "cancelling", "not_found")
    assert registered is None or registered.done()
    assert active_count == 0
    assert is_active is False


def test_cancel_uses_per_thread_lock_no_cross_thread_effect():
    async def case():
        mgr = TaskManager()
        sa, ca = asyncio.Event(), asyncio.Event()
        sb, cb = asyncio.Event(), asyncio.Event()
        ta, _ = await mgr.start("a", lambda: _cancellable(sa, ca))
        tb, _ = await mgr.start("b", lambda: _cancellable(sb, cb))
        await asyncio.gather(sa.wait(), sb.wait())

        # 取消 a 不影响 b
        state_a = await mgr.cancel("a", timeout=1.0)
        await asyncio.sleep(0)
        b_active = mgr.is_active("b")
        b_got_cancel = cb.is_set()     # 在手动清理 b 之前记录它是否被误取消
        await asyncio.gather(ta, return_exceptions=True)
        tb.cancel()
        await asyncio.gather(tb, return_exceptions=True)
        return state_a, b_active, ca.is_set(), b_got_cancel

    state_a, b_active, a_cancelled, b_cancelled = run(case())
    assert state_a == "cancelled"
    assert b_active is True           # b 未被波及
    assert a_cancelled and not b_cancelled
