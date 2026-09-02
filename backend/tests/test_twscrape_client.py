from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.twscrape_client import _is_not_newer, _tweet_payload


def test_twscrape_payload_matches_poller_contract() -> None:
    tweet = SimpleNamespace(
        id=201,
        user=SimpleNamespace(id=99),
        rawContent="hello #x",
        lang="en",
        conversationId=200,
        date=datetime(2026, 8, 31, 1, 2, tzinfo=UTC),
        likeCount=4,
        retweetCount=3,
        replyCount=2,
        quoteCount=1,
        bookmarkedCount=5,
        viewCount=100,
        hashtags=["x"],
        mentionedUsers=[SimpleNamespace(username="openai", id=42)],
        links=[
            SimpleNamespace(
                tcourl="https://t.co/a", url="https://example.com", text="example.com"
            )
        ],
        media=SimpleNamespace(photos=[], videos=[], animated=[]),
        retweetedTweet=None,
        quotedTweet=None,
        inReplyToTweetId=199,
        url="https://x.com/test/status/201",
    )

    payload = _tweet_payload(tweet)

    assert payload["id"] == "201"
    assert payload["author_id"] == "99"
    assert payload["source_provider"] == "twscrape"
    assert payload["public_metrics"]["impression_count"] == 100
    assert payload["referenced_tweets"] == [{"type": "replied_to", "id": "199"}]


def test_since_id_comparison_handles_x_snowflakes() -> None:
    assert _is_not_newer("100", "100") is True
    assert _is_not_newer("99", "100") is True
    assert _is_not_newer("101", "100") is False
