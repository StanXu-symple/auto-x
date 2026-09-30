from datetime import datetime
from typing import Any

from app.schemas.common import APIModel
from app.services.tweet_types import TweetType


class TweetOut(APIModel):
    id: int
    tweet_id: str
    monitored_user_id: int
    username: str
    display_name: str | None = None
    author_id: str
    text: str
    tweet_type: TweetType = "original"
    lang: str | None
    conversation_id: str | None
    posted_at: datetime
    like_count: int
    retweet_count: int
    reply_count: int
    quote_count: int
    bookmark_count: int
    impression_count: int
    entities: dict[str, Any] | None
    attachments: dict[str, Any] | None
    referenced_tweets: list[dict[str, Any]] | None
    raw_payload: dict[str, Any] | None = None
    fetched_at: datetime
