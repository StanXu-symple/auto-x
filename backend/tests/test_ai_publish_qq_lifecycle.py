"""QQ auto-publish status follows every queued message, including missing rows."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.errors import APIError
from app.api.routes.qq import clear_deliveries, delete_delivery, retry_delivery
from app.core.config import Settings
from app.db.base import Base
from app.models import (  # noqa: F401 - register tables
    AIDraft,
    AIGenerationJob,
    AIPublishDispatch,
    ArticlePublishAttempt,
    QQDelivery,
    Tweet,
)
from app.models.monitored_user import MonitoredUser
from app.services.ai_publish import AIPublishDispatcher


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


def delivery(delivery_id: int, status: str, attempt_id: str | None = "qq-attempt") -> QQDelivery:
    return QQDelivery(
        id=delivery_id,
        article_id=1 if attempt_id else None,
        article_publish_attempt_id=attempt_id,
        sequence=delivery_id - 1,
        kind="article" if attempt_id else "test",
        idempotency_key=f"qq-lifecycle:{delivery_id}",
        bot_name="bot",
        bot_app_id="app",
        bot_version=1,
        target_name="group",
        group_openid="group",
        message_body="message",
        status=status,
        attempts=1,
        max_attempts=3,
        next_attempt_at=datetime.now(UTC),
    )


async def seed_auto_publish(
    factory,
    *,
    delivery_statuses: list[str],
    expected_count: int | None = None,
    attempt_status: str = "queued",
    dispatch_status: str = "accepted",
) -> None:
    async with factory() as session, session.begin():
        session.add(MonitoredUser(id=1, username="writer"))
        session.add(
            Tweet(
                id=1,
                tweet_id="source-1",
                monitored_user_id=1,
                author_id="writer",
                text="source",
                posted_at=datetime.now(UTC),
                raw_payload={},
            )
        )
        session.add(
            AIGenerationJob(
                id=1,
                source_tweet_id=1,
                skill_ids=[],
                skill_snapshot=[],
                idempotency_key="qq-lifecycle-job",
                status="succeeded",
                provider="openai_responses",
                model_name="test-model",
            )
        )
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
        session.add(
            AIPublishDispatch(
                id=1,
                job_id=1,
                draft_id=1,
                channel="qq",
                status=dispatch_status,
                attempts=1,
                article_publish_attempt_id="qq-attempt",
                payload_snapshot={},
                next_attempt_at=datetime.now(UTC),
            )
        )
        session.add(
            ArticlePublishAttempt(
                attempt_id="qq-attempt",
                article_id=1,
                channel="qq",
                status=attempt_status,
                target_summary="group",
                delivery_count=(
                    len(delivery_statuses) if expected_count is None else expected_count
                ),
            )
        )
        session.add_all(
            [delivery(index, status) for index, status in enumerate(delivery_statuses, 1)]
        )


@pytest.mark.asyncio
async def test_reconcile_waits_for_all_groups_after_one_message_fails(db_factory) -> None:
    await seed_auto_publish(
        db_factory, delivery_statuses=["failed", "queued"], attempt_status="failed"
    )
    dispatcher = AIPublishDispatcher(Settings(_env_file=None), None, db_factory)
    await dispatcher._reconcile_qq()
    async with db_factory() as session:
        assert (await session.get(AIPublishDispatch, 1)).status == "accepted"
    async with db_factory() as session, session.begin():
        (await session.get(QQDelivery, 2)).status = "sent"
    await dispatcher._reconcile_qq()
    async with db_factory() as session:
        assert (await session.get(AIPublishDispatch, 1)).status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("statuses", [[], ["sent"]])
async def test_reconcile_finishes_incomplete_delivery_history(db_factory, statuses) -> None:
    await seed_auto_publish(db_factory, delivery_statuses=statuses, expected_count=2)
    dispatcher = AIPublishDispatcher(Settings(_env_file=None), None, db_factory)
    await dispatcher._reconcile_qq()
    async with db_factory() as session:
        dispatch = await session.get(AIPublishDispatch, 1)
        attempt = await session.get(ArticlePublishAttempt, "qq-attempt")
        assert dispatch.status == attempt.status == "failed"
        assert "投递记录不完整" in dispatch.last_error


@pytest.mark.asyncio
async def test_reconcile_recovers_from_attempt_failed_when_all_messages_sent(db_factory) -> None:
    await seed_auto_publish(
        db_factory, delivery_statuses=["sent"], attempt_status="failed"
    )
    dispatcher = AIPublishDispatcher(Settings(_env_file=None), None, db_factory)
    await dispatcher._reconcile_qq()
    async with db_factory() as session:
        assert (await session.get(AIPublishDispatch, 1)).status == "published"
        assert (await session.get(ArticlePublishAttempt, "qq-attempt")).status == "published"


@pytest.mark.asyncio
async def test_reconcile_skips_active_rows_ahead_of_batch_limit(db_factory) -> None:
    await seed_auto_publish(db_factory, delivery_statuses=["queued"])
    async with db_factory() as session, session.begin():
        for item_id in range(2, 102):
            attempt_id = f"qq-attempt-{item_id}"
            session.add(
                AIGenerationJob(
                    id=item_id,
                    source_tweet_id=1,
                    skill_ids=[],
                    skill_snapshot=[],
                    idempotency_key=f"qq-lifecycle-job-{item_id}",
                    status="succeeded",
                    provider="openai_responses",
                    model_name="test-model",
                )
            )
            session.add(
                AIDraft(
                    id=item_id,
                    job_id=item_id,
                    source_tweet_id=1,
                    title="Generated title",
                    content="Generated body",
                    images=[],
                )
            )
            session.add(
                AIPublishDispatch(
                    id=item_id,
                    job_id=item_id,
                    draft_id=item_id,
                    channel="qq",
                    status="accepted",
                    attempts=1,
                    article_publish_attempt_id=attempt_id,
                    payload_snapshot={},
                    next_attempt_at=datetime.now(UTC),
                )
            )
            session.add(
                ArticlePublishAttempt(
                    attempt_id=attempt_id,
                    article_id=item_id,
                    channel="qq",
                    status="queued",
                    target_summary="group",
                    delivery_count=1,
                )
            )
            queued = delivery(item_id, "sent" if item_id == 101 else "queued", attempt_id)
            queued.article_id = item_id
            session.add(queued)
    dispatcher = AIPublishDispatcher(Settings(_env_file=None), None, db_factory)
    await dispatcher._reconcile_qq()
    async with db_factory() as session:
        assert (await session.get(AIPublishDispatch, 1)).status == "accepted"
        assert (await session.get(AIPublishDispatch, 100)).status == "accepted"
        assert (await session.get(AIPublishDispatch, 101)).status == "published"


@pytest.mark.asyncio
async def test_delete_and_clear_reject_active_auto_delivery(db_factory) -> None:
    await seed_auto_publish(db_factory, delivery_statuses=["sent"])
    async with db_factory() as session:
        with pytest.raises(APIError) as delete_error:
            await delete_delivery(1, session, object())
        assert delete_error.value.status_code == 409
        with pytest.raises(APIError) as clear_error:
            await clear_deliveries(session, object())
        assert clear_error.value.status_code == 409
    async with db_factory() as session:
        assert await session.get(QQDelivery, 1) is not None


@pytest.mark.asyncio
async def test_clear_rejects_sending_delivery_and_preserves_rows(db_factory) -> None:
    async with db_factory() as session, session.begin():
        session.add_all([delivery(1, "sending", None), delivery(2, "sent", None)])
    async with db_factory() as session:
        with pytest.raises(APIError) as error:
            await clear_deliveries(session, object())
        assert error.value.status_code == 409
    async with db_factory() as session:
        assert len(list(await session.scalars(select(QQDelivery)))) == 2


@pytest.mark.asyncio
async def test_retry_rejects_auto_delivery_even_after_dispatch_failed(db_factory) -> None:
    await seed_auto_publish(
        db_factory, delivery_statuses=["failed"], dispatch_status="failed"
    )
    async with db_factory() as session:
        with pytest.raises(APIError) as error:
            await retry_delivery(1, session, object(), object())
        assert error.value.code == "qq_auto_delivery_retry_forbidden"
    async with db_factory() as session:
        assert (await session.get(QQDelivery, 1)).status == "failed"


@pytest.mark.asyncio
async def test_retry_rejects_auto_delivery_after_dispatch_removed(db_factory) -> None:
    await seed_auto_publish(
        db_factory, delivery_statuses=["failed"], dispatch_status="failed"
    )
    async with db_factory() as session, session.begin():
        await session.delete(await session.get(AIPublishDispatch, 1))
        (await session.get(QQDelivery, 1)).idempotency_key = "ai:1:qq:group:0"
    async with db_factory() as session:
        with pytest.raises(APIError) as error:
            await retry_delivery(1, session, object(), object())
        assert error.value.code == "qq_auto_delivery_retry_forbidden"
