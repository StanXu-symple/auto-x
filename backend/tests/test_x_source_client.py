import pytest

from app.services.x_client import TweetBatch, XUser
from app.services.x_source_client import XSourceClient


class FakeClient:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[str, str]] = []

    async def lookup_user(self, username: str) -> XUser:
        self.calls.append(("lookup", username))
        return XUser(id=self.name, username=username, name=None, raw_payload={})

    async def get_user_tweets(self, user_id: str, **_kwargs) -> TweetBatch:
        self.calls.append(("tweets", user_id))
        return TweetBatch(tweets=[], newest_id=None, next_token=None, result_count=0)


@pytest.mark.asyncio
async def test_source_client_routes_every_call_using_current_provider() -> None:
    source = object.__new__(XSourceClient)
    source.official = FakeClient("official")
    source.twscrape = FakeClient("twscrape")
    providers = iter(["official_api", "twscrape"])

    async def provider() -> str:
        return next(providers)

    source._provider = provider

    user = await source.lookup_user("first")
    batch = await source.get_user_tweets("42", include_replies=True)

    assert user.id == "official"
    assert batch.result_count == 0
    assert source.official.calls == [("lookup", "first")]
    assert source.twscrape.calls == [("tweets", "42")]
