import asyncio
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta

import pytest

from app.ai_worker import AIGenerationWorker, AILockLostError
from app.core.config import Settings
from app.models.ai import AIGenerationJob


class AsyncContext(AbstractAsyncContextManager):
    def __init__(self, value=None):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return None


class ClaimSession:
    def __init__(self, job):
        self.job = job
        self.expunge_called = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def begin(self):
        return AsyncContext()

    async def get(self, *_args, **_kwargs):
        return self.job

    async def flush(self):
        return None

    def expunge(self, _job):
        self.expunge_called = True


def stale_job(*, attempts: int = 1, max_attempts: int = 3) -> AIGenerationJob:
    now = datetime.now(UTC)
    return AIGenerationJob(
        id=8,
        source_tweet_id=1,
        skill_ids=[],
        skill_snapshot=[],
        idempotency_key="auto:1",
        status="running",
        provider="openai_responses",
        model_name="gpt-5.6-terra",
        attempts=attempts,
        max_attempts=max_attempts,
        next_attempt_at=now - timedelta(minutes=1),
        lease_expires_at=now - timedelta(seconds=1),
        request_snapshot={"config": {}, "source": {}},
    )


@pytest.mark.asyncio
async def test_stale_running_job_is_reclaimed_with_new_fencing_token(monkeypatch) -> None:
    job = stale_job()
    session = ClaimSession(job)
    monkeypatch.setattr("app.ai_worker.AsyncSessionFactory", lambda: session)
    worker = object.__new__(AIGenerationWorker)
    worker.settings = Settings(_env_file=None, ai_worker_lock_ttl_seconds=60)
    worker.worker_id = "worker-new"

    claimed = await worker._claim(job.id, "new-claim-token")
    assert claimed is job
    assert job.status == "running"
    assert job.attempts == 2
    assert job.claim_token == "new-claim-token"
    assert job.claimed_by == "worker-new"
    assert job.lease_expires_at > datetime.now(UTC)
    assert session.expunge_called


@pytest.mark.asyncio
async def test_stale_job_at_attempt_limit_becomes_failed(monkeypatch) -> None:
    job = stale_job(attempts=3, max_attempts=3)
    session = ClaimSession(job)
    monkeypatch.setattr("app.ai_worker.AsyncSessionFactory", lambda: session)
    worker = object.__new__(AIGenerationWorker)
    worker.settings = Settings(_env_file=None, ai_worker_lock_ttl_seconds=60)
    worker.worker_id = "worker-new"

    assert await worker._claim(job.id, "new-token") is None
    assert job.status == "failed"
    assert job.claim_token is None
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_ai_worker_stop_cancels_inflight_generation() -> None:
    worker = object.__new__(AIGenerationWorker)
    worker.stop_event = asyncio.Event()
    cancelled = asyncio.Event()

    async def generation() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(generation())
    await asyncio.sleep(0)
    worker.stop_event.set()
    await worker._wait_for_tasks([task])
    assert cancelled.is_set()
    assert task.cancelled()


@pytest.mark.asyncio
async def test_ai_worker_lease_check_fails_closed() -> None:
    class LostRedis:
        async def eval(self, *_args):
            return 0

    worker = object.__new__(AIGenerationWorker)
    worker.redis = LostRedis()
    worker.settings = Settings(_env_file=None, ai_worker_lock_ttl_seconds=60)
    lost = asyncio.Event()
    with pytest.raises(AILockLostError):
        await worker._assert_lock("lock", "token", lost)
    assert lost.is_set()
