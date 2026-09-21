"""
后台 Agent 任务生命周期管理。

核心不变量：**同一个 thread_id 在任意时刻最多只有一个活跃 Agent Runtime。**

旧实现里 /api/task 对旧任务只调用 task.cancel() 就立刻 create_task 启动新任务，
而 cancel() 只是"请求取消"，旧任务可能仍在跑 Python 子进程 / 同步检索 / 写文件，
于是同一 session 目录、SQLite checkpoint、预算与监控会被两个 Runtime 并发踩踏。

本模块把"取消并等待旧任务真正结束"抽成统一语义，/api/task 与 /api/cancel 共用
**同一把 per-thread 锁**，使 start / cancel / replace 串行进入同一会话的生命周期管理：

- 旧任务在 timeout 内确认结束 -> 移除旧登记，再启动新任务；
- 旧任务在 timeout 内无法退出   -> 不启动新任务，交由上层返回 409 / cancelling。

注意锁内只做"读活跃任务 / cancel / wait / 登记或删除"这类毫秒级操作，
绝不在锁内执行 Agent、检索或等待整个任务跑完（否则锁会被持有数分钟）。
"""

import asyncio
from typing import Awaitable, Callable, Optional

# 等待旧任务退出的默认上限（秒）。超过即认为仍在关闭中，拒绝并发启动新任务。
DEFAULT_CANCEL_TIMEOUT = 2.0


def _consume_task_exception(task: asyncio.Task) -> None:
    """
    消费已结束任务的结果/异常，避免后台任务以异常或取消结束时 asyncio 打印
    "Task exception was never retrieved"（错误事件已由 monitor 推送给前端）。
    """
    if not task.done() or task.cancelled():
        return
    try:
        task.exception()
    except BaseException:
        # 仅用于标记异常已被取回，不需要在这里处理
        pass


async def cancel_and_wait_task(task: asyncio.Task, timeout: float = DEFAULT_CANCEL_TIMEOUT) -> bool:
    """
    请求取消 task 并**观察**它是否在 timeout 内真正结束。

    语义（与 asyncio.wait_for 不同）：
    - 只在开始时发送**一次** task.cancel()；
    - 用 asyncio.wait 观察最多 timeout 秒，超时直接返回 False，
      不会像 wait_for 那样在超时时再次 cancel 被等待对象；
    - 在 timeout 内结束（正常结束 / 被取消 / 抛异常结束）返回 True。

    asyncio Task 无法像 OS 进程那样被强杀；"退不出来"通常是卡在不可中断的同步调用，
    此时调用方不应在同一 thread 上启动新任务，而应返回 cancelling / 409。

    :return: True 表示任务生命周期已经结束；False 表示 timeout 内仍未退出。
    """
    if task.done():
        _consume_task_exception(task)
        return True

    task.cancel()  # 仅发送一次取消请求
    done, _pending = await asyncio.wait({task}, timeout=timeout)

    if task in done:
        _consume_task_exception(task)
        return True
    # 超时：不再次 cancel，保留任务继续收尾；调用方据此拒绝并发启动新任务
    return False


class TaskManager:
    """
    按 thread_id 登记并串行化后台任务替换。

    每个 thread 一把锁，start() 与 cancel() 都在同一把锁内完成
    "读活跃任务 -> cancel/wait -> 登记/删除"，保证并发的 /api/task 与 /api/cancel
    不会产生顺序不明的生命周期交错或幽灵登记。

    `_locks` 与 `_tasks` 都是按 thread_id 增长的 dict。对单机 demo / 简历项目而言
    会话规模有限，保留这些锁不做激进回收（贸然 del 可能命中正准备获取锁的协程）；
    若未来需要支撑海量 thread，应在"无活跃任务 + 锁未被持有 + 无 pending 生命周期
    操作"三者同时满足时再安全清理。
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        # 每个 thread 一把锁，保证"取消旧任务 -> 等待 -> 建新任务/取消"整体原子
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, thread_id: str) -> Optional[asyncio.Task]:
        return self._tasks.get(thread_id)

    def is_active(self, thread_id: str) -> bool:
        task = self._tasks.get(thread_id)
        return task is not None and not task.done()

    def _forget(self, thread_id: str, task: asyncio.Task) -> None:
        """done_callback：仅当登记的仍是自己时才移除，避免误删后启动的新任务。"""
        _consume_task_exception(task)
        if self._tasks.get(thread_id) is task:
            self._tasks.pop(thread_id, None)

    def _lock_for(self, thread_id: str) -> asyncio.Lock:
        return self._locks.setdefault(thread_id, asyncio.Lock())

    async def start(
        self,
        thread_id: str,
        coro_factory: Callable[[], Awaitable[object]],
        *,
        cancel_timeout: float = DEFAULT_CANCEL_TIMEOUT,
    ) -> tuple[Optional[asyncio.Task], bool]:
        """
        在指定 thread 上启动任务；若已有活跃任务，先取消并等待其结束。

        :param coro_factory: 无参可调用，调用后返回要执行的 coroutine（延迟创建，
                             只有确认旧任务结束后才真正构造新协程）。
        :return: (task, started)。started=False 表示旧任务仍在关闭，新任务未启动，
                 调用方应返回 409 / cancelling，而不是并发启动第二个 Runtime。
        """
        async with self._lock_for(thread_id):
            old = self._tasks.get(thread_id)
            if old is not None and not old.done():
                stopped = await cancel_and_wait_task(old, cancel_timeout)
                if not stopped:
                    return None, False
                # 旧任务已结束，清掉可能残留的登记
                if self._tasks.get(thread_id) is old:
                    self._tasks.pop(thread_id, None)

            task = asyncio.create_task(coro_factory())
            self._tasks[thread_id] = task
            task.add_done_callback(lambda finished: self._forget(thread_id, finished))
            return task, True

    async def cancel(self, thread_id: str, timeout: float = 1.0) -> str:
        """
        取消指定 thread 的活跃任务（与 start 使用同一把 per-thread 锁）。

        :return: "not_found"（无任务/已结束）、"cancelled"（已确认结束）、
                 "cancelling"（已请求取消但 timeout 内未退出）。
        """
        async with self._lock_for(thread_id):
            task = self._tasks.get(thread_id)
            if task is None or task.done():
                self._tasks.pop(thread_id, None)
                return "not_found"

            stopped = await cancel_and_wait_task(task, timeout)
            if not stopped:
                # 已发过一次 cancel，任务仍在收尾；不并发处理，交由调用方提示
                return "cancelling"

            if self._tasks.get(thread_id) is task:
                self._tasks.pop(thread_id, None)
            return "cancelled"

    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done())
