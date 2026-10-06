import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ai_worker import AIGenerationWorker
from app.api.errors import APIError
from app.api.routes.ai import delete_ai_job, patch_ai_draft
from app.db.base import Base
from app.models.ai import AIDraft, AIGenerationAttempt, AIGenerationJob, AISkill, AIUserProfile
from app.models.monitored_user import MonitoredUser
from app.models.tweet import Tweet
from app.models.tweet_screenshot import TweetScreenshot
from app.schemas.ai import AIDraftPatch
from app.services import article_media, tweet_screenshot_media
from app.services.article_media import INCLUDE_SOURCE_SCREENSHOT_KEY, SOURCE_SCREENSHOT_IMAGES_KEY


@pytest.fixture
async def context(tmp_path, monkeypatch):
    monkeypatch.setattr(article_media, "ARTICLE_UPLOAD_DIR", tmp_path / "article-uploads")
    monkeypatch.setattr(tweet_screenshot_media, "TWEET_SCREENSHOT_DIR", tmp_path / "screenshots")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[
                    MonitoredUser.__table__,
                    Tweet.__table__,
                    TweetScreenshot.__table__,
                    AISkill.__table__,
                    AIGenerationJob.__table__,
                    AIGenerationAttempt.__table__,
                    AIDraft.__table__,
                    AIUserProfile.__table__,
                ],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(MonitoredUser(id=1, username="alice", x_user_id="42"))
        db.add(
            Tweet(
                id=1,
                tweet_id="123",
                monitored_user_id=1,
                author_id="42",
                text="Source post",
                posted_at=datetime.now(UTC),
                raw_payload={},
            )
        )
        source_image = tweet_screenshot_media.write_screenshot("123", "a" * 32, b"screenshot")
        source_path = tweet_screenshot_media.screenshot_path(source_image)
        db.add(TweetScreenshot(tweet_id="123", status="succeeded", image_path=source_image))
        copy_image, copy_path = article_media.snapshot_article_screenshot(
            source_path, article_id=1, admin_id=7
        )
        upload_image = "7/upload.png"
        upload_path = article_media.write_article_image(upload_image, b"uploaded image")
        job = AIGenerationJob(
            id=1,
            source_tweet_id=1,
            skill_ids=[],
            skill_snapshot=[],
            idempotency_key="test:1",
            status="succeeded",
            provider="openai_responses",
            model_name="test-model",
        )
        draft = AIDraft(
            id=1,
            job=job,
            source_tweet_id=1,
            title="Article",
            content="Content",
            images=[upload_image],
            draft_metadata={"old": True, SOURCE_SCREENSHOT_IMAGES_KEY: [copy_image]},
            revision=1,
        )
        db.add(draft)
        await db.commit()
        yield SimpleNamespace(
            db=db,
            factory=factory,
            job=job,
            draft=draft,
            copy_image=copy_image,
            copy_path=copy_path,
            upload_image=upload_image,
            upload_path=upload_path,
            source_path=source_path,
        )
    await engine.dispose()


@pytest.mark.parametrize("metadata", [None, {"new": True}, {SOURCE_SCREENSHOT_IMAGES_KEY: None}])
async def test_metadata_edit_preserves_server_screenshot_copies(context, metadata):
    result = await patch_ai_draft(
        1, AIDraftPatch(metadata=metadata, revision=1), context.db, None
    )

    assert result.metadata[SOURCE_SCREENSHOT_IMAGES_KEY] == [context.copy_image]
    assert "old" not in result.metadata
    assert result.revision == 2
    assert await asyncio.to_thread(context.copy_path.exists)
    assert await asyncio.to_thread(context.upload_path.exists)
    assert await asyncio.to_thread(context.source_path.exists)


async def test_client_cannot_replace_tracked_copies_or_delete_unrelated_file(context):
    forged_image = "7/source-1-" + "b" * 64 + ".png"
    forged_path = article_media.write_article_image(forged_image, b"unrelated owned image")
    result = await patch_ai_draft(
        1,
        AIDraftPatch(
            metadata={"new": True, SOURCE_SCREENSHOT_IMAGES_KEY: [forged_image]}, revision=1
        ),
        context.db,
        None,
    )

    assert result.metadata == {"new": True, SOURCE_SCREENSHOT_IMAGES_KEY: [context.copy_image]}
    await delete_ai_job(1, context.db, None)

    assert not await asyncio.to_thread(context.copy_path.exists)
    assert not await asyncio.to_thread(context.upload_path.exists)
    assert await asyncio.to_thread(forged_path.exists)
    assert await asyncio.to_thread(context.source_path.exists)


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [(None, None), ({SOURCE_SCREENSHOT_IMAGES_KEY: ["7/injected.png"]}, {})],
)
async def test_metadata_without_server_copies_keeps_null_semantics(context, metadata, expected):
    context.draft.draft_metadata = {"old": True}
    await context.db.commit()

    result = await patch_ai_draft(
        1, AIDraftPatch(metadata=metadata, revision=1), context.db, None
    )

    assert result.metadata == expected


@pytest.mark.parametrize("preference", [False, True])
@pytest.mark.parametrize("metadata", [None, {}, {INCLUDE_SOURCE_SCREENSHOT_KEY: "override"}])
async def test_metadata_edit_preserves_article_screenshot_preference(context, preference, metadata):
    context.draft.draft_metadata = {INCLUDE_SOURCE_SCREENSHOT_KEY: preference}
    await context.db.commit()

    result = await patch_ai_draft(
        1, AIDraftPatch(metadata=metadata, revision=1), context.db, None
    )

    assert result.metadata == {INCLUDE_SOURCE_SCREENSHOT_KEY: preference}
    assert article_media.article_includes_source_screenshot(context.draft) is preference
    assert await asyncio.to_thread(context.source_path.exists)


async def test_metadata_edit_cannot_set_screenshot_preference_for_legacy_article(context):
    context.draft.draft_metadata = None
    await context.db.commit()

    result = await patch_ai_draft(
        1, AIDraftPatch(metadata={INCLUDE_SOURCE_SCREENSHOT_KEY: False}, revision=1),
        context.db, None,
    )

    assert result.metadata == {}
    assert article_media.article_includes_source_screenshot(context.draft) is True


def test_generated_metadata_cannot_set_new_article_screenshot_preferences():
    metadata = {
        "other": True, INCLUDE_SOURCE_SCREENSHOT_KEY: False,
        SOURCE_SCREENSHOT_IMAGES_KEY: ["7/source-1-" + "a" * 64 + ".png"],
    }

    assert article_media.preserve_article_media_metadata(None, metadata) == {"other": True}
    assert metadata[INCLUDE_SOURCE_SCREENSHOT_KEY] is False


async def test_job_delete_cleans_copies_and_uploads_but_preserves_source_and_shared_media(context):
    shared_image = "7/shared.png"
    shared_path = article_media.write_article_image(shared_image, b"shared upload")
    context.draft.images = [context.upload_image, shared_image]
    context.db.add(
        AIDraft(
            id=2,
            article_source="user",
            title="Other article",
            content="Other content",
            images=[shared_image, context.copy_image],
        )
    )
    await context.db.commit()

    result = await delete_ai_job(1, context.db, None)

    assert result.message == "AI 生成任务已删除"
    assert await context.db.scalar(select(AIGenerationJob).where(AIGenerationJob.id == 1)) is None
    assert await context.db.scalar(select(AIDraft).where(AIDraft.id == 1)) is None
    assert not await asyncio.to_thread(context.upload_path.exists)
    assert await asyncio.to_thread(context.copy_path.exists)
    assert await asyncio.to_thread(shared_path.exists)
    assert await asyncio.to_thread(context.source_path.exists)
    assert await context.db.get(TweetScreenshot, "123") is not None


async def test_running_job_delete_keeps_draft_and_files(context):
    context.job.status = "running"
    await context.db.commit()

    with pytest.raises(APIError) as error:
        await delete_ai_job(1, context.db, None)

    assert error.value.code == "ai_job_running"
    assert await context.db.get(AIDraft, 1) is context.draft
    assert await asyncio.to_thread(context.copy_path.exists)
    assert await asyncio.to_thread(context.upload_path.exists)


async def test_older_same_timestamp_tweet_cannot_replace_newer_author_profile(context, monkeypatch):
    original = await context.db.get(Tweet, 1)
    context.db.add(
        Tweet(
            id=2,
            tweet_id="124",
            monitored_user_id=1,
            author_id="42",
            text="Newer source post",
            posted_at=original.posted_at,
            raw_payload={},
        )
    )
    context.db.add(
        AIUserProfile(
            monitored_user_id=1,
            identity_summary="newer profile",
            focus_summary="newer focus",
            relationship_summary="",
            recurring_topics=[],
            evidence=[],
            confidence=0.8,
            version=2,
            last_source_tweet_id=2,
        )
    )
    context.job.status = "running"
    context.job.claim_token = "claim"
    context.job.request_snapshot = {"author_context": {"author": {"monitored_user_id": 1}}}
    await context.db.commit()
    monkeypatch.setattr("app.ai_worker.AsyncSessionFactory", context.factory)
    worker = object.__new__(AIGenerationWorker)
    worker._assert_lock = AsyncMock()

    assert await worker._commit_success(
        1,
        "claim",
        "lock",
        asyncio.Event(),
        {
            "title": "Older result",
            "content": "Older content",
            "author_profile": {"identity_summary": "older profile", "focus_summary": "older focus"},
        },
        {},
        "a" * 64,
        "b" * 64,
    )

    async with context.factory() as db:
        profile = await db.get(AIUserProfile, 1)
        assert profile.identity_summary == "newer profile"
        assert profile.focus_summary == "newer focus"
        assert profile.last_source_tweet_id == 2


@pytest.mark.parametrize("preference", [None, False, True])
async def test_regeneration_preserves_server_copies_and_ignores_generated_copy_metadata(
    context, monkeypatch, preference
):
    context.job.status = "running"
    context.job.claim_token = "claim"
    if preference is not None:
        context.draft.draft_metadata = {
            **context.draft.draft_metadata, INCLUDE_SOURCE_SCREENSHOT_KEY: preference,
        }
    await context.db.commit()
    monkeypatch.setattr("app.ai_worker.AsyncSessionFactory", context.factory)
    worker = object.__new__(AIGenerationWorker)
    worker._assert_lock = AsyncMock()
    supplied_image = "7/source-1-" + "b" * 64 + ".png"

    committed = await worker._commit_success(
        1,
        "claim",
        "lock",
        asyncio.Event(),
        {
            "title": "Regenerated article",
            "content": "New content",
            "metadata": {
                "new": True, SOURCE_SCREENSHOT_IMAGES_KEY: [supplied_image],
                INCLUDE_SOURCE_SCREENSHOT_KEY: not preference,
            },
        },
        {},
        "a" * 64,
        "b" * 64,
    )
    await context.db.refresh(context.draft)

    assert committed is True
    assert context.draft.title == "Regenerated article"
    assert context.draft.revision == 2
    assert context.draft.draft_metadata[SOURCE_SCREENSHOT_IMAGES_KEY] == [context.copy_image]
    assert context.draft.draft_metadata["new"] is True
    if preference is None:
        assert INCLUDE_SOURCE_SCREENSHOT_KEY not in context.draft.draft_metadata
    else:
        assert context.draft.draft_metadata[INCLUDE_SOURCE_SCREENSHOT_KEY] is preference
    assert context.draft.images == [context.upload_image]
    assert await asyncio.to_thread(context.copy_path.exists)
