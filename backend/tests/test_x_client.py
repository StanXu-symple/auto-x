from datetime import UTC, datetime

import httpx
import pytest

from app.services.x_client import XAPIError, XClient, XRateLimitError


@pytest.mark.asyncio
async def test_lookup_user_parses_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/2/users/by/username/openai"
        assert "profile_image_url" in request.url.params["user.fields"]
        return httpx.Response(
            200,
            json={"data": {"id": "42", "username": "OpenAI", "name": "OpenAI"}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = XClient("token", client=http_client)
        user = await client.lookup_user("openai")
    assert user.id == "42"
    assert user.username == "openai"
    assert user.name == "OpenAI"


@pytest.mark.asyncio
async def test_tweet_request_uses_since_id_filters_and_pagination() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "12",
                            "text": "new",
                            "attachments": {"media_keys": ["m1"]},
                        }
                    ],
                    "includes": {"media": [{"media_key": "m1"}]},
                    "meta": {"newest_id": "12", "next_token": "next"},
                },
            )
        return httpx.Response(
            200,
            json={"data": [{"id": "11", "text": "older"}], "meta": {}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = XClient("token", client=http_client, max_pages=3)
        batch = await client.get_user_tweets(
            "42",
            since_id="10",
            include_replies=False,
            include_retweets=False,
        )

    assert batch.newest_id == "12"
    assert batch.result_count == 2
    assert requests[0].url.params["since_id"] == "10"
    assert requests[0].url.params["exclude"] == "replies,retweets"
    assert requests[1].url.params["pagination_token"] == "next"
    assert batch.tweets[0]["_includes"]["media"][0]["media_key"] == "m1"
    assert batch.next_token is None


@pytest.mark.asyncio
async def test_max_pages_returns_checkpoint_and_accepts_initial_token() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [{"id": "50", "text": "page"}],
                "meta": {"newest_id": "50", "next_token": "continue-here"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = XClient("token", client=http_client, max_pages=1)
        batch = await client.get_user_tweets(
            "42", since_id="10", initial_pagination_token="start-here"
        )

    assert requests[0].url.params["pagination_token"] == "start-here"
    assert requests[0].url.params["since_id"] == "10"
    assert batch.next_token == "continue-here"


@pytest.mark.asyncio
async def test_includes_are_attached_only_to_related_tweet() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "2", "attachments": {"media_keys": ["m2"]}},
                    {"id": "1", "attachments": {"media_keys": ["m1"]}},
                ],
                "includes": {
                    "media": [
                        {"media_key": "m1", "url": "one"},
                        {"media_key": "m2", "url": "two"},
                    ]
                },
                "meta": {"newest_id": "2"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        batch = await XClient("token", client=http_client).get_user_tweets("42")
    assert batch.tweets[0]["_includes"]["media"] == [{"media_key": "m2", "url": "two"}]
    assert batch.tweets[1]["_includes"]["media"] == [{"media_key": "m1", "url": "one"}]


@pytest.mark.asyncio
async def test_rate_limit_reset_header_is_propagated() -> None:
    reset = 1_900_000_000

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"x-rate-limit-reset": str(reset)},
            json={"title": "Too Many Requests"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = XClient("token", client=http_client)
        with pytest.raises(XRateLimitError) as caught:
            await client.lookup_user("openai")
    assert caught.value.status_code == 429
    assert caught.value.reset_at == datetime.fromtimestamp(reset, tz=UTC)


@pytest.mark.asyncio
async def test_missing_token_is_a_configuration_error() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)) as http_client:
        client = XClient("", client=http_client)
        with pytest.raises(XAPIError, match="Bearer Token"):
            await client.lookup_user("openai")


@pytest.mark.asyncio
async def test_dynamic_token_provider_is_used_for_each_request() -> None:
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(
            200,
            json={"data": {"id": "42", "username": "OpenAI", "name": "OpenAI"}},
        )

    tokens = iter(["first-token", "rotated-token"])

    async def provider() -> str:
        return next(tokens)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = XClient(client=http_client, token_provider=provider)
        await client.lookup_user("openai")
        await client.lookup_user("openai")

    assert seen == ["Bearer first-token", "Bearer rotated-token"]
