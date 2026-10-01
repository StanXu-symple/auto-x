"""Durable screenshot outbox; ingestion only writes rows, a separate loop executes them."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.monitored_user import MonitoredUser
from app.models.tweet import Tweet
from app.models.tweet_screenshot import TweetScreenshot
from app.services.tweet_screenshot_client import ScreenshotBusyError
from app.services.tweet_screenshot_media import (
    ValidatedScreenshot,
    delete_screenshot,
    validate_screenshot,
    write_screenshot,
)

logger = logging.getLogger(__name__)
RETWEET_ERROR = "Native repost has no independently verifiable post card; screenshot skipped"


def capture_identity(tweet: Tweet, user: MonitoredUser) -> tuple[str, int]:
    if any(item.get("type") == "retweeted" for item in (tweet.referenced_tweets or [])):
        raise ValueError(RETWEET_ERROR)
    username = user.username
    source_url = (tweet.raw_payload or {}).get("source_url")
    if source_url:
        url = urlsplit(str(source_url))
        parts = url.path.strip("/").split("/")
        if (
            url.scheme == "https"
            and url.netloc.lower() in ("x.com", "twitter.com")
            and len(parts) == 3
            and parts[1] == "status"
            and parts[2] == tweet.tweet_id
        ):
            username = parts[0]
    elif user.x_user_id and tweet.author_id != user.x_user_id:
        raise ValueError("Post author cannot be resolved to the monitored account")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", username):
        raise ValueError("Post author username is invalid")
    attachments = tweet.attachments or {}
    media = attachments.get("media") or attachments.get("media_keys") or []
    return username, len(media) if isinstance(media, list) else 0


async def enqueue_tweet_screenshots(session: AsyncSession, tweet_ids: list[str]) -> int:
    """Called inside the tweet insert transaction; duplicates never reset existing jobs."""
    if not tweet_ids:
        return 0
    tweets = list(await session.scalars(select(Tweet).where(Tweet.tweet_id.in_(tweet_ids))))
    now = datetime.now(UTC)
    rows = []
    for tweet in tweets:
        native_repost = any(
            reference.get("type") == "retweeted" for reference in (tweet.referenced_tweets or [])
        )
        rows.append(
            {
                "tweet_id": tweet.tweet_id,
                "status": "failed" if native_repost else "pending",
                "attempts": 0,
                "next_attempt_at": now,
                "last_error": RETWEET_ERROR if native_repost else None,
                "created_at": now,
                "updated_at": now,
            }
        )
    if not rows:
        return 0
    dialect = session.get_bind().dialect.name
    insert = postgres_insert if dialect == "postgresql" else sqlite_insert
    result = await session.execute(
        insert(TweetScreenshot)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["tweet_id"])
        .returning(TweetScreenshot.tweet_id)
    )
    return len(result.all())


@dataclass(frozen=True)
class ScreenshotClaim:
    tweet_id: str
    token: str
    attempt: int
    username: str
    expected_text: str
    expected_media_count: int


class TweetScreenshotProcessor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        client: object,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.client = client
        # A cancelled caller leaves enough time for the remote browser operation to
        # settle before another worker may claim the row after a process crash.
        self.lease_seconds = settings.camoufox_job_timeout_seconds + 150

    async def claim_one(self) -> ScreenshotClaim | None:
        now = datetime.now(UTC)
        due = or_(
            and_(
                TweetScreenshot.status == "pending", TweetScreenshot.next_attempt_at <= now
            ),
            and_(TweetScreenshot.status == "running", TweetScreenshot.lease_until <= now),
        )
        async with self.session_factory() as session, session.begin():
            row = await session.scalar(
                select(TweetScreenshot)
                .join(Tweet, Tweet.tweet_id == TweetScreenshot.tweet_id)
                .where(due)
                # Initial history imports must not keep a newly published post
                # behind hours of older capture work on a single browser.
                .order_by(Tweet.posted_at.desc(), TweetScreenshot.next_attempt_at)
                .limit(1)
                .with_for_update(skip_locked=True, of=TweetScreenshot)
            )
            if row is None:
                return None
            if row.attempts >= self.settings.tweet_screenshot_max_attempts:
                row.status = "failed"
                row.last_error = row.last_error or "Screenshot processing lease expired"
                row.lease_token = None
                row.lease_until = None
                return None
            tweet = await session.scalar(select(Tweet).where(Tweet.tweet_id == row.tweet_id))
            user = await session.get(MonitoredUser, tweet.monitored_user_id) if tweet else None
            if tweet is None or user is None:
                row.status = "failed"
                row.last_error = "Screenshot source post is no longer available"
                row.lease_token = None
                row.lease_until = None
                return None
            try:
                username, media_count = capture_identity(tweet, user)
            except ValueError as exc:
                row.status = "failed"
                row.last_error = str(exc)
                row.lease_token = None
                row.lease_until = None
                return None
            token = uuid.uuid4().hex
            # Conditional update fences claims even on databases lacking SKIP LOCKED.
            result = await session.execute(
                update(TweetScreenshot)
                .where(TweetScreenshot.tweet_id == row.tweet_id, due)
                .values(
                    status="running",
                    attempts=row.attempts + 1,
                    lease_token=token,
                    lease_until=now + timedelta(seconds=self.lease_seconds),
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if not result.rowcount:
                return None
            return ScreenshotClaim(
                row.tweet_id, token, row.attempts + 1, username, tweet.text, media_count
            )

    async def _renew(self, claim: ScreenshotClaim, lost: asyncio.Event) -> None:
        while True:
            await asyncio.sleep(20)
            now = datetime.now(UTC)
            try:
                async with self.session_factory() as session, session.begin():
                    result = await session.execute(
                        update(TweetScreenshot)
                        .where(
                            TweetScreenshot.tweet_id == claim.tweet_id,
                            TweetScreenshot.status == "running",
                            TweetScreenshot.lease_token == claim.token,
                            TweetScreenshot.lease_until > now,
                        )
                        .values(lease_until=now + timedelta(seconds=self.lease_seconds))
                    )
                    if not result.rowcount:
                        lost.set()
                        return
            except asyncio.CancelledError:
                raise
            except Exception:
                lost.set()
                logger.exception("Screenshot lease renewal failed")
                return

    async def _finish(
        self,
        claim: ScreenshotClaim,
        *,
        capture: ValidatedScreenshot | None = None,
        image_path: str | None = None,
        error: str | None = None,
    ) -> bool:
        now = datetime.now(UTC)
        values = {"lease_token": None, "lease_until": None, "updated_at": now}
        if capture is not None:
            values.update(
                status="succeeded",
                image_path=image_path,
                sha256=capture.sha256,
                width=capture.width,
                height=capture.height,
                canonical_url=capture.canonical_url,
                author_username=claim.username,
                captured_at=capture.captured_at,
                last_error=None,
            )
        else:
            values.update(
                status=(
                    "failed"
                    if claim.attempt >= self.settings.tweet_screenshot_max_attempts
                    else "pending"
                ),
                last_error=(error or "Screenshot capture failed")[:2000],
                next_attempt_at=now
                + timedelta(seconds=self.settings.tweet_screenshot_retry_seconds * claim.attempt),
            )
        async with self.session_factory() as session, session.begin():
            result = await session.execute(
                update(TweetScreenshot)
                .where(
                    TweetScreenshot.tweet_id == claim.tweet_id,
                    TweetScreenshot.status == "running",
                    TweetScreenshot.lease_token == claim.token,
                    TweetScreenshot.lease_until > now,
                )
                .values(**values)
            )
            return bool(result.rowcount)

    async def _reschedule_busy(self, claim: ScreenshotClaim, error: str) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            await session.execute(
                update(TweetScreenshot)
                .where(
                    TweetScreenshot.tweet_id == claim.tweet_id,
                    TweetScreenshot.status == "running",
                    TweetScreenshot.lease_token == claim.token,
                    TweetScreenshot.lease_until > now,
                )
                .values(
                    status="pending",
                    attempts=TweetScreenshot.attempts - 1,
                    lease_token=None,
                    lease_until=None,
                    last_error=error[:2000],
                    next_attempt_at=now
                    + timedelta(seconds=self.settings.tweet_screenshot_retry_seconds),
                    updated_at=now,
                )
            )

    async def _discard_unreferenced(self, image_path: str) -> None:
        # A cancellation may arrive just after the success transaction committed;
        # never remove a PNG that the durable row already exposes to API readers.
        async with self.session_factory() as session:
            referenced = await session.scalar(
                select(TweetScreenshot.tweet_id).where(
                    TweetScreenshot.image_path == image_path,
                    TweetScreenshot.status == "succeeded",
                )
            )
        if referenced is None:
            await asyncio.to_thread(delete_screenshot, image_path)

    async def run_once(self) -> bool:
        claim = await self.claim_one()
        if claim is None:
            return False
        lost = asyncio.Event()
        renewal = asyncio.create_task(self._renew(claim, lost))
        image_path = None
        try:
            async with asyncio.timeout(self.settings.camoufox_job_timeout_seconds + 90):
                data = await self.client.capture(
                    tweet_id=claim.tweet_id,
                    username=claim.username,
                    expected_text=claim.expected_text,
                    expected_media_count=claim.expected_media_count,
                    job_id=claim.token,
                )
            capture = await asyncio.to_thread(
                validate_screenshot, data, tweet_id=claim.tweet_id, username=claim.username
            )
            if lost.is_set():
                return True
            storage_task = asyncio.create_task(
                asyncio.to_thread(write_screenshot, claim.tweet_id, claim.token, capture.png)
            )
            try:
                image_path = await asyncio.shield(storage_task)
            except asyncio.CancelledError:
                # Filesystem threads cannot be cancelled; finish the atomic write
                # before cleanup so no late orphan appears after shutdown.
                image_path = await storage_task
                raise
            if not await self._finish(claim, capture=capture, image_path=image_path):
                await self._discard_unreferenced(image_path)
        except asyncio.CancelledError:
            # Do not immediately release this lease: the remote synchronous browser
            # job may still be using its page after the HTTP caller is cancelled.
            if image_path:
                await self._discard_unreferenced(image_path)
            raise
        except ScreenshotBusyError as exc:
            await self._reschedule_busy(claim, str(exc))
        except Exception as exc:
            if image_path:
                await self._discard_unreferenced(image_path)
            await self._finish(claim, error=str(exc))
            logger.warning(
                "X post screenshot failed",
                extra={"tweet_id": claim.tweet_id, "attempt": claim.attempt, "error": str(exc)},
            )
        finally:
            renewal.cancel()
            with suppress(asyncio.CancelledError):
                await renewal
        return True
