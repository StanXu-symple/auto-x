from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

os.environ.setdefault("TWS_TELEMETRY", "0")

from twscrape import API  # noqa: E402
from twscrape.accounts_pool import NoAccountError  # noqa: E402
from twscrape.http import HttpStatusError  # noqa: E402

from app.services.twscrape_credentials import TwscrapeCredential
from app.services.x_client import TweetBatch, XAPIError, XRateLimitError, XUser


class TwscrapeClient:
    """Adapt twscrape's cookie-backed GraphQL client to the polling client contract.

    The encrypted MySQL credential remains the source of truth. twscrape requires SQLite,
    so each worker hydrates a mode-0600 temporary runtime database and removes it on close.
    """

    def __init__(
        self,
        credential_provider: Callable[[], Awaitable[TwscrapeCredential]],
        *,
        max_pages: int,
        page_size: int,
    ) -> None:
        self.credential_provider = credential_provider
        self.max_results = max(20, min(1000, max_pages * page_size))
        self._api: API | None = None
        self._credential_version: int | None = None
        self._db_path: Path | None = None
        self._request_lock = asyncio.Lock()

    async def _ensure_api(self, credential: TwscrapeCredential) -> API:
        if self._api is not None and self._credential_version == credential["version"]:
            return self._api
        self._remove_runtime_db()
        descriptor, path = tempfile.mkstemp(prefix="xsentinel-twscrape-", suffix=".sqlite")
        os.close(descriptor)
        os.chmod(path, 0o600)
        api = API(
            path,
            raise_when_no_account=True,
            wait_timeout=30,
            wait_interval=0.5,
        )
        cookie_header = f"auth_token={credential['auth_token']}; ct0={credential['ct0']}"
        try:
            await api.pool.add_account_cookies(credential["account_label"], cookie_header)
        except Exception:
            await asyncio.to_thread(Path(path).unlink, missing_ok=True)
            raise
        self._db_path = Path(path)
        self._api = api
        self._credential_version = credential["version"]
        return api

    def _remove_runtime_db(self) -> None:
        if self._db_path is not None:
            self._db_path.unlink(missing_ok=True)
        self._db_path = None
        self._api = None
        self._credential_version = None

    async def aclose(self) -> None:
        async with self._request_lock:
            self._remove_runtime_db()

    async def lookup_user(self, username: str) -> XUser:
        async with self._request_lock:
            api = await self._api_for_request()
            try:
                user = await api.user_by_login(username)
            except Exception as exc:
                raise self._translate_error(exc) from exc
            if user is None:
                raise XAPIError(f"X user @{username} was not found", status_code=404)
            return XUser(
                id=str(user.id),
                username=str(user.username or username).lower(),
                name=user.displayname or None,
                raw_payload={
                    "id": str(user.id),
                    "username": user.username,
                    "name": user.displayname,
                    "description": user.rawDescription,
                    "profile_image_url": user.profileImageUrl,
                    "source_provider": "twscrape",
                },
            )

    async def get_user_tweets(
        self,
        user_id: str,
        *,
        since_id: str | None = None,
        initial_pagination_token: str | None = None,
        include_replies: bool = True,
        include_retweets: bool = True,
    ) -> TweetBatch:
        del initial_pagination_token
        async with self._request_lock:
            api = await self._api_for_request()
            items: list[Any] = []
            try:
                generator = (
                    api.user_tweets_and_replies(int(user_id), limit=self.max_results)
                    if include_replies
                    else api.user_tweets(int(user_id), limit=self.max_results)
                )
                async for tweet in generator:
                    tweet_id = str(tweet.id)
                    if since_id and _is_not_newer(tweet_id, since_id):
                        continue
                    if not include_replies and tweet.inReplyToTweetId is not None:
                        continue
                    if not include_retweets and tweet.retweetedTweet is not None:
                        continue
                    items.append(tweet)
            except Exception as exc:
                raise self._translate_error(exc) from exc

            payloads = [_tweet_payload(tweet) for tweet in items]
            newest_id = max((str(tweet.id) for tweet in items), key=int, default=None)
            return TweetBatch(
                tweets=payloads,
                newest_id=newest_id,
                next_token=None,
                result_count=len(payloads),
            )

    async def _api_for_request(self) -> API:
        try:
            credential = await self.credential_provider()
            return await self._ensure_api(credential)
        except XAPIError:
            raise
        except Exception as exc:
            raise XAPIError(f"twscrape credential is unavailable: {exc}") from exc

    @staticmethod
    def _translate_error(exc: Exception) -> XAPIError:
        if isinstance(exc, NoAccountError):
            return XRateLimitError(
                "twscrape account is unavailable, invalid, or temporarily rate-limited",
                reset_at=datetime.now(UTC) + timedelta(minutes=15),
            )
        if isinstance(exc, HttpStatusError):
            status_code = exc.response.status_code
            return XAPIError(
                f"twscrape X request returned HTTP {status_code}",
                status_code=status_code,
            )
        return XAPIError(f"twscrape request failed: {type(exc).__name__}: {exc}")


def _is_not_newer(tweet_id: str, since_id: str) -> bool:
    try:
        return int(tweet_id) <= int(since_id)
    except ValueError:
        return tweet_id <= since_id


def _tweet_payload(tweet: Any) -> dict[str, Any]:
    media: list[dict[str, Any]] = []
    for photo in tweet.media.photos:
        media.append({"type": "photo", "url": photo.url})
    for video in tweet.media.videos:
        best = max(video.variants, key=lambda item: item.bitrate, default=None)
        media.append(
            {
                "type": "video",
                "url": best.url if best else None,
                "preview_image_url": video.thumbnailUrl,
                "duration_ms": video.duration,
            }
        )
    for animated in tweet.media.animated:
        media.append(
            {
                "type": "animated_gif",
                "url": animated.videoUrl,
                "preview_image_url": animated.thumbnailUrl,
            }
        )
    referenced: list[dict[str, str]] = []
    if tweet.retweetedTweet is not None:
        referenced.append({"type": "retweeted", "id": str(tweet.retweetedTweet.id)})
    if tweet.quotedTweet is not None:
        referenced.append({"type": "quoted", "id": str(tweet.quotedTweet.id)})
    if tweet.inReplyToTweetId is not None:
        referenced.append({"type": "replied_to", "id": str(tweet.inReplyToTweetId)})
    return {
        "id": str(tweet.id),
        "author_id": str(tweet.user.id),
        "text": tweet.rawContent,
        "lang": tweet.lang,
        "conversation_id": str(tweet.conversationId),
        "created_at": tweet.date.astimezone(UTC).isoformat(),
        "public_metrics": {
            "like_count": tweet.likeCount,
            "retweet_count": tweet.retweetCount,
            "reply_count": tweet.replyCount,
            "quote_count": tweet.quoteCount,
            "bookmark_count": tweet.bookmarkedCount,
            "impression_count": tweet.viewCount or 0,
        },
        "entities": {
            "hashtags": [{"tag": value} for value in tweet.hashtags],
            "mentions": [
                {"username": value.username, "id": str(value.id)}
                for value in tweet.mentionedUsers
            ],
            "urls": [
                {"url": value.tcourl, "expanded_url": value.url, "display_url": value.text}
                for value in tweet.links
            ],
        },
        "attachments": {"media": media} if media else None,
        "referenced_tweets": referenced or None,
        "source_provider": "twscrape",
        "source_url": tweet.url,
    }
