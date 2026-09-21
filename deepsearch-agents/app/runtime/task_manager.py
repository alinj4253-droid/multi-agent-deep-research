"""
后台 Agent 任务生命周期管理。

核心不变量：**同一个 thread_id 在任意时刻最多只有一个活跃 Agent Runtime。**

旧实现里 /api/task 对旧任务只调用 task.cancel() 就立刻 create_task 启动新任务，
而 cancel() 只是"请求取消"，旧任务可能仍在跑 Python 子进程 / 同步检索 / 写文件，
于是同一 session 目录、SQLite checkpoint、预算与监控会被两个 Runtime 并发踩踏。

本模块把"取消并等待旧任务真正结束"抽成统一语义，/api/task 与 /api/cancel 共用：

- 旧任务在 timeout 内确认结束 -> 移除旧登记，再启动新任务；
- 旧任务在 timeout 内无法退出   -> 不启动新任务，交由上层返回 409 / cancelling。
"""

import asyncio
from typing import Awaitable, Callable, Optional

# 等待旧任务退出的默认上限（秒）。超过即认为仍在关闭中，拒绝并发启动新任务。
DEFAULT_CANCEL_TIMEOUT = 2.0


async def cancel_and_wait_task(task: asyncio.Task, timeout: float = DEFAULT_CANCEL_TIMEOUT) -> bool:
    """
    请求取消 task 并等待其真正结束。

    :return: True 表示任务生命周期已经结束（正常结束 / 被取消 / 抛异常结束）；
             False 表示在 timeout 内仍未退出，调用方不应在同一 thread 上启动新任务。
    """
    if task.done():
        return True

    task.cancel()
    try:
        # shield 不是必须的（外层没有取消这里），直接 wait_for 即可：
        # 被 cancel 的 task 会抛 CancelledError，wait_for 把它向上传递。
        await asyncio.wait_for(task, timeout=timeout)
    except asyncio.CancelledError:
        # 旧任务响应了取消，生命周期已结束
        return True
    except asyncio.TimeoutError:
        # 旧任务在 timeout 内没有退出（可能卡在不可中断的同步调用）
        return False
    except Exception:
        # 旧任务以异常结束，生命周期同样已经结束
        return True

    return task.done()


class TaskManager:
    """按 thread_id 登记并串行化后台任务替换。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        # 每个 thread 一把锁，保证"取消旧任务 -> 等待 -> 建新任务"整体原子
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, thread_id: str) -> Optional[asyncio.Task]:
        return self._tasks.get(thread_id)

    def is_active(self, thread_id: str) -> bool:
        task = self._tasks.get(thread_id)
        return task is not None and not task.done()

    def _forget(self, thread_id: str, task: asyncio.Task) -> None:
        """done_callback：仅当登记的仍是自己时才移除，避免误删后启动的新任务。"""
        # 消费异常，避免后台任务以异常/取消结束时 asyncio 打印
        # "Task exception was never retrieved"（错误事件已由 monitor 推送）。
        if not task.cancelled():
            try:
                task.exception()
            except (asyncio.CancelledError, Exception):
                pass
        if self._tasks.get(thread_id) is task:
            self._tasks.pop(thread_id, None)

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
        lock = self._locks.setdefault(thread_id, asyncio.Lock())
        async with lock:
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
        取消指定 thread 的活跃任务。

        :return: "not_found"（无任务/已结束）、"cancelled"（已确认结束）、
                 "cancelling"（已请求取消但 timeout 内未退出）。
        """
        task = self._tasks.get(thread_id)
        if task is None or task.done():
            self._tasks.pop(thread_id, None)
            return "not_found"

        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except asyncio.CancelledError:
            self._forget(thread_id, task)
            return "cancelled"
        except asyncio.TimeoutError:
            return "cancelling"
        except Exception:
            self._forget(thread_id, task)
            return "cancelled"

        self._forget(thread_id, task)
        return "cancelled"

    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done())
