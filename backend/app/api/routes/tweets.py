from datetime import datetime

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.api.deps import CurrentAdmin, DbSession
from app.api.errors import APIError
from app.core.config import get_settings
from app.core.time import to_database_utc
from app.db.base import utcnow
from app.models.monitored_user import MonitoredUser
from app.models.tweet import Tweet
from app.models.tweet_screenshot import TweetScreenshot
from app.schemas.common import Page
from app.schemas.tweet import TweetOut, TweetScreenshotOut
from app.services.tweet_screenshot_media import screenshot_path
from app.services.tweet_screenshots import capture_identity, enqueue_tweet_screenshots
from app.services.tweet_types import TweetType

router = APIRouter(tags=["Tweets"])


def _tweet_out(
    tweet: Tweet,
    username: str,
    *,
    include_raw: bool,
    display_name: str | None = None,
    screenshot: TweetScreenshot | None = None,
) -> TweetOut:
    return TweetOut(
        id=tweet.id,
        tweet_id=tweet.tweet_id,
        monitored_user_id=tweet.monitored_user_id,
        username=username,
        display_name=display_name,
        author_id=tweet.author_id,
        text=tweet.text,
        tweet_type=tweet.tweet_type,
        lang=tweet.lang,
        conversation_id=tweet.conversation_id,
        posted_at=tweet.posted_at,
        like_count=tweet.like_count,
        retweet_count=tweet.retweet_count,
        reply_count=tweet.reply_count,
        quote_count=tweet.quote_count,
        bookmark_count=tweet.bookmark_count,
        impression_count=tweet.impression_count,
        entities=tweet.entities,
        attachments=tweet.attachments,
        referenced_tweets=tweet.referenced_tweets,
        raw_payload=tweet.raw_payload if include_raw else None,
        fetched_at=tweet.fetched_at,
        screenshot=_screenshot_out(screenshot) if screenshot else None,
    )


def _screenshot_out(screenshot: TweetScreenshot) -> TweetScreenshotOut:
    return TweetScreenshotOut(
        status=screenshot.status,
        attempts=screenshot.attempts,
        last_error=screenshot.last_error,
        next_attempt_at=screenshot.next_attempt_at,
        captured_at=screenshot.captured_at,
        author_username=screenshot.author_username,
        canonical_url=screenshot.canonical_url,
        width=screenshot.width,
        height=screenshot.height,
        sha256=screenshot.sha256,
        image_url=(
            f"{get_settings().api_prefix}/tweets/{screenshot.tweet_id}/screenshot"
            if screenshot.status == "succeeded" and screenshot.image_path
            else None
        ),
    )


@router.get("", response_model=Page[TweetOut])
async def list_tweets(
    db: DbSession,
    _: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    monitored_user_id: int | None = None,
    username: str | None = Query(default=None, max_length=64),
    search: str | None = Query(default=None, max_length=200),
    posted_after: datetime | None = None,
    posted_before: datetime | None = None,
    include_raw: bool = False,
    tweet_type: TweetType | None = None,
) -> Page[TweetOut]:
    conditions = []
    if tweet_type is not None:
        conditions.append(Tweet.tweet_type == tweet_type)
    if monitored_user_id is not None:
        conditions.append(Tweet.monitored_user_id == monitored_user_id)
    if username:
        conditions.append(MonitoredUser.username == username.strip().lstrip("@").lower())
    if search:
        conditions.append(Tweet.text.contains(search.strip()))
    if posted_after:
        conditions.append(Tweet.posted_at >= to_database_utc(posted_after))
    if posted_before:
        conditions.append(Tweet.posted_at <= to_database_utc(posted_before))

    joined = Tweet.__table__.join(
        MonitoredUser.__table__, Tweet.monitored_user_id == MonitoredUser.id
    )
    total = int(
        await db.scalar(select(func.count(Tweet.id)).select_from(joined).where(*conditions)) or 0
    )
    rows = (
        await db.execute(
            select(Tweet, MonitoredUser.username, MonitoredUser.display_name, TweetScreenshot)
            .select_from(joined)
            .outerjoin(TweetScreenshot, Tweet.tweet_id == TweetScreenshot.tweet_id)
            .where(*conditions)
            .order_by(Tweet.posted_at.desc(), Tweet.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return Page(
        items=[
            _tweet_out(
                tweet,
                handle,
                include_raw=include_raw,
                display_name=display_name,
                screenshot=screenshot,
            )
            for tweet, handle, display_name, screenshot in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{tweet_id}", response_model=TweetOut)
async def get_tweet(
    tweet_id: str,
    db: DbSession,
    _: CurrentAdmin,
    include_raw: bool = False,
) -> TweetOut:
    row = (
        await db.execute(
            select(Tweet, MonitoredUser.username, MonitoredUser.display_name, TweetScreenshot)
            .join(MonitoredUser, Tweet.monitored_user_id == MonitoredUser.id)
            .outerjoin(TweetScreenshot, Tweet.tweet_id == TweetScreenshot.tweet_id)
            .where(Tweet.tweet_id == tweet_id)
        )
    ).one_or_none()
    if row is None:
        raise APIError(404, "tweet_not_found", "Tweet was not found")
    return _tweet_out(
        row[0], row[1], include_raw=include_raw, display_name=row[2], screenshot=row[3]
    )


@router.get("/{tweet_id}/screenshot", response_class=FileResponse)
async def get_tweet_screenshot(tweet_id: str, db: DbSession, _: CurrentAdmin) -> FileResponse:
    screenshot = await db.get(TweetScreenshot, tweet_id)
    if screenshot is None or screenshot.status != "succeeded":
        raise APIError(404, "screenshot_not_available", "A successful screenshot is not available")
    path = screenshot_path(screenshot.image_path)
    if path is None:
        raise APIError(404, "screenshot_file_not_found", "The screenshot image file is unavailable")
    return FileResponse(
        path,
        media_type="image/png",
        filename=f"x-{tweet_id}.png",
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store", "ETag": f'"{screenshot.sha256}"'},
    )


@router.post("/{tweet_id}/screenshot", response_model=TweetScreenshotOut, status_code=202)
async def request_tweet_screenshot(
    tweet_id: str, db: DbSession, _: CurrentAdmin
) -> TweetScreenshotOut:
    if not get_settings().tweet_screenshot_enabled:
        raise APIError(503, "screenshot_disabled", "X post screenshots are disabled")
    row = (
        await db.execute(
            select(Tweet, MonitoredUser)
            .join(MonitoredUser, Tweet.monitored_user_id == MonitoredUser.id)
            .where(Tweet.tweet_id == tweet_id)
        )
    ).one_or_none()
    if row is None:
        raise APIError(404, "tweet_not_found", "Tweet was not found")
    try:
        capture_identity(row[0], row[1])
    except ValueError as exc:
        raise APIError(422, "screenshot_unsupported", str(exc)) from exc
    await enqueue_tweet_screenshots(db, [tweet_id])
    screenshot = await db.get(TweetScreenshot, tweet_id, with_for_update=True)
    if screenshot.status == "failed" or (
        screenshot.status == "succeeded" and screenshot_path(screenshot.image_path) is None
    ):
        screenshot.status = "pending"
        screenshot.attempts = 0
        screenshot.next_attempt_at = utcnow()
        screenshot.last_error = None
        screenshot.lease_token = None
        screenshot.lease_until = None
    await db.commit()
    return _screenshot_out(screenshot)
