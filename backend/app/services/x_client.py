from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx


class XAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class XRateLimitError(XAPIError):
    def __init__(self, message: str, *, reset_at: datetime, payload: Any = None) -> None:
        super().__init__(message, status_code=429, payload=payload)
        self.reset_at = reset_at


@dataclass(slots=True)
class XUser:
    id: str
    username: str
    name: str | None
    raw_payload: dict[str, Any]


@dataclass(slots=True)
class TweetBatch:
    tweets: list[dict[str, Any]]
    newest_id: str | None
    next_token: str | None
    result_count: int


class XClient:
    """Minimal X API v2 adapter with explicit rate-limit propagation."""

    def __init__(
        self,
        bearer_token: str = "",
        *,
        base_url: str = "https://api.x.com/2",
        timeout_seconds: float = 20.0,
        max_pages: int = 5,
        page_size: int = 100,
        client: httpx.AsyncClient | None = None,
        token_provider: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self.bearer_token = bearer_token
        self.token_provider = token_provider
        self.base_url = base_url.rstrip("/")
        self.max_pages = max_pages
        self.page_size = page_size
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "User-Agent": "X-Sentinel/1.0",
                "Accept": "application/json",
            },
        )

    async def __aenter__(self) -> XClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        bearer_token = self.bearer_token
        if self.token_provider is not None:
            try:
                bearer_token = await self.token_provider()
            except Exception as exc:
                raise XAPIError(f"X credential is unavailable: {exc}") from exc
        if not bearer_token:
            raise XAPIError("X Bearer Token is not configured")
        try:
            response = await self._client.get(
                f"{self.base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {bearer_token}"},
            )
        except httpx.TimeoutException as exc:
            raise XAPIError("X API request timed out") from exc
        except httpx.HTTPError as exc:
            raise XAPIError(f"X API transport error: {exc}") from exc

        try:
            payload: Any = response.json()
        except ValueError:
            payload = {"body": response.text[:1000]}

        if response.status_code == 429:
            now = datetime.now(UTC)
            reset_at = now + timedelta(minutes=15)
            if reset_header := response.headers.get("x-rate-limit-reset"):
                try:
                    reset_at = datetime.fromtimestamp(int(reset_header), tz=UTC)
                except (TypeError, ValueError, OSError):
                    pass
            elif retry_after := response.headers.get("retry-after"):
                try:
                    reset_at = now + timedelta(seconds=max(1, int(retry_after)))
                except (TypeError, ValueError):
                    pass
            raise XRateLimitError(
                self._error_message(payload, "X API rate limit exceeded"),
                reset_at=reset_at,
                payload=payload,
            )
        if response.is_error:
            raise XAPIError(
                self._error_message(payload, f"X API returned HTTP {response.status_code}"),
                status_code=response.status_code,
                payload=payload,
            )
        if not isinstance(payload, dict):
            raise XAPIError(
                "X API returned an invalid JSON object", status_code=response.status_code
            )
        return payload

    @staticmethod
    def _error_message(payload: Any, fallback: str) -> str:
        if isinstance(payload, dict):
            if isinstance(payload.get("detail"), str):
                return payload["detail"]
            errors = payload.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                return str(errors[0].get("detail") or errors[0].get("message") or fallback)
            if isinstance(payload.get("title"), str):
                return payload["title"]
        return fallback

    async def lookup_user(self, username: str) -> XUser:
        payload = await self._request(
            f"/users/by/username/{username}",
            params={"user.fields": "id,name,username,created_at,description,profile_image_url"},
        )
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("id"):
            raise XAPIError(f"X user @{username} was not found", status_code=404, payload=payload)
        return XUser(
            id=str(data["id"]),
            username=str(data.get("username") or username).lower(),
            name=data.get("name"),
            raw_payload=data,
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
        tweets: list[dict[str, Any]] = []
        next_token = initial_pagination_token
        newest_id: str | None = None
        for _ in range(self.max_pages):
            params: dict[str, Any] = {
                "max_results": self.page_size,
                "tweet.fields": (
                    "id,text,author_id,created_at,public_metrics,entities,attachments,"
                    "referenced_tweets,lang,conversation_id"
                ),
                "expansions": "attachments.media_keys,attachments.poll_ids,referenced_tweets.id",
                "media.fields": "media_key,type,url,preview_image_url,width,height,duration_ms",
                "poll.fields": "id,options,duration_minutes,end_datetime,voting_status",
            }
            if since_id:
                params["since_id"] = since_id
            if next_token:
                params["pagination_token"] = next_token
            exclude: list[str] = []
            if not include_replies:
                exclude.append("replies")
            if not include_retweets:
                exclude.append("retweets")
            if exclude:
                params["exclude"] = ",".join(exclude)

            payload = await self._request(f"/users/{user_id}/tweets", params=params)
            page = payload.get("data") or []
            if not isinstance(page, list):
                raise XAPIError("X API tweets response has an invalid data field", payload=payload)
            for tweet in page:
                if isinstance(tweet, dict) and tweet.get("id"):
                    enriched = dict(tweet)
                    relevant_includes = self._relevant_includes(tweet, payload.get("includes"))
                    if relevant_includes:
                        enriched["_includes"] = relevant_includes
                    tweets.append(enriched)
            meta = payload.get("meta") or {}
            if newest_id is None and meta.get("newest_id"):
                newest_id = str(meta["newest_id"])
            next_token = meta.get("next_token")
            if not next_token:
                break

        if newest_id is None and tweets:
            newest_id = max((str(tweet["id"]) for tweet in tweets), key=int)
        return TweetBatch(
            tweets=tweets,
            newest_id=newest_id,
            next_token=str(next_token) if next_token else None,
            result_count=len(tweets),
        )

    @staticmethod
    def _relevant_includes(tweet: dict[str, Any], includes: Any) -> dict[str, list[dict[str, Any]]]:
        if not isinstance(includes, dict):
            return {}
        attachments = tweet.get("attachments") or {}
        media_keys = {str(item) for item in attachments.get("media_keys", [])}
        poll_ids = {str(item) for item in attachments.get("poll_ids", [])}
        referenced_ids = {
            str(item.get("id"))
            for item in (tweet.get("referenced_tweets") or [])
            if isinstance(item, dict) and item.get("id")
        }
        selected: dict[str, list[dict[str, Any]]] = {}
        media = [
            item
            for item in (includes.get("media") or [])
            if isinstance(item, dict) and str(item.get("media_key")) in media_keys
        ]
        polls = [
            item
            for item in (includes.get("polls") or [])
            if isinstance(item, dict) and str(item.get("id")) in poll_ids
        ]
        references = [
            item
            for item in (includes.get("tweets") or [])
            if isinstance(item, dict) and str(item.get("id")) in referenced_ids
        ]
        if media:
            selected["media"] = media
        if polls:
            selected["polls"] = polls
        if references:
            selected["tweets"] = references
        return selected
