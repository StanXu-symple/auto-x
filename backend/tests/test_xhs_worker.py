import asyncio

from app.xhs_worker import RELEASE_HEARTBEAT_SCRIPT, XiaohongshuWorker


class FakeRedis:
    def __init__(self) -> None:
        self.eval_args: tuple[object, ...] | None = None

    async def eval(self, *args: object) -> int:
        self.eval_args = args
        return 1


async def test_worker_only_releases_its_own_heartbeat() -> None:
    redis = FakeRedis()
    worker = object.__new__(XiaohongshuWorker)
    worker.redis = redis  # type: ignore[assignment]
    worker.worker_id = "current-worker"

    await worker._release_heartbeat()

    assert redis.eval_args == (
        RELEASE_HEARTBEAT_SCRIPT,
        1,
        "xsentinel:xhs-worker:heartbeat",
        "current-worker",
    )
    assert "heartbeat['worker_id'] == ARGV[1]" in RELEASE_HEARTBEAT_SCRIPT


async def test_dependency_wait_stops_during_retry() -> None:
    class UnavailableRedis:
        async def ping(self) -> None:
            raise ConnectionError("redis starting")

    worker = object.__new__(XiaohongshuWorker)
    worker.redis = UnavailableRedis()  # type: ignore[assignment]
    worker.stop_event = asyncio.Event()
    worker.stop_event.set()

    assert await worker._wait_for_dependencies(attempts=2) is False
