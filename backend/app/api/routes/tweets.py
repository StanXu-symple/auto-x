from datetime import datetime

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import CurrentAdmin, DbSession
from app.api.errors import APIError
from app.core.time import to_mysql_utc_naive
from app.models.monitored_user import MonitoredUser
from app.models.tweet import Tweet
from app.schemas.common import Page
from app.schemas.tweet import TweetOut

router = APIRouter(tags=["Tweets"])


def _tweet_out(tweet: Tweet, username: str, *, include_raw: bool) -> TweetOut:
    return TweetOut(
        id=tweet.id,
        tweet_id=tweet.tweet_id,
        monitored_user_id=tweet.monitored_user_id,
        username=username,
        author_id=tweet.author_id,
        text=tweet.text,
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
) -> Page[TweetOut]:
    conditions = []
    if monitored_user_id is not None:
        conditions.append(Tweet.monitored_user_id == monitored_user_id)
    if username:
        conditions.append(MonitoredUser.username == username.strip().lstrip("@").lower())
    if search:
        conditions.append(Tweet.text.contains(search.strip()))
    if posted_after:
        conditions.append(Tweet.posted_at >= to_mysql_utc_naive(posted_after))
    if posted_before:
        conditions.append(Tweet.posted_at <= to_mysql_utc_naive(posted_before))

    joined = Tweet.__table__.join(
        MonitoredUser.__table__, Tweet.monitored_user_id == MonitoredUser.id
    )
    total = int(
        await db.scalar(select(func.count(Tweet.id)).select_from(joined).where(*conditions)) or 0
    )
    rows = (
        await db.execute(
            select(Tweet, MonitoredUser.username)
            .select_from(joined)
            .where(*conditions)
            .order_by(Tweet.posted_at.desc(), Tweet.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return Page(
        items=[_tweet_out(tweet, handle, include_raw=include_raw) for tweet, handle in rows],
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
            select(Tweet, MonitoredUser.username)
            .join(MonitoredUser, Tweet.monitored_user_id == MonitoredUser.id)
            .where(Tweet.tweet_id == tweet_id)
        )
    ).one_or_none()
    if row is None:
        raise APIError(404, "tweet_not_found", "Tweet was not found")
    return _tweet_out(row[0], row[1], include_raw=include_raw)
