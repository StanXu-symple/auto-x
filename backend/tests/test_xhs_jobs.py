import json

import pytest

from app.services.xhs_jobs import (
    XHSJobFailedError,
    XHSWorkerUnavailableError,
    submit_xhs_job,
    xhs_response_key,
)


class FakeRedis:
    def __init__(
        self, *, online: bool = True, result: dict | None = None, restart: bool = False
    ) -> None:
        self.online = online
        self.result = result
        self.restart = restart
        self.heartbeat_reads = 0
        self.values: dict[str, str] = {}
        self.jobs: list[dict] = []

    async def get(self, key: str):
        if key == "xsentinel:xhs-worker:heartbeat":
            if not self.online:
                return None
            self.heartbeat_reads += 1
            worker_id = (
                "replacement-worker"
                if self.restart and self.heartbeat_reads > 1
                else "test-worker"
            )
            return json.dumps({"installed": True, "worker_id": worker_id})
        return self.values.get(key)

    async def ttl(self, _key: str) -> int:
        return 30

    async def rpush(self, _key: str, raw: str) -> None:
        job = json.loads(raw)
        self.jobs.append(job)
        if self.result is not None:
            self.values[xhs_response_key(job["job_id"])] = json.dumps(self.result)

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)


async def test_submit_xhs_job_returns_worker_result() -> None:
    redis = FakeRedis(result={"ok": True, "data": {"message": "done"}})

    result = await submit_xhs_job(
        redis, operation="post", admin_id=7, payload={"title": "test"}, timeout_seconds=1
    )

    assert result == {"message": "done"}
    assert redis.jobs[0]["admin_id"] == 7
    assert "a1" not in json.dumps(redis.jobs[0])


async def test_submit_xhs_job_rejects_offline_worker() -> None:
    with pytest.raises(XHSWorkerUnavailableError):
        await submit_xhs_job(
            FakeRedis(online=False),
            operation="post",
            admin_id=7,
            payload={},
            timeout_seconds=1,
        )


async def test_submit_xhs_job_surfaces_worker_failure() -> None:
    redis = FakeRedis(result={"ok": False, "error": "browser exited"})

    with pytest.raises(XHSJobFailedError, match="browser exited"):
        await submit_xhs_job(
            redis, operation="post", admin_id=7, payload={}, timeout_seconds=1
        )


async def test_submit_xhs_job_detects_worker_restart() -> None:
    with pytest.raises(XHSJobFailedError, match="退出或重启"):
        await submit_xhs_job(
            FakeRedis(restart=True),
            operation="post",
            admin_id=7,
            payload={},
            timeout_seconds=2,
        )
