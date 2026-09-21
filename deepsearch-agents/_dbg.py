import asyncio
from app.runtime.task_manager import TaskManager


async def _uncancellable(stop):
    task = asyncio.current_task()
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.02)
        except asyncio.CancelledError:
            task.uncancel()
        except asyncio.TimeoutError:
            pass
    return "stopped"


async def main():
    mgr = TaskManager()
    stop = asyncio.Event()
    old, _ = await mgr.start("t1", lambda: _uncancellable(stop))
    await asyncio.sleep(0.05)
    new, ok = await mgr.start("t1", lambda: asyncio.sleep(0, result="x"), cancel_timeout=0.02)
    print("started_ok:", ok, "new:", new)
    stop.set()
    try:
        r = await asyncio.wait_for(old, timeout=1.0)
        print("old finished:", r, "done:", old.done())
    except Exception as e:
        print("cleanup err:", type(e).__name__, "cancelling:", old.cancelling())


asyncio.run(asyncio.wait_for(main(), timeout=5))
print("EVENT LOOP CLOSED OK")
