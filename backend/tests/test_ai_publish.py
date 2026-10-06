import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ai_worker import AIGenerationWorker
from app.control_plane.nacos import Instance
from app.core.config import Settings
from app.core.time import as_utc
from app.db.base import Base
from app.models import (  # noqa: F401 - register all metadata tables
    Admin,
    AIDraft,
    AIGenerationJob,
    AIListenTask,
    AIPublishDispatch,
    ArticlePublishAttempt,
    QQBotAccount,
    QQDelivery,
    QQJoinedGroup,
    Tweet,
    TweetScreenshot,
    XiaohongshuCredential,
)
from app.models.monitored_user import MonitoredUser
from app.services.ai_publish import (
    AIPublishDispatcher,
    AwaitingSourceScreenshot,
    retry_failed_dispatch,
)
from app.services.xhs_jobs import XHSJobNotAcceptedError, XHSWorkerUnavailableError


class FakeRedis:
    def __init__(self) -> None:
        self.queued: list[int] = []

    async def rpush(self, _key: str, *values: str) -> None:
        self.queued.extend(int(value) for value in values)


@pytest.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def job(job_id: int, channels: list[str]) -> AIGenerationJob:
    return AIGenerationJob(
        id=job_id,
        source_tweet_id=1,
        listen_task_id=1,
        skill_ids=[],
        skill_snapshot=[],
        idempotency_key=f"listen:{job_id}",
        status="succeeded",
        provider="openai_responses",
        model_name="test-model",
        task_snapshot={
            "auto_publish_channels": channels,
            "owner_admin_id": 5,
            "qq_bot_id": 3,
            "qq_group_openids": ["group-a"],
        },
    )


async def seed_base(factory, channels: list[str]) -> None:
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        session.add(Admin(id=5, username="owner", password_hash="unused"))
        session.add(AIListenTask(id=1, name="listen", desired_state="enabled", owner_admin_id=5))
        session.add(MonitoredUser(id=1, username="writer"))
        session.add(
            Tweet(
                id=1,
                tweet_id="123456789",
                monitored_user_id=1,
                author_id="writer",
                text="source",
                posted_at=now,
                raw_payload={},
            )
        )
        session.add(job(1, channels))
        session.add(
            AIDraft(
                id=1,
                job_id=1,
                source_tweet_id=1,
                title="Generated title",
                content="Generated body",
                images=[],
            )
        )


@pytest.mark.asyncio
async def test_seed_creates_each_selected_channel_once_and_freezes_payload(db_factory) -> None:
    await seed_base(db_factory, ["qq", "xhs"])
    dispatcher = AIPublishDispatcher(Settings(_env_file=None), FakeRedis(), db_factory)
    await dispatcher._seed_uninitialized()
    await dispatcher._seed_uninitialized()
    async with db_factory() as session:
        rows = list(
            await session.scalars(select(AIPublishDispatch).order_by(AIPublishDispatch.channel))
        )
        stored_job = await session.get(AIGenerationJob, 1)
    assert [row.channel for row in rows] == ["qq", "xhs"]
    assert all(row.payload_snapshot["title"] == "Generated title" for row in rows)
    assert all(row.payload_snapshot["owner_admin_id"] == 5 for row in rows)
    assert stored_job.publish_outbox_initialized_at is not None


@pytest.mark.asyncio
async def test_xhs_waits_for_pending_screenshot_then_bounds_retry(db_factory) -> None:
    await seed_base(db_factory, ["xhs"])
    dispatcher = AIPublishDispatcher(Settings(_env_file=None), FakeRedis(), db_factory)
    await dispatcher._seed_uninitialized()
    async with db_factory() as session, session.begin():
        session.add(
            XiaohongshuCredential(
                admin_id=5, encrypted_a1="unused", encrypted_web_session="unused"
            )
        )
        session.add(
            TweetScreenshot(
                tweet_id="123456789", status="pending", next_attempt_at=datetime.now(UTC)
            )
        )
    async with db_factory() as session:
        row = await session.scalar(select(AIPublishDispatch))
    with pytest.raises(AwaitingSourceScreenshot):
        await dispatcher._prepare_media(row.id, require_image=True)
    await dispatcher.process_dispatch(row.id)
    async with db_factory() as session:
        row = await session.get(AIPublishDispatch, row.id)
        assert row.status == "retry_wait"
        assert row.attempts == 1
        assert row.article_publish_attempt_id is None


@pytest.mark.asyncio
async def test_xhs_timeout_is_uncertain_and_never_requeued(db_factory, monkeypatch) -> None:
    await seed_base(db_factory, ["xhs"])
    dispatcher = AIPublishDispatcher(Settings(_env_file=None), FakeRedis(), db_factory)
    await dispatcher._seed_uninitialized()
    async with db_factory() as session:
        row = await session.scalar(select(AIPublishDispatch))
    calls = 0

    async def prepared(*_args, **_kwargs):
        return {"title": "Generated title", "content": "Generated body"}, ["/image.png"], 5

    async def timeout(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("network response lost")

    monkeypatch.setattr(dispatcher, "_prepare_media", prepared)
    monkeypatch.setattr("app.services.ai_publish.submit_xhs_job", timeout)
    await dispatcher.process_dispatch(row.id)
    await dispatcher.process_dispatch(row.id)
    async with db_factory() as session:
        row = await session.get(AIPublishDispatch, row.id)
    assert row.status == "uncertain"
    assert row.article_publish_attempt_id is not None
    assert calls == 1


@pytest.mark.asyncio
async def test_same_admin_xhs_dispatches_run_serially(db_factory, monkeypatch) -> None:
    await seed_base(db_factory, ["xhs"])
    async with db_factory() as session, session.begin():
        session.add(
            Tweet(
                id=2,
                tweet_id="123456790",
                monitored_user_id=1,
                author_id="writer",
                text="second source",
                posted_at=datetime.now(UTC),
                raw_payload={},
            )
        )
        second = job(2, ["xhs"])
        second.source_tweet_id = 2
        session.add(second)
        session.add(
            AIDraft(
                id=2,
                job_id=2,
                source_tweet_id=2,
                title="Second title",
                content="Second body",
                images=[],
            )
        )
    dispatcher = AIPublishDispatcher(Settings(_env_file=None), FakeRedis(), db_factory)
    await dispatcher._seed_uninitialized()
    async with db_factory() as session:
        ids = list(
            await session.scalars(select(AIPublishDispatch.id).order_by(AIPublishDispatch.id))
        )
    started = asyncio.Event()
    release = asyncio.Event()
    active = 0
    peak = 0
    calls = 0

    async def prepared(*_args, **_kwargs):
        return {"title": "title", "content": "body"}, ["/image.png"], 5

    async def submit(*_args, **_kwargs):
        nonlocal active, peak, calls
        calls += 1
        active += 1
        peak = max(peak, active)
        started.set()
        if calls == 1:
            await release.wait()
        active -= 1

    monkeypatch.setattr(dispatcher, "_prepare_media", prepared)
    monkeypatch.setattr("app.services.ai_publish.submit_xhs_job", submit)
    first = asyncio.create_task(dispatcher.process_dispatch(ids[0]))
    await asyncio.wait_for(started.wait(), timeout=2)
    await dispatcher.process_dispatch(ids[1])
    async with db_factory() as session:
        waiting = await session.get(AIPublishDispatch, ids[1])
        assert waiting.status == "retry_wait"
        assert waiting.attempts == 0
        assert calls == 1
    release.set()
    await first
    async with db_factory() as session, session.begin():
        waiting = await session.get(AIPublishDispatch, ids[1])
        waiting.next_attempt_at = datetime.now(UTC)
    await dispatcher.process_dispatch(ids[1])
    async with db_factory() as session:
        assert (await session.get(AIPublishDispatch, ids[0])).status == "published"
        assert (await session.get(AIPublishDispatch, ids[1])).status == "published"
    assert calls == 2
    assert peak == 1


@pytest.mark.asyncio
async def test_confirmed_not_accepted_xhs_retries_with_limit(db_factory, monkeypatch) -> None:
    await seed_base(db_factory, ["xhs"])
    dispatcher = AIPublishDispatcher(Settings(_env_file=None), FakeRedis(), db_factory)
    await dispatcher._seed_uninitialized()
    async with db_factory() as session:
        dispatch_id = await session.scalar(select(AIPublishDispatch.id))
    calls = 0

    async def prepared(*_args, **_kwargs):
        return {"title": "title", "content": "body"}, ["/image.png"], 5

    async def busy(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise XHSJobNotAcceptedError("browser capacity", retry_after_seconds=30)

    monkeypatch.setattr(dispatcher, "_prepare_media", prepared)
    monkeypatch.setattr("app.services.ai_publish.submit_xhs_job", busy)
    for attempt_number in range(1, 6):
        await dispatcher.process_dispatch(dispatch_id)
        async with db_factory() as session, session.begin():
            row = await session.get(AIPublishDispatch, dispatch_id)
            assert row.rejection_attempts == attempt_number
            assert row.article_publish_attempt_id is None
            assert row.status == ("failed" if attempt_number == 5 else "retry_wait")
            if attempt_number == 1:
                assert (as_utc(row.next_attempt_at) - datetime.now(UTC)).total_seconds() >= 25
                await dispatcher.run_once()
                assert calls == 1
            row.next_attempt_at = datetime.now(UTC)
    await dispatcher.process_dispatch(dispatch_id)
    assert calls == 5
    async with db_factory() as session:
        history = list(await session.scalars(select(ArticlePublishAttempt)))
    assert len(history) == 5
    assert all(item.status == "failed" for item in history)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "detail", "expected_retry_after"),
    [
        (409, "Account already has an active job", None),
        (429, "Browser worker at capacity", 17),
    ],
)
async def test_xhs_http_only_classifies_explicit_preacceptance_rejections(
    tmp_path, status: int, detail: str, expected_retry_after: int | None
) -> None:
    secret = tmp_path / "ai-worker.secret"
    secret.write_text("test-secret\n")
    settings = Settings(
        _env_file=None, xhs_transport="http", service_client_secret_file=str(secret)
    )
    dispatcher = AIPublishDispatcher(settings, FakeRedis(), None)
    client = await dispatcher._ensure_xhs_client()
    assert client is not None
    await client.http.aclose()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={"detail": detail},
            headers={"Retry-After": "17"} if status == 429 else {},
        )

    client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def endpoint(_admin_id: int):
        return Instance("127.0.0.1", 8006, "xhs-worker")

    async def headers():
        return {}

    client.endpoint = endpoint  # type: ignore[method-assign]
    client.headers = headers  # type: ignore[method-assign]
    try:
        with pytest.raises(XHSJobNotAcceptedError) as error:
            await client.submit(
                operation="login",
                admin_id=5,
                payload={"encrypted_a1": "a", "encrypted_web_session": "b"},
                timeout_seconds=1,
            )
        assert error.value.retry_after_seconds == expected_retry_after
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "detail"),
    [(409, "Job already accepted"), (429, "Gateway rate limit")],
)
async def test_xhs_http_other_rejection_is_not_safe_to_retry(
    tmp_path, status: int, detail: str
) -> None:
    secret = tmp_path / "ai-worker.secret"
    secret.write_text("test-secret\n")
    settings = Settings(
        _env_file=None, xhs_transport="http", service_client_secret_file=str(secret)
    )
    dispatcher = AIPublishDispatcher(settings, FakeRedis(), None)
    client = await dispatcher._ensure_xhs_client()
    assert client is not None
    await client.http.aclose()
    client.http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(status, json={"detail": detail})
        )
    )

    async def endpoint(_admin_id: int):
        return Instance("127.0.0.1", 8006, "xhs-worker")

    async def headers():
        return {}

    client.endpoint = endpoint  # type: ignore[method-assign]
    client.headers = headers  # type: ignore[method-assign]
    try:
        with pytest.raises(XHSWorkerUnavailableError):
            await client.submit(
                operation="login",
                admin_id=5,
                payload={"encrypted_a1": "a", "encrypted_web_session": "b"},
                timeout_seconds=1,
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_qq_creates_one_durable_batch_then_reconciles(db_factory) -> None:
    await seed_base(db_factory, ["qq"])
    redis = FakeRedis()
    dispatcher = AIPublishDispatcher(Settings(_env_file=None), redis, db_factory)
    await dispatcher._seed_uninitialized()
    async with db_factory() as session, session.begin():
        session.add(
            QQBotAccount(
                id=3,
                name="bot",
                app_id="app",
                encrypted_app_secret="unused",
                secret_hint="hint",
                secret_fingerprint="fingerprint",
                is_enabled=True,
            )
        )
        session.add(
            QQJoinedGroup(
                id=7,
                bot_id=3,
                app_id="app",
                group_openid="group-a",
                is_joined=True,
                last_event_at=datetime.now(UTC),
            )
        )
    # Existing QQ BigInteger IDs need explicit values under SQLite. PostgreSQL
    # supplies these IDs from its normal sequence in production.
    next_id = 0

    def give_sqlite_ids(sync_session, _flush_context, _instances):
        nonlocal next_id
        for item in sync_session.new:
            if isinstance(item, QQDelivery) and item.id is None:
                next_id += 1
                item.id = next_id

    event.listen(db_factory.class_.sync_session_class, "before_flush", give_sqlite_ids)
    try:
        async with db_factory() as session:
            row = await session.scalar(select(AIPublishDispatch))
        await dispatcher.process_dispatch(row.id)
        await dispatcher.process_dispatch(row.id)
        async with db_factory() as session:
            row = await session.get(AIPublishDispatch, row.id)
            deliveries = list(await session.scalars(select(QQDelivery)))
        assert row.status == "accepted"
        assert len(deliveries) == 1
        assert deliveries[0].idempotency_key == "ai:1:qq:group-a:0"
        assert redis.queued == [deliveries[0].id]
        async with db_factory() as session, session.begin():
            attempt = await session.get(ArticlePublishAttempt, row.article_publish_attempt_id)
            attempt.status = "published"
            delivery = await session.get(QQDelivery, deliveries[0].id)
            delivery.status = "sent"
        await dispatcher._reconcile_qq()
        async with db_factory() as session:
            assert (await session.get(AIPublishDispatch, row.id)).status == "published"
    finally:
        event.remove(db_factory.class_.sync_session_class, "before_flush", give_sqlite_ids)


@pytest.mark.asyncio
async def test_manual_retry_only_resets_preflight_failure(db_factory) -> None:
    await seed_base(db_factory, ["xhs", "qq"])
    dispatcher = AIPublishDispatcher(Settings(_env_file=None), FakeRedis(), db_factory)
    await dispatcher._seed_uninitialized()
    async with db_factory() as session, session.begin():
        rows = list(
            await session.scalars(select(AIPublishDispatch).order_by(AIPublishDispatch.channel))
        )
        safe, attempted = rows
        safe.status = "failed"
        safe.last_error = "missing configuration"
        safe.rejection_attempts = 5
        attempted.status = "failed"
        attempted.article_publish_attempt_id = "some-prior-attempt"
    async with db_factory() as session, session.begin():
        assert await retry_failed_dispatch(session, safe.id) is not None
        assert await retry_failed_dispatch(session, attempted.id) is None
    async with db_factory() as session:
        assert (await session.get(AIPublishDispatch, safe.id)).status == "pending"
        assert (await session.get(AIPublishDispatch, safe.id)).rejection_attempts == 0
        assert (await session.get(AIPublishDispatch, attempted.id)).status == "failed"


@pytest.mark.asyncio
async def test_http_transport_uses_ai_worker_identity(tmp_path, db_factory) -> None:
    secret = tmp_path / "ai-worker.secret"
    secret.write_text("test-secret\n")
    settings = Settings(
        _env_file=None,
        xhs_transport="http",
        service_client_secret_file=str(secret),
    )
    dispatcher = AIPublishDispatcher(settings, FakeRedis(), db_factory)
    client = await dispatcher._ensure_xhs_client()
    assert client is not None
    assert client.tokens.client_id == "ai-worker"
    await client.aclose()


@pytest.mark.asyncio
async def test_publish_outbox_error_does_not_fail_generated_draft(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(
                sync,
                tables=[
                    table
                    for table in Base.metadata.sorted_tables
                    if table.name != "ai_publish_dispatches"
                ],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        session.add(MonitoredUser(id=1, username="writer"))
        session.add(
            Tweet(
                id=1,
                tweet_id="123456789",
                monitored_user_id=1,
                author_id="writer",
                text="source",
                posted_at=now,
                raw_payload={},
            )
        )
        session.add(AIListenTask(id=1, name="task", desired_state="enabled"))
        session.add(
            AIGenerationJob(
                id=1,
                source_tweet_id=1,
                listen_task_id=1,
                skill_ids=[],
                skill_snapshot=[],
                idempotency_key="listen:1",
                status="running",
                claim_token="claim",
                provider="openai_responses",
                model_name="test-model",
                task_snapshot={"auto_publish_channels": ["qq"]},
            )
        )
    next_id = 0

    def give_sqlite_draft_id(sync_session, _flush_context, _instances):
        nonlocal next_id
        for item in sync_session.new:
            if isinstance(item, AIDraft) and item.id is None:
                next_id += 1
                item.id = next_id

    event.listen(factory.class_.sync_session_class, "before_flush", give_sqlite_draft_id)
    worker = object.__new__(AIGenerationWorker)

    async def valid_lock(*_args):
        return None

    worker._assert_lock = valid_lock  # type: ignore[method-assign]
    monkeypatch.setattr("app.ai_worker.AsyncSessionFactory", factory)
    try:
        assert await worker._commit_success(
            1,
            "claim",
            "lock",
            asyncio.Event(),
            {"title": "Generated title", "content": "Generated body"},
            {},
            "a" * 64,
            "b" * 64,
        )
        async with factory() as session:
            stored_job = await session.get(AIGenerationJob, 1)
            draft = await session.scalar(select(AIDraft).where(AIDraft.job_id == 1))
            assert stored_job.status == "succeeded"
            assert stored_job.publish_outbox_initialized_at is None
            assert draft is not None and draft.content == "Generated body"
    finally:
        event.remove(factory.class_.sync_session_class, "before_flush", give_sqlite_draft_id)
        await engine.dispose()
