"""Shared classification for normalized X API and twscrape payloads."""

from typing import Any, Literal

TweetType = Literal["original", "reply", "retweet"]
ListenMode = Literal["all", "original", "reply", "retweet"]


def classify_tweet(payload: dict[str, Any]) -> TweetType:
    references = payload.get("referenced_tweets")
    kinds = (
        {item.get("type") for item in references if isinstance(item, dict)}
        if isinstance(references, list)
        else set()
    )
    # A repost of a reply belongs to retweets; a reply with a quote remains a reply.
    if "retweeted" in kinds:
        return "retweet"
    if "replied_to" in kinds:
        return "reply"
    if "quoted" in kinds:
        return "retweet"
    return "original"


def target_accepts_tweet(target: Any, tweet: Any) -> bool:
    mode = getattr(target, "listen_mode", None) or "all"
    return mode == "all" or mode == getattr(tweet, "tweet_type", None)
