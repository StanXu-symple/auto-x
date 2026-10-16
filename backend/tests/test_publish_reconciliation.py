"""Human decisions release an ambiguous XHS attempt without replaying it implicitly."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.errors import APIError
from app.api.routes.ai import reconcile_ai_publish_dispatch, retry_ai_publish_dispatch
from app.api.routes.articles import _ensure_no_auto_publish_conflict, article_publish_history
from app.db.base import Base
from app.models import Admin, AIDraft, AIGenerationJob, AIPublishDispatch, ArticlePublishAttempt
from app.schemas.ai_publish import AIPublishReconcileRequest
from app.services.ai_publish_read import dispatches_by_draft

OWNER = SimpleNamespace(id=9)
NOTE_URL = "https://www.xiaohongshu.com/explore/abcdef012345678901234567"


@pytest.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        session.add(Admin(id=9, username="owner", password_hash="unused"))
        session.add(
            AIGenerationJob(
                id=7,
                source_tweet_id=1,
                idempotency_key="reconciliation-job",
                skill_ids=[],
                skill_snapshot=[],
                status="succeeded",
                provider="openai_responses",
                model_name="test",
            )
        )
        session.add(
            AIDraft(
                id=20, job_id=7, article_source="ai", title="文章", content="正文", revision=1,
                publish_status="published", publish_channel="qq", publish_attempt_id="qq-attempt",
            )
        )
        session.add(
            ArticlePublishAttempt(
                attempt_id="xhs-attempt", article_id=20, channel="xhs", status="failed",
                error="原始网络中断", target_summary="当前账号", completed_at=datetime.now(UTC),
            )
        )
        session.add(
            AIPublishDispatch(
                id=15, job_id=7, draft_id=20, channel="xhs", status="uncertain", attempts=2,
                owner_admin_id=9, article_publish_attempt_id="xhs-attempt",
                payload_snapshot={"owner_admin_id": 9, "draft_revision": 1},
                last_error="小红书结果未确认", completed_at=datetime.now(UTC),
            )
        )
    try:
        yield factory
    finally:
        await engine.dispose()


async def test_confirm_published_is_durable_and_keeps_other_channel_state(db_factory):
    async with db_factory() as session:
        result = await reconcile_ai_publish_dispatch(
            15,
            AIPublishReconcileRequest(
                decision="published", published_url=NOTE_URL, note="已在创作中心核实"
            ),
            session, OWNER,
        )
        assert result.status == "published"
        assert result.reconciliation_result == "published"
        assert result.published_url == NOTE_URL
        assert result.last_error is None
    async with db_factory() as session:
        rows = await dispatches_by_draft(session, [20])
        assert rows[20][0].published_url == NOTE_URL
        assert rows[20][0].reconciled_by_admin_id == 9
        assert rows[20][0].reconciled_at is not None
        history = await article_publish_history(20, session, OWNER, page=1, page_size=20)
        assert history.items[0].status == "published"
        assert history.items[0].reconciliation_note == "已在创作中心核实"
        assert history.items[0].published_url == NOTE_URL
        draft = await session.get(AIDraft, 20)
        assert (draft.publish_channel, draft.publish_attempt_id) == ("qq", "qq-attempt")


async def test_confirm_not_published_requires_explicit_retry_and_preserves_history(db_factory):
    async with db_factory() as session:
        draft = await session.get(AIDraft, 20)
        with pytest.raises(APIError) as guard:
            await _ensure_no_auto_publish_conflict(session, draft, "xhs")
        assert guard.value.code == "article_auto_publish_conflict"
        result = await reconcile_ai_publish_dispatch(
            15,
            AIPublishReconcileRequest(decision="not_published", note="已检查账号没有此笔记"),
            session, OWNER,
        )
        assert result.status == "failed"
        assert result.article_publish_attempt_id is None
        assert result.attempts == 2
        await _ensure_no_auto_publish_conflict(session, draft, "xhs")
    async with db_factory() as session:
        result = await retry_ai_publish_dispatch(15, session, OWNER)
        assert result.status == "pending"
        assert result.reconciliation_result is None
        assert result.reconciled_at is None
        assert result.reconciliation_note is None
        assert result.published_url is None
        assert result.reconciled_by_admin_id is None
        attempt = await session.get(ArticlePublishAttempt, "xhs-attempt")
        assert attempt.status == "failed"
        assert attempt.reconciliation_result == "not_published"
        assert attempt.reconciliation_note == "已检查账号没有此笔记"


async def test_still_uncertain_does_not_enable_retry_or_drop_original_attempt(db_factory):
    async with db_factory() as session:
        result = await reconcile_ai_publish_dispatch(
            15, AIPublishReconcileRequest(decision="uncertain", note="等待账号本人确认"),
            session, OWNER,
        )
        assert result.status == "uncertain"
        assert result.article_publish_attempt_id == "xhs-attempt"
        with pytest.raises(APIError) as error:
            await retry_ai_publish_dispatch(15, session, OWNER)
        assert error.value.code == "ai_publish_dispatch_not_retryable"


async def test_edited_draft_can_record_old_result_but_cannot_retry_old_payload(db_factory):
    async with db_factory() as session:
        draft = await session.get(AIDraft, 20)
        draft.revision = 2
        draft.content = "新版正文"
        await session.commit()
        result = await reconcile_ai_publish_dispatch(
            15, AIPublishReconcileRequest(decision="not_published"), session, OWNER
        )
        assert result.reconciliation_result == "not_published"
        with pytest.raises(APIError) as error:
            await retry_ai_publish_dispatch(15, session, OWNER)
        assert error.value.code == "ai_publish_dispatch_article_changed"
        assert (await session.get(AIPublishDispatch, 15)).status == "failed"


async def test_missing_attempt_cannot_release_duplicate_publish_guard(db_factory):
    async with db_factory() as session:
        dispatch = await session.get(AIPublishDispatch, 15)
        dispatch.article_publish_attempt_id = None
        await session.commit()
        with pytest.raises(APIError) as error:
            await reconcile_ai_publish_dispatch(
                15, AIPublishReconcileRequest(decision="not_published"), session, OWNER
            )
        assert error.value.code == "ai_publish_attempt_not_found"
        assert dispatch.status == "uncertain"
        assert dispatch.reconciled_at is None


@pytest.mark.parametrize(
    ("status", "channel", "admin_id", "expected_code"),
    [
        ("published", "xhs", 9, "ai_publish_dispatch_not_reconcilable"),
        ("dispatching", "xhs", 9, "ai_publish_dispatch_not_reconcilable"),
        ("failed", "xhs", 9, "ai_publish_dispatch_not_reconcilable"),
        ("uncertain", "qq", 9, "ai_publish_dispatch_not_reconcilable"),
        ("uncertain", "xhs", 8, "ai_publish_dispatch_forbidden"),
    ],
)
async def test_reconcile_rejects_unowned_or_non_ambiguous_results(
    db_factory, status, channel, admin_id, expected_code
):
    async with db_factory() as session:
        row = await session.get(AIPublishDispatch, 15)
        row.status = status
        row.channel = channel
        await session.commit()
        with pytest.raises(APIError) as error:
            await reconcile_ai_publish_dispatch(
                15, AIPublishReconcileRequest(decision="not_published"),
                session, SimpleNamespace(id=admin_id),
            )
        assert error.value.code == expected_code
        assert row.reconciled_at is None


async def test_stale_session_cannot_overwrite_completed_reconciliation(db_factory):
    async with db_factory() as stale_session:
        stale_dispatch = await stale_session.get(AIPublishDispatch, 15)
        assert stale_dispatch.status == "uncertain"
        async with db_factory() as current_session:
            await reconcile_ai_publish_dispatch(
                15, AIPublishReconcileRequest(decision="published", published_url=NOTE_URL),
                current_session, OWNER,
            )
        with pytest.raises(APIError) as error:
            await reconcile_ai_publish_dispatch(
                15, AIPublishReconcileRequest(decision="not_published"), stale_session, OWNER
            )
        assert error.value.code == "ai_publish_dispatch_not_reconcilable"
        await stale_session.rollback()
    async with db_factory() as session:
        assert (await session.get(AIPublishDispatch, 15)).status == "published"


async def test_stale_retry_cannot_replay_a_result_updated_by_another_session(db_factory):
    async with db_factory() as stale_session:
        await reconcile_ai_publish_dispatch(
            15, AIPublishReconcileRequest(decision="not_published"), stale_session, OWNER
        )
        stale_dispatch = await stale_session.get(AIPublishDispatch, 15)
        assert stale_dispatch.status == "failed"
        async with db_factory() as current_session:
            await current_session.execute(
                update(AIPublishDispatch).where(AIPublishDispatch.id == 15).values(
                    status="published", article_publish_attempt_id="new-attempt"
                )
            )
            await current_session.commit()
        with pytest.raises(APIError) as error:
            await retry_ai_publish_dispatch(15, stale_session, OWNER)
        assert error.value.code == "ai_publish_dispatch_not_retryable"


@pytest.mark.parametrize(
    "url",
    [
        None, "", "https://www.xiaohongshu.com/", "http://xhslink.com/a/abc",
        "javascript:alert(1)", "https://xiaohongshu.com.evil.example/explore/abc",
        "https://evil.example/xiaohongshu.com/abc", "https://user@xhslink.com/a/abc",
        "https://xhslink.com:8007/a/abc", "https://xhslink.com/a/ab c",
    ],
)
def test_confirm_published_rejects_invalid_or_non_platform_url(url):
    with pytest.raises(ValidationError):
        AIPublishReconcileRequest(decision="published", published_url=url)


@pytest.mark.parametrize(
    "url", [NOTE_URL, "https://xhslink.com/a/abc123", "https://www.xiaohongshu.com:443/explore/abc"]
)
def test_note_or_short_share_links_are_accepted_without_network_lookup(url):
    assert AIPublishReconcileRequest(decision="published", published_url=url).published_url == url


def test_reconcile_note_is_trimmed_and_bounded():
    assert AIPublishReconcileRequest(decision="uncertain", note="  等待确认  ").note == "等待确认"
    with pytest.raises(ValidationError):
        AIPublishReconcileRequest(decision="not_published", note="a" * 2001)
