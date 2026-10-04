import asyncio
import hashlib
import struct
import zlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.errors import APIError
from app.api.routes.articles import (
    create_article,
    delete_article,
    list_articles,
    publish_article,
    update_article,
)
from app.db.base import Base
from app.models.ai import AIDraft, ArticlePublishAttempt
from app.models.monitored_user import MonitoredUser
from app.models.qq import QQBotAccount, QQDelivery, QQJoinedGroup
from app.models.tweet import Tweet
from app.models.tweet_screenshot import TweetScreenshot
from app.schemas.article import ArticleCreate, ArticlePatch, ArticlePublishCreate
from app.services import article_media, tweet_screenshot_media
from app.services.xhs_client import validated_source


def screenshot_png(pixel: bytes = b"\xff\x00\x00\xff") -> bytes:
    def chunk(kind: bytes, contents: bytes) -> bytes:
        return (
            struct.pack(">I", len(contents))
            + kind
            + contents
            + struct.pack(">I", zlib.crc32(kind + contents) & 0xFFFFFFFF)
        )

    return (
        tweet_screenshot_media.PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00" + pixel))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def media_directories(tmp_path, monkeypatch):
    uploads = tmp_path / "article-uploads"
    screenshots = tmp_path / "tweet-screenshots"
    monkeypatch.setattr(article_media, "ARTICLE_UPLOAD_DIR", uploads)
    monkeypatch.setattr(tweet_screenshot_media, "TWEET_SCREENSHOT_DIR", screenshots)
    monkeypatch.setenv("ARTICLE_UPLOAD_DIR", str(uploads))
    return SimpleNamespace(uploads=uploads, screenshots=screenshots)


@pytest.fixture
def admin():
    return SimpleNamespace(id=7)


@pytest.fixture
async def database(media_directories):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[
                    MonitoredUser.__table__,
                    Tweet.__table__,
                    TweetScreenshot.__table__,
                    AIDraft.__table__,
                    ArticlePublishAttempt.__table__,
                    QQBotAccount.__table__,
                    QQJoinedGroup.__table__,
                    QQDelivery.__table__,
                ],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        # Production uses PostgreSQL BIGINT sequences; SQLite requires explicit
        # IDs for these columns. Keep the workaround local to this session.
        next_ids = {AIDraft: 100, QQDelivery: 100}

        def assign_ids(sync_session, _flush_context, _instances):
            for instance in sync_session.new:
                model = type(instance)
                if model in next_ids and instance.id is None:
                    instance.id = next_ids[model]
                    next_ids[model] += 1

        event.listen(session.sync_session, "before_flush", assign_ids)
        now = datetime.now(UTC)
        session.add(MonitoredUser(id=1, username="alice", x_user_id="42"))
        session.add(
            Tweet(
                id=1,
                tweet_id="123",
                monitored_user_id=1,
                author_id="42",
                text="Source post",
                posted_at=now,
                raw_payload={},
            )
        )
        session.add(
            AIDraft(
                id=1,
                source_tweet_id=1,
                article_source="ai",
                title="关联文章",
                content="正文",
                images=[],
                revision=1,
            )
        )
        session.add(
            QQBotAccount(
                id=1,
                name="测试机器人",
                app_id="qq-app",
                encrypted_app_secret="encrypted",
                secret_hint="hint",
                secret_fingerprint="fingerprint",
                is_enabled=True,
            )
        )
        session.add(
            QQJoinedGroup(
                id=1,
                bot_id=1,
                app_id="qq-app",
                group_openid="group",
                is_joined=True,
                last_event_at=now,
            )
        )
        await session.commit()
        yield session
        event.remove(session.sync_session, "before_flush", assign_ids)
    await engine.dispose()


async def add_screenshot(database, *, status="succeeded", file_exists=True):
    image = screenshot_png()
    relative_name = "123/" + "a" * 32 + ".png"
    if file_exists:
        tweet_screenshot_media.write_screenshot("123", "a" * 32, image)
    screenshot = TweetScreenshot(
        tweet_id="123",
        status=status,
        image_path=relative_name,
        sha256=hashlib.sha256(image).hexdigest(),
        width=1,
        height=1,
        captured_at=datetime.now(UTC),
    )
    database.add(screenshot)
    await database.commit()
    return screenshot


async def articles(database):
    return await list_articles(
        database,
        None,
        page=1,
        page_size=20,
        keyword=None,
        article_source=None,
        publish_status=None,
    )


async def test_existing_article_automatically_exposes_source_screenshot(database):
    screenshot = await add_screenshot(database)

    article = (await articles(database)).items[0]

    assert article.images == []
    assert article.include_source_screenshot is True
    assert article.source_url == "https://x.com/alice/status/123"
    assert article.source_screenshot.tweet_id == "123"
    assert article.source_screenshot.sha256 == screenshot.sha256
    assert article.source_screenshot.captured_at is not None
    assert article.revision == 1


@pytest.mark.parametrize("value", [None, "false", 0, 1])
def test_screenshot_preference_rejects_null_and_non_booleans(value):
    with pytest.raises(ValidationError):
        ArticlePatch(include_source_screenshot=value, revision=1)


async def test_remove_source_screenshot_persists_per_article_and_keeps_publish_copy(
    database, admin, xhs_publish
):
    screenshot = await add_screenshot(database)
    await publish_article(1, ArticlePublishCreate(channel="xhs"), database, None, admin)
    copied_path = Path(xhs_publish.await_args.kwargs["payload"]["images"][0])
    article = await database.get(AIDraft, 1)
    prior_copies = article_media.article_screenshot_copies(article)
    database.add(
        AIDraft(
            id=2, source_tweet_id=1, article_source="ai", title="Same source",
            content="Other article", images=[], revision=1,
        )
    )
    await database.commit()

    result = await update_article(
        1, ArticlePatch(include_source_screenshot=False, revision=1), database, admin
    )
    await database.refresh(article)

    assert result.include_source_screenshot is False
    assert result.source_screenshot is None
    assert result.revision == 2
    assert result.publish_status == "unpublished"
    assert result.publish_channel is None
    assert result.publish_error is None
    assert result.published_at is None
    assert article.publish_attempt_id is None
    assert article.draft_metadata[article_media.INCLUDE_SOURCE_SCREENSHOT_KEY] is False
    assert article_media.article_screenshot_copies(article) == prior_copies
    assert await asyncio.to_thread(copied_path.read_bytes) == screenshot_png()
    assert tweet_screenshot_media.screenshot_path(screenshot.image_path) is not None
    assert await database.get(TweetScreenshot, "123") is screenshot
    refreshed = {item.id: item for item in (await articles(database)).items}
    assert refreshed[1].include_source_screenshot is False
    assert refreshed[1].source_screenshot is None
    assert refreshed[1].source_url == refreshed[2].source_url
    assert refreshed[2].include_source_screenshot is True
    assert refreshed[2].source_screenshot.tweet_id == "123"

    edited = await update_article(
        1, ArticlePatch(title="Still detached", revision=2), database, admin
    )
    assert edited.include_source_screenshot is False
    assert edited.source_screenshot is None
    restored = await update_article(
        1, ArticlePatch(include_source_screenshot=True, revision=3), database, admin
    )
    assert restored.include_source_screenshot is True
    assert restored.source_screenshot.tweet_id == "123"
    assert await asyncio.to_thread(copied_path.exists)


async def test_removed_source_screenshot_stays_removed_when_capture_finishes(database, admin):
    await update_article(
        1, ArticlePatch(include_source_screenshot=False, revision=1), database, admin
    )
    await add_screenshot(database)

    result = (await articles(database)).items[0]
    assert result.include_source_screenshot is False
    assert result.source_screenshot is None


async def test_queued_article_cannot_change_screenshot_preference(database, admin):
    article = await database.get(AIDraft, 1)
    article.publish_status = "queued"
    await database.commit()

    with pytest.raises(APIError) as error:
        await update_article(
            1, ArticlePatch(include_source_screenshot=False, revision=1), database, admin
        )

    assert error.value.code == "article_publish_in_progress"
    assert article_media.article_includes_source_screenshot(article) is True


async def test_article_list_resolves_shared_sources_in_one_database_query(database):
    await add_screenshot(database)
    database.add(
        AIDraft(
            id=2,
            source_tweet_id=1,
            article_source="ai",
            title="同一原帖的另一篇文章",
            content="正文",
            images=[],
            revision=1,
        )
    )
    await database.commit()
    source_queries = []

    def count_source_queries(_connection, _cursor, statement, _parameters, _context, _many):
        if "JOIN tweet_screenshots" in statement:
            source_queries.append(statement)

    engine = database.sync_session.bind
    event.listen(engine, "before_cursor_execute", count_source_queries)
    try:
        page = await articles(database)
    finally:
        event.remove(engine, "before_cursor_execute", count_source_queries)

    assert page.total == 2
    assert all(article.source_screenshot.tweet_id == "123" for article in page.items)
    assert len(source_queries) == 1


async def test_late_screenshot_appears_without_regenerating_article(database):
    screenshot = await add_screenshot(database, status="pending", file_exists=False)
    assert (await articles(database)).items[0].source_screenshot is None

    tweet_screenshot_media.write_screenshot("123", "a" * 32, screenshot_png())
    screenshot.status = "succeeded"
    await database.commit()

    article = (await articles(database)).items[0]
    assert article.source_screenshot.tweet_id == "123"
    assert article.revision == 1
    assert article.title == "关联文章"


@pytest.mark.parametrize(
    ("status", "file_exists"),
    [("pending", True), ("running", True), ("failed", True), ("succeeded", False)],
)
async def test_unavailable_screenshot_is_not_exposed(database, status, file_exists):
    await add_screenshot(database, status=status, file_exists=file_exists)

    assert (await articles(database)).items[0].source_screenshot is None


async def test_manual_article_does_not_inherit_an_unrelated_screenshot(database, admin):
    await add_screenshot(database)
    image = f"{admin.id}/uploaded.png"
    article_media.write_article_image(image, b"manual image")

    result = await create_article(
        ArticleCreate(title="手动文章", content="手动正文", images=[image]), database, admin
    )

    assert result.source_tweet_id is None
    assert result.source_screenshot is None
    assert result.images == [image]


async def test_article_edit_preserves_source_screenshot_and_uploaded_images(database, admin):
    await add_screenshot(database)
    image = f"{admin.id}/uploaded.png"
    article_media.write_article_image(image, b"manual image")

    result = await update_article(
        1,
        ArticlePatch(title="编辑后的标题", images=[image], revision=1),
        database,
        admin,
    )

    assert result.title == "编辑后的标题"
    assert result.images == [image]
    assert result.source_screenshot.tweet_id == "123"
    assert result.revision == 2
    assert tweet_screenshot_media.screenshot_path("123/" + "a" * 32 + ".png") is not None


@pytest.fixture
def xhs_publish(monkeypatch):
    submit = AsyncMock(return_value={})
    monkeypatch.setattr("app.api.routes.articles.has_xhs_credentials", AsyncMock(return_value=True))
    monkeypatch.setattr("app.api.routes.articles.submit_xhs_job", submit)
    monkeypatch.setattr("app.api.routes.articles.clear_verification_image", lambda _admin_id: None)
    return submit


async def test_qq_publish_sends_source_screenshot_before_uploaded_images(
    database, admin, media_directories, monkeypatch
):
    await add_screenshot(database)
    image = f"{admin.id}/uploaded.png"
    upload_path = article_media.write_article_image(image, b"manual image")
    article = await database.get(AIDraft, 1)
    article.images = [image]
    await database.commit()
    enqueue = AsyncMock()
    monkeypatch.setattr("app.api.routes.articles.enqueue_qq_delivery_ids", enqueue)

    result = await publish_article(
        1,
        ArticlePublishCreate(channel="qq", bot_id=1, group_openids=["group"]),
        database,
        None,
        admin,
    )

    deliveries = list(await database.scalars(select(QQDelivery).order_by(QQDelivery.sequence)))
    assert result.publish_status == "queued"
    assert deliveries[0].message_body.startswith("标题:")
    image_paths = [Path(delivery.media_path) for delivery in deliveries if delivery.media_path]
    assert len(image_paths) == 2
    assert await asyncio.to_thread(image_paths[0].read_bytes) == screenshot_png()
    assert image_paths[0].parent == media_directories.uploads / str(admin.id)
    assert image_paths[1] == upload_path
    assert result.delivery_ids == [delivery.id for delivery in deliveries]
    enqueue.assert_awaited_once()
    assert article.images == [image]
    assert str(image_paths[0].relative_to(media_directories.uploads)) in (
        article.draft_metadata["source_screenshot_images"]
    )


async def test_xhs_publish_accepts_source_screenshot_as_only_image(
    database, admin, media_directories, xhs_publish
):
    await add_screenshot(database)

    result = await publish_article(
        1, ArticlePublishCreate(channel="xhs"), database, None, admin
    )

    assert result.publish_status == "published"
    payload = xhs_publish.await_args.kwargs["payload"]
    assert len(payload["images"]) == 1
    path = Path(payload["images"][0])
    assert await asyncio.to_thread(path.read_bytes) == screenshot_png()
    assert path.parent == media_directories.uploads / str(admin.id)
    assert validated_source(str(path), admin.id) == path
    assert (await database.get(AIDraft, 1)).images == []


async def test_xhs_publish_omits_removed_source_and_accepts_18_uploaded_images(
    database, admin, xhs_publish
):
    screenshot = await add_screenshot(database)
    images = [f"{admin.id}/upload-{index}.png" for index in range(18)]
    for image in images:
        article_media.write_article_image(image, b"manual image")
    await update_article(
        1, ArticlePatch(images=images, include_source_screenshot=False, revision=1),
        database, admin,
    )

    await publish_article(1, ArticlePublishCreate(channel="xhs"), database, None, admin)

    assert xhs_publish.await_args.kwargs["payload"]["images"] == [
        str(article_media.article_image_path(image)) for image in images
    ]
    assert article_media.article_screenshot_copies(await database.get(AIDraft, 1)) == []
    assert tweet_screenshot_media.screenshot_path(screenshot.image_path) is not None


async def test_removing_all_images_requires_an_image_before_xhs_publish(
    database, admin, xhs_publish
):
    screenshot = await add_screenshot(database)
    image = f"{admin.id}/uploaded.png"
    upload_path = article_media.write_article_image(image, b"manual image")
    article = await database.get(AIDraft, 1)
    article.images = [image]
    await database.commit()
    await update_article(
        1, ArticlePatch(images=[], include_source_screenshot=False, revision=1), database, admin
    )

    with pytest.raises(APIError) as error:
        await publish_article(1, ArticlePublishCreate(channel="xhs"), database, None, admin)

    assert error.value.code == "xhs_images_required"
    xhs_publish.assert_not_awaited()
    assert not await asyncio.to_thread(upload_path.exists)
    assert tweet_screenshot_media.screenshot_path(screenshot.image_path) is not None


async def test_qq_publish_omits_removed_source_screenshot(database, admin, monkeypatch):
    screenshot = await add_screenshot(database)
    image = f"{admin.id}/uploaded.png"
    upload_path = article_media.write_article_image(image, b"manual image")
    await update_article(
        1, ArticlePatch(images=[image], include_source_screenshot=False, revision=1),
        database, admin,
    )
    enqueue = AsyncMock()
    monkeypatch.setattr("app.api.routes.articles.enqueue_qq_delivery_ids", enqueue)

    await publish_article(
        1, ArticlePublishCreate(channel="qq", bot_id=1, group_openids=["group"]),
        database, None, admin,
    )

    deliveries = list(await database.scalars(select(QQDelivery).order_by(QQDelivery.sequence)))
    assert [Path(row.media_path) for row in deliveries if row.media_path] == [upload_path]
    assert article_media.article_screenshot_copies(await database.get(AIDraft, 1)) == []
    assert tweet_screenshot_media.screenshot_path(screenshot.image_path) is not None
    enqueue.assert_awaited_once()


@pytest.mark.parametrize("upload_count", [17, 18])
async def test_xhs_image_limit_includes_source_screenshot(
    database, admin, xhs_publish, upload_count
):
    await add_screenshot(database)
    images = [f"{admin.id}/upload-{index}.png" for index in range(upload_count)]
    for image in images:
        article_media.write_article_image(image, b"manual image")
    article = await database.get(AIDraft, 1)
    article.images = images
    await database.commit()

    if upload_count == 18:
        with pytest.raises(APIError) as error:
            await publish_article(
                1, ArticlePublishCreate(channel="xhs"), database, None, admin
            )
        assert error.value.status_code == 422
        xhs_publish.assert_not_awaited()
    else:
        await publish_article(1, ArticlePublishCreate(channel="xhs"), database, None, admin)
        assert len(xhs_publish.await_args.kwargs["payload"]["images"]) == 18


async def test_article_delete_cleans_publish_copies_but_retains_original_screenshot(
    database, admin, xhs_publish, media_directories
):
    screenshot = await add_screenshot(database)
    image = f"{admin.id}/uploaded.png"
    upload_path = article_media.write_article_image(image, b"manual image")
    article = await database.get(AIDraft, 1)
    article.images = [image]
    await database.commit()
    await publish_article(1, ArticlePublishCreate(channel="xhs"), database, None, admin)
    copied_path = Path(xhs_publish.await_args.kwargs["payload"]["images"][0])

    await delete_article(1, database, admin)

    assert await database.get(AIDraft, 1) is None
    assert not await asyncio.to_thread(copied_path.exists)
    assert not await asyncio.to_thread(upload_path.exists)
    source_path = tweet_screenshot_media.screenshot_path(screenshot.image_path)
    assert await asyncio.to_thread(source_path.read_bytes) == screenshot_png()
    assert await database.get(TweetScreenshot, "123") is not None


async def test_republishing_reuses_unchanged_copy_and_preserves_prior_capture(
    database, admin, xhs_publish, media_directories
):
    screenshot = await add_screenshot(database)
    payload = ArticlePublishCreate(channel="xhs")
    await publish_article(1, payload, database, None, admin)
    initial_path = Path(xhs_publish.await_args.kwargs["payload"]["images"][0])
    await publish_article(1, payload, database, None, admin)
    assert Path(xhs_publish.await_args.kwargs["payload"]["images"][0]) == initial_path
    article = await database.get(AIDraft, 1)
    assert len(article.draft_metadata["source_screenshot_images"]) == 1

    newer_png = screenshot_png(b"\x00\x00\xff\xff")
    tweet_screenshot_media.write_screenshot("123", "a" * 32, newer_png)
    screenshot.sha256 = hashlib.sha256(newer_png).hexdigest()
    await database.commit()
    await publish_article(1, payload, database, None, admin)
    newer_path = Path(xhs_publish.await_args.kwargs["payload"]["images"][0])

    assert newer_path != initial_path
    assert await asyncio.to_thread(initial_path.read_bytes) == screenshot_png()
    assert await asyncio.to_thread(newer_path.read_bytes) == newer_png
    assert len(article.draft_metadata["source_screenshot_images"]) == 2
    assert all(
        (media_directories.uploads / image).parent == initial_path.parent
        for image in article.draft_metadata["source_screenshot_images"]
    )
