import asyncio

from app.worker import PollingWorker


async def test_stop_event_cancels_inflight_poll_tasks() -> None:
    worker = object.__new__(PollingWorker)
    worker.stop_event = asyncio.Event()
    cancelled = asyncio.Event()

    async def long_poll() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(long_poll())
    await asyncio.sleep(0)
    worker.stop_event.set()
    await worker._wait_for_poll_tasks([task])
    assert cancelled.is_set()
    assert task.cancelled()
