import asyncio
import base64
import hashlib
import struct
import zlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.errors import APIError
from app.api.routes.tweets import (
    get_tweet,
    get_tweet_screenshot,
    list_tweets,
    request_tweet_screenshot,
)
from app.control_plane.nacos import Instance
from app.core.config import Settings
from app.db.base import Base
from app.models.monitored_user import MonitoredUser
from app.models.tweet import Tweet
from app.models.tweet_screenshot import TweetScreenshot
from app.services import tweet_screenshot_media as media
from app.services.tweet_screenshot_client import ScreenshotBusyError, TweetScreenshotClient
from app.services.tweet_screenshots import (
    RETWEET_ERROR,
    TweetScreenshotProcessor,
    capture_identity,
    enqueue_tweet_screenshots,
)


def png(width=2, height=1):
    def chunk(kind, value):
        return (
            struct.pack(">I", len(value))
            + kind
            + value
            + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)
        )

    return (
        media.PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff\x00\x00\xff" * width) * height))
        + chunk(b"IEND", b"")
    )


def response(tweet_id="123", username="alice"):
    image = png()
    return {
        "tweet_id": tweet_id,
        "username": username,
        "canonical_url": f"https://x.com/{username}/status/{tweet_id}",
        "png_base64": base64.b64encode(image).decode(),
        "sha256": hashlib.sha256(image).hexdigest(),
        "width": 2,
        "height": 1,
        "captured_at": datetime.now(UTC).isoformat(),
    }


@pytest.fixture
def settings():
    return SimpleNamespace(
        camoufox_job_timeout_seconds=30,
        tweet_screenshot_enabled=True,
        tweet_screenshot_max_attempts=3,
        tweet_screenshot_retry_seconds=30,
        tweet_screenshot_scan_interval_seconds=5,
        api_prefix="/api/v1",
    )


@pytest.fixture
async def database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda connection: Base.metadata.create_all(
                connection,
                tables=[
                    MonitoredUser.__table__,
                    Tweet.__table__,
                    TweetScreenshot.__table__,
                ],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        session.add(MonitoredUser(id=1, username="alice", x_user_id="42"))
        session.add(
            Tweet(
                id=1,
                tweet_id="123",
                monitored_user_id=1,
                author_id="42",
                text="A precise post",
                posted_at=datetime.now(UTC),
                raw_payload={},
                attachments={"media_keys": ["photo"]},
            )
        )
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def screenshot_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "TWEET_SCREENSHOT_DIR", tmp_path)
    return tmp_path


async def test_enqueue_is_transactional_and_duplicate_safe(database):
    async with database() as session:
        assert await enqueue_tweet_screenshots(session, ["123", "123"]) == 1
        await session.rollback()
    async with database() as session:
        assert await session.get(TweetScreenshot, "123") is None
        assert await enqueue_tweet_screenshots(session, ["123"]) == 1
        await session.commit()
        existing = await session.get(TweetScreenshot, "123")
        existing.status = "succeeded"
        existing.attempts = 2
        await session.commit()
        assert await enqueue_tweet_screenshots(session, ["123"]) == 0
        await session.commit()
        await session.refresh(existing)
        assert existing.status == "succeeded" and existing.attempts == 2


async def test_processor_persists_only_validated_png_and_metadata(database, settings):
    async with database() as session, session.begin():
        await enqueue_tweet_screenshots(session, ["123"])
    client = SimpleNamespace(capture=AsyncMock(return_value=response()))
    processor = TweetScreenshotProcessor(database, settings, client)
    assert await processor.run_once()
    assert not await processor.run_once()
    args, kwargs = client.capture.call_args
    assert args == ()
    assert {key: kwargs[key] for key in ["tweet_id", "username", "expected_text"]} == {
        "tweet_id": "123",
        "username": "alice",
        "expected_text": "A precise post",
    }
    assert kwargs["expected_media_count"] == 1
    assert len(kwargs["job_id"]) == 32
    async with database() as session:
        job = await session.get(TweetScreenshot, "123")
        assert (job.status, job.attempts, job.width, job.height) == ("succeeded", 1, 2, 1)
        assert media.screenshot_path(job.image_path).read_bytes() == png()
        assert job.sha256 == hashlib.sha256(png()).hexdigest()
        assert job.author_username == "alice"
        assert "png_base64" not in job.__dict__
        assert job.lease_token is None


async def test_new_posts_take_priority_over_due_initial_history(database, settings):
    async with database() as session, session.begin():
        old = await session.scalar(select(Tweet).where(Tweet.tweet_id == "123"))
        old.posted_at = datetime.now(UTC) - timedelta(days=5)
        session.add(Tweet(
            id=2, tweet_id="456", monitored_user_id=1, author_id="42",
            text="Newly published", posted_at=datetime.now(UTC), raw_payload={},
        ))
        await session.flush()
        await enqueue_tweet_screenshots(session, ["123", "456"])
        old_capture = await session.get(TweetScreenshot, "123")
        old_capture.next_attempt_at = datetime.now(UTC) - timedelta(hours=1)
    processor = TweetScreenshotProcessor(database, settings, object())
    claim = await processor.claim_one()
    assert claim.tweet_id == "456"


@pytest.mark.parametrize(
    "field,value",
    [
        ("tweet_id", "999"),
        ("username", "mallory"),
        ("canonical_url", "https://x.com/alice/status/999"),
        ("canonical_url", "https://x.com.evil.test/alice/status/123"),
        ("canonical_url", "https://x.com/alice/status/123?bad=1"),
        ("sha256", "0" * 64),
        ("width", 3),
        ("png_base64", "invalid!"),
        ("captured_at", "yesterday"),
    ],
)
def test_receiver_rejects_mismatched_or_corrupt_results(field, value):
    data = {**response(), field: value}
    with pytest.raises(ValueError):
        media.validate_screenshot(data, tweet_id="123", username="alice")


def test_receiver_rejects_corrupt_png_and_oversize_payload(monkeypatch):
    data = response()
    corrupt = bytearray(png())
    corrupt[30] ^= 1
    data["png_base64"] = base64.b64encode(corrupt).decode()
    data["sha256"] = hashlib.sha256(corrupt).hexdigest()
    with pytest.raises(ValueError, match="checksum"):
        media.validate_screenshot(data, tweet_id="123", username="alice")
    monkeypatch.setattr(media, "MAX_SCREENSHOT_BYTES", 5)
    with pytest.raises(ValueError, match="size"):
        media.validate_screenshot(response(), tweet_id="123", username="alice")


def test_png_validation_streams_multiple_pixel_chunks_and_rejects_extra_zlib_stream():
    assert media.png_dimensions(png(256, 257)) == (256, 257)
    image = png()
    idat_offset = image.index(b"IDAT")
    length = struct.unpack(">I", image[idat_offset - 4 : idat_offset])[0]
    compressed = image[idat_offset + 4 : idat_offset + 4 + length] + zlib.compress(b"extra")
    replaced_chunk = (
        struct.pack(">I", len(compressed))
        + b"IDAT"
        + compressed
        + struct.pack(">I", zlib.crc32(b"IDAT" + compressed) & 0xFFFFFFFF)
    )
    invalid = image[: idat_offset - 4] + replaced_chunk + image[idat_offset + 8 + length :]
    with pytest.raises(ValueError, match="pixel length"):
        media.png_dimensions(invalid)


async def test_capture_failures_have_durable_bounded_retry(database, settings):
    async with database() as session, session.begin():
        await enqueue_tweet_screenshots(session, ["123"])
    client = SimpleNamespace(capture=AsyncMock(side_effect=RuntimeError("browser busy")))
    processor = TweetScreenshotProcessor(database, settings, client)
    for attempt in range(1, 4):
        assert await processor.run_once()
        async with database() as session, session.begin():
            job = await session.get(TweetScreenshot, "123")
            assert job.attempts == attempt
            assert job.status == ("pending" if attempt < 3 else "failed")
            assert job.last_error == "browser busy"
            assert job.next_attempt_at > datetime.now(UTC).replace(tzinfo=None)
            job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    assert not await processor.run_once()
    assert client.capture.await_count == 3


async def test_shared_browser_capacity_does_not_exhaust_attempt_budget(database, settings):
    async with database() as session, session.begin():
        await enqueue_tweet_screenshots(session, ["123"])
    client = SimpleNamespace(capture=AsyncMock(side_effect=ScreenshotBusyError("browser busy")))
    processor = TweetScreenshotProcessor(database, settings, client)
    for _ in range(4):
        await processor.run_once()
        async with database() as session, session.begin():
            job = await session.get(TweetScreenshot, "123")
            assert job.status == "pending" and job.attempts == 0
            assert job.lease_token is None
            job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    client.capture = AsyncMock(return_value=response())
    await processor.run_once()
    async with database() as session:
        job = await session.get(TweetScreenshot, "123")
        assert job.status == "succeeded" and job.attempts == 1


async def test_processor_calls_real_rpc_contract_and_downloads_binary(database, tmp_path):
    import json

    secret = tmp_path / "caller.secret"
    secret.write_text("test-only-secret")
    settings = Settings(
        _env_file=None,
        tweet_screenshot_client_secret_file=str(secret),
        camoufox_job_timeout_seconds=30,
    )
    client = TweetScreenshotClient(settings)
    await client.http.aclose()
    submitted = []

    def handler(request):
        assert request.headers["authorization"] == "Bearer test-rpc-token"
        if request.method == "POST":
            job = json.loads(parse_qs(request.content.decode())["job"][0])
            submitted.append(job)
            capture = response()
            del capture["png_base64"]
            return httpx.Response(
                200,
                json={
                    "job_id": job["job_id"],
                    "state": "succeeded",
                    "data": {**capture, "screenshot_ready": True, "size_bytes": len(png())},
                },
            )
        assert request.url.path == f"/v1/jobs/{submitted[0]['job_id']}/screenshot"
        return httpx.Response(200, headers={"content-type": "image/png"}, content=png())

    client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client.endpoint = AsyncMock(return_value=Instance("browser.internal", 8007, "camoufox"))
    client.headers = AsyncMock(return_value={"Authorization": "Bearer test-rpc-token"})
    async with database() as session, session.begin():
        await enqueue_tweet_screenshots(session, ["123"])
    try:
        await TweetScreenshotProcessor(database, settings, client).run_once()
    finally:
        await client.aclose()
    assert len(submitted) == 1
    assert submitted[0]["payload"] == {
        "operation": "x_screenshot",
        "tweet_id": "123",
        "username": "alice",
        "expected_text": "A precise post",
        "expected_media_count": 1,
    }
    assert len(submitted[0]["job_id"]) == 32
    async with database() as session:
        job = await session.get(TweetScreenshot, "123")
        assert job.status == "succeeded", job.last_error
        assert media.screenshot_path(job.image_path).read_bytes() == png()


async def test_expired_claim_is_recovered_and_old_token_cannot_finish(database, settings):
    async with database() as session, session.begin():
        await enqueue_tweet_screenshots(session, ["123"])
    processor = TweetScreenshotProcessor(database, settings, SimpleNamespace())
    old = await processor.claim_one()
    assert await processor.claim_one() is None
    async with database() as session, session.begin():
        job = await session.get(TweetScreenshot, "123")
        job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    current = await processor.claim_one()
    assert current.attempt == 2 and current.token != old.token
    capture = media.validate_screenshot(response(), tweet_id="123", username="alice")
    assert not await processor._finish(old, capture=capture, image_path="old.png")
    assert await processor._finish(current, error="temporary failure")
    async with database() as session:
        job = await session.get(TweetScreenshot, "123")
        assert job.status == "pending" and job.image_path is None


async def test_cancellation_keeps_lease_until_remote_work_can_settle(database, settings):
    async with database() as session, session.begin():
        await enqueue_tweet_screenshots(session, ["123"])
    started = asyncio.Event()

    async def capture(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    processor = TweetScreenshotProcessor(database, settings, SimpleNamespace(capture=capture))
    task = asyncio.create_task(processor.run_once())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with database() as session:
        job = await session.get(TweetScreenshot, "123")
        assert job.status == "running" and job.lease_token is not None
        assert job.lease_until > datetime.now(UTC).replace(tzinfo=None)
    assert await processor.claim_one() is None


def test_native_reposts_are_skipped_but_quotes_and_replies_capture_own_card():
    user = MonitoredUser(username="alice", x_user_id="42")
    tweet = Tweet(
        tweet_id="123", author_id="42", raw_payload={}, referenced_tweets=[{"type": "retweeted"}]
    )
    with pytest.raises(ValueError, match="Native repost"):
        capture_identity(tweet, user)
    for kind in ["quoted", "replied_to"]:
        tweet.referenced_tweets = [{"type": kind}]
        assert capture_identity(tweet, user) == ("alice", 0)
    tweet.author_id = "other"
    with pytest.raises(ValueError, match="author cannot be resolved"):
        capture_identity(tweet, user)


async def test_repost_outbox_records_reason_without_using_browser(database):
    async with database() as session, session.begin():
        tweet = await session.scalar(select(Tweet).where(Tweet.tweet_id == "123"))
        tweet.referenced_tweets = [{"type": "retweeted", "id": "456"}]
        await session.flush()
        await enqueue_tweet_screenshots(session, ["123"])
    async with database() as session:
        job = await session.get(TweetScreenshot, "123")
        assert job.status == "failed" and job.last_error == RETWEET_ERROR
        assert job.attempts == 0


async def test_api_metadata_download_and_manual_retry(database, settings, monkeypatch):
    monkeypatch.setattr("app.api.routes.tweets.get_settings", lambda: settings)
    async with database() as session:
        result = await request_tweet_screenshot("123", session, object())
        assert result.status == "pending"
        await request_tweet_screenshot("123", session, object())
        assert len(list(await session.scalars(select(TweetScreenshot)))) == 1
    processor = TweetScreenshotProcessor(
        database, settings, SimpleNamespace(capture=AsyncMock(return_value=response()))
    )
    await processor.run_once()
    async with database() as session:
        result = await get_tweet("123", session, object())
        assert result.screenshot.image_url == "/api/v1/tweets/123/screenshot"
        page = await list_tweets(
            session, object(), page=1, page_size=10, username=None, search=None
        )
        assert page.items[0].screenshot.status == "succeeded"
        download = await get_tweet_screenshot("123", session, object())
        assert download.media_type == "image/png"
        assert "private" in download.headers["cache-control"]
        assert await request_tweet_screenshot("123", session, object()) == result.screenshot
        media.delete_screenshot((await session.get(TweetScreenshot, "123")).image_path)
        with pytest.raises(APIError) as failure:
            await get_tweet_screenshot("123", session, object())
        assert failure.value.status_code == 404
        queued = await request_tweet_screenshot("123", session, object())
        assert queued.status == "pending" and queued.attempts == 0


def test_storage_rejects_traversal_and_writes_distinct_claim_files():
    assert media.screenshot_path("../secrets.png") is None
    with pytest.raises(ValueError):
        media.write_screenshot("../123", "a" * 32, png())
    old = media.write_screenshot("123", "a" * 32, png())
    new = media.write_screenshot("123", "b" * 32, png())
    media.delete_screenshot(old)
    assert media.screenshot_path(old) is None
    assert media.screenshot_path(new).read_bytes() == png()
