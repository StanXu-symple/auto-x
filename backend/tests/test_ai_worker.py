import asyncio
import logging
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.ai_worker import (
    AIGenerationWorker,
    AILockLostError,
    FailureCommitResult,
)
from app.core.config import Settings
from app.models.ai import AIGenerationJob
from app.schemas.ai import GeneratedDraft
from app.services.ai_provider import AIProviderError, ProviderRequest, ProviderResult


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
        feature_code="article_generation",
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


class ProcessRedis:
    async def set(self, *_args, **_kwargs):
        return True

    async def eval(self, *_args):
        return 1


def generation_request() -> ProviderRequest:
    return ProviderRequest(
        provider="openai_responses",
        model="gpt-5.6-terra",
        base_url="https://api.openai.com/v1",
        bridge_url=None,
        prompt_template="SECRET PROMPT",
        language="zh-CN",
        tone="专业自然",
        reasoning_effort="medium",
        max_output_tokens=1200,
        timeout_seconds=30,
        skill_snapshot=[{"name": "writer", "instructions": "SECRET SKILL"}],
        feature_snapshot={"code": "article_generation"},
        author_context={"author": {"monitored_user_id": 3}},
        source={"text": "SECRET SOURCE CONTENT"},
        job_id=8,
        api_key="SECRET API KEY",
    )


def generation_result() -> ProviderResult:
    return ProviderResult(
        draft=GeneratedDraft(
            title="测试标题",
            content="测试正文",
            excerpt=None,
            metadata=None,
            author_profile={
                "identity_summary": "作者简介",
                "focus_summary": "当前关注",
                "relationship_summary": "内容联系",
                "recurring_topics": [],
                "evidence": [],
                "confidence": 0.8,
            },
        ),
        response_snapshot={"id": "resp_test"},
        prompt_hash="a" * 64,
        source_text_hash="b" * 64,
    )


def process_worker() -> AIGenerationWorker:
    worker = object.__new__(AIGenerationWorker)
    worker.settings = Settings(_env_file=None, ai_worker_lock_ttl_seconds=60)
    worker.worker_id = "ai-worker-test"
    worker.redis = ProcessRedis()  # type: ignore[assignment]

    async def renew_forever(*_args):
        await asyncio.Event().wait()

    async def assert_lock(*_args):
        return None

    worker._renew_lease = renew_forever  # type: ignore[method-assign]
    worker._assert_lock = assert_lock  # type: ignore[method-assign]
    worker._observe = lambda *_args: None  # type: ignore[method-assign]
    return worker


@pytest.mark.asyncio
async def test_successful_generation_logs_ordered_safe_stages(caplog) -> None:
    worker = process_worker()
    job = stale_job(attempts=0)
    job.status = "running"
    job.attempts = 1
    job.claim_token = "claim"
    request = generation_request()
    result = generation_result()

    async def claim(*_args):
        return job

    async def provider_request(*_args):
        return request

    async def generate(*_args, **_kwargs):
        return result

    async def commit_success(*_args, **_kwargs):
        return True

    worker._claim = claim  # type: ignore[method-assign]
    worker._provider_request = provider_request  # type: ignore[method-assign]
    worker._generate_with_lease = generate  # type: ignore[method-assign]
    worker._commit_success = commit_success  # type: ignore[method-assign]

    with caplog.at_level(logging.INFO, logger="app.ai_worker"):
        assert await worker.process_job(job.id) is True

    stages = [record.stage for record in caplog.records if hasattr(record, "stage")]
    assert stages == [
        "job_discovered",
        "lock_acquired",
        "job_claimed",
        "data_source_resolution_started",
        "data_source_resolved",
        "provider_request_prepared",
        "provider_request_started",
        "provider_response_validated",
        "draft_persistence_started",
        "draft_persisted",
        "author_profile_persisted",
        "job_completed",
    ]
    completed = next(record for record in caplog.records if record.stage == "job_completed")
    assert completed.ai_job_id == job.id
    assert completed.worker_id == "ai-worker-test"
    assert completed.provider == "openai_responses"
    assert completed.model == "gpt-5.6-terra"
    assert completed.attempt == 1
    assert completed.max_attempts == 3
    assert isinstance(completed.elapsed_ms, int)
    log_values = repr([record.__dict__ for record in caplog.records])
    assert "SECRET PROMPT" not in log_values
    assert "SECRET SKILL" not in log_values
    assert "SECRET SOURCE CONTENT" not in log_values
    assert "SECRET API KEY" not in log_values


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_stage", "retry_delay"),
    [
        ("retry_wait", "job_retry_scheduled", 12),
        ("failed", "job_failed_permanently", None),
    ],
)
async def test_generation_failure_logs_retry_or_permanent_failure(
    caplog, outcome: str, expected_stage: str, retry_delay: int | None
) -> None:
    worker = process_worker()
    job = stale_job(attempts=0)
    job.status = "running"
    job.attempts = 1
    job.claim_token = "claim"
    request = generation_request()

    async def claim(*_args):
        return job

    async def provider_request(*_args):
        return request

    async def generate(*_args, **_kwargs):
        raise AIProviderError(
            "AI provider HTTP 429: SECRET PROVIDER RESPONSE",
            retryable=True,
            status_code=429,
        )

    async def commit_failure(*_args, **_kwargs):
        return FailureCommitResult(
            outcome=outcome,
            attempt=1,
            max_attempts=3,
            retry_delay_seconds=retry_delay,
            next_attempt_at=(datetime.now(UTC) + timedelta(seconds=12))
            if retry_delay
            else None,
        )

    worker._claim = claim  # type: ignore[method-assign]
    worker._provider_request = provider_request  # type: ignore[method-assign]
    worker._generate_with_lease = generate  # type: ignore[method-assign]
    worker._commit_failure = commit_failure  # type: ignore[method-assign]

    with caplog.at_level(logging.INFO, logger="app.ai_worker"):
        assert await worker.process_job(job.id) is False

    failure = next(record for record in caplog.records if record.stage == expected_stage)
    assert failure.outcome == outcome
    assert failure.status_code == 429
    assert failure.retry_delay_seconds == retry_delay
    assert failure.error_summary == "AI provider HTTP 429"
    assert "SECRET PROVIDER RESPONSE" not in caplog.text


@pytest.mark.asyncio
async def test_long_provider_request_logs_progress_and_cleans_up(monkeypatch, caplog) -> None:
    worker = process_worker()
    finished = asyncio.Event()
    provider_cancelled = asyncio.Event()

    async def generate(_request):
        try:
            await finished.wait()
            return SimpleNamespace(value="done")
        except asyncio.CancelledError:
            provider_cancelled.set()
            raise

    worker.provider = SimpleNamespace(generate=generate)
    monkeypatch.setattr("app.ai_worker.AI_PROVIDER_PROGRESS_INTERVAL_SECONDS", 0.01)
    context = {
        "ai_job_id": 8,
        "provider": "openai_responses",
        "model": "gpt-5.6-terra",
        "attempt": 1,
        "max_attempts": 3,
    }

    with caplog.at_level(logging.INFO, logger="app.ai_worker"):
        task = asyncio.create_task(
            worker._generate_with_lease(
                generation_request(),
                asyncio.Event(),
                started_perf=asyncio.get_running_loop().time(),
                job_context=context,
            )
        )
        await asyncio.sleep(0.025)
        finished.set()
        result = await task

    assert result.value == "done"
    progress = [
        record for record in caplog.records if record.stage == "provider_request_in_progress"
    ]
    assert progress
    assert progress[0].provider_elapsed_ms >= 0
    assert progress[0].request_timeout_seconds == 30
    assert not provider_cancelled.is_set()
