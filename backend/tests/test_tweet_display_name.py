from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.routes.tweets import get_tweet, list_tweets
from app.models.tweet import Tweet


def sample_tweet():
    return Tweet(
        id=1,
        tweet_id="123",
        monitored_user_id=2,
        author_id="42",
        text="Hello",
        tweet_type="original",
        posted_at=datetime.now(UTC),
        fetched_at=datetime.now(UTC),
        like_count=0,
        retweet_count=0,
        reply_count=0,
        quote_count=0,
        bookmark_count=0,
        impression_count=0,
        raw_payload={"private": "data"},
    )


@pytest.mark.parametrize("display_name", ["主昵称", None, ""])
async def test_list_returns_nickname_and_handle(display_name):
    db = AsyncMock()
    db.scalar.return_value = 1
    db.execute.return_value = SimpleNamespace(
        all=lambda: [(sample_tweet(), "openai", display_name, None)]
    )
    result = await list_tweets(db, object(), page=1, page_size=15, username=None, search=None)
    assert result.items[0].username == "openai"
    assert result.items[0].display_name == display_name
    assert result.items[0].raw_payload is None
    assert "monitored_users.display_name" in str(db.execute.call_args.args[0])


async def test_detail_returns_nickname_without_replacing_handle():
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(
        one_or_none=lambda: (sample_tweet(), "openai", "主昵称", None)
    )
    result = await get_tweet("123", db, object(), include_raw=True)
    assert result.display_name == "主昵称"
    assert result.username == "openai"
    assert result.raw_payload == {"private": "data"}
