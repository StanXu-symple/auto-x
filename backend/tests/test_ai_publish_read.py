from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.api.errors import APIError
from app.api.routes.ai import get_ai_job, list_ai_jobs, retry_ai_publish_dispatch
from app.models.ai import AIDraft, AIGenerationJob
from app.models.ai_publish import AIPublishDispatch


def _job(now: datetime) -> AIGenerationJob:
    return AIGenerationJob(
        id=7,
        source_tweet_id=11,
        listen_task_id=3,
        trigger_type="listen_task",
        task_config_version=1,
        lifetime_attempts=1,
        is_archived=False,
        feature_code="article_generation",
        skill_ids=[2],
        skill_snapshot=[],
        idempotency_key="listen:3:tweet:11",
        status="succeeded",
        provider="openai_responses",
        model_name="test-model",
        attempts=1,
        max_attempts=3,
        next_attempt_at=now,
        manual=False,
        created_at=now,
        updated_at=now,
    )


def _dispatch(now: datetime) -> AIPublishDispatch:
    return AIPublishDispatch(
        id=15,
        job_id=7,
        draft_id=20,
        channel="qq",
        status="accepted",
        attempts=1,
        article_publish_attempt_id="attempt-1",
        next_attempt_at=now,
        created_at=now,
        updated_at=now,
    )


async def test_ai_job_list_and_detail_project_independent_publish_status() -> None:
    now = datetime.now(UTC)
    job = _job(now)
    dispatch = _dispatch(now)
    result = MagicMock()
    result.all.return_value = [(job, "x11", "source", "alice")]
    result.one_or_none.return_value = (job, "x11", "source", "alice")
    session = AsyncMock()
    session.scalar.return_value = 1
    session.execute.return_value = result
    session.scalars.return_value = [dispatch]

    page = await list_ai_jobs(
        session,
        None,
        page=1,
        page_size=20,
        job_status=None,
        provider=None,
        manual=None,
        source_tweet_id=None,
        listen_task_id=None,
        trigger_type=None,
    )
    assert page.total == 1
    assert page.items[0].auto_publish_dispatches[0].status == "accepted"
    assert page.items[0].auto_publish_dispatches[0].article_publish_attempt_id == "attempt-1"
    assert session.scalars.await_count == 1
    dispatch_query = session.scalars.await_args.args[0]
    params = dispatch_query.compile(dialect=postgresql.dialect()).params
    assert params["job_id_1"] == [7]

    detail = await get_ai_job(7, session, None)
    assert detail.auto_publish_dispatches[0].channel == "qq"
    assert detail.auto_publish_dispatches[0].status == "accepted"


async def test_retry_auto_publish_requires_owner_and_preflight_failure() -> None:
    now = datetime.now(UTC)
    dispatch = _dispatch(now)
    dispatch.status = "failed"
    dispatch.payload_snapshot = {"owner_admin_id": 9, "draft_revision": 1}
    session = AsyncMock()
    session.get.return_value = dispatch
    draft = AIDraft(id=20, article_source="ai", title="自动文章", content="正文", revision=1)
    session.scalar.return_value = draft

    with pytest.raises(APIError) as forbidden:
        await retry_ai_publish_dispatch(15, session, SimpleNamespace(id=8))
    assert forbidden.value.status_code == 403
    session.commit.assert_not_awaited()

    with pytest.raises(APIError) as unsafe:
        await retry_ai_publish_dispatch(15, session, SimpleNamespace(id=9))
    assert unsafe.value.code == "ai_publish_dispatch_not_retryable"
    session.commit.assert_not_awaited()

    dispatch.article_publish_attempt_id = None
    draft.publish_channel = "qq"
    draft.publish_attempt_id = "manual-1"
    with pytest.raises(APIError) as manual_attempt:
        await retry_ai_publish_dispatch(15, session, SimpleNamespace(id=9))
    assert manual_attempt.value.code == "ai_publish_dispatch_manual_attempt_exists"
    session.commit.assert_not_awaited()

    draft.publish_attempt_id = None
    retried = await retry_ai_publish_dispatch(15, session, SimpleNamespace(id=9))
    assert retried.status == "pending"
    assert retried.attempts == 1
    session.commit.assert_awaited_once()


async def test_retry_auto_publish_rejects_edited_draft_before_resetting_dispatch() -> None:
    now = datetime.now(UTC)
    dispatch = _dispatch(now)
    dispatch.status = "failed"
    dispatch.article_publish_attempt_id = None
    dispatch.payload_snapshot = {"owner_admin_id": 9, "draft_revision": 1}
    session = AsyncMock()
    session.get.return_value = dispatch
    session.scalar.return_value = AIDraft(
        id=20, article_source="ai", title="修改后的文章", content="新正文", revision=2
    )

    with pytest.raises(APIError) as error:
        await retry_ai_publish_dispatch(15, session, SimpleNamespace(id=9))

    assert error.value.code == "ai_publish_dispatch_article_changed"
    assert dispatch.status == "failed"
    session.commit.assert_not_awaited()
    locked_draft_query = session.scalar.await_args.args[0]
    sql = str(locked_draft_query.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql
