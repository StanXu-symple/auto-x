from __future__ import annotations

from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.services.settings_service import get_x_source_provider
from app.services.twscrape_client import TwscrapeClient
from app.services.twscrape_credentials import get_twscrape_credentials
from app.services.x_client import TweetBatch, XClient, XUser
from app.services.x_credentials import get_bearer_token


class XSourceClient:
    """Dynamically route each polling operation through the selected X source."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        redis: Redis,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.redis = redis
        self.settings = settings
        self.official = XClient(
            base_url=settings.x_api_base_url,
            timeout_seconds=settings.x_request_timeout_seconds,
            max_pages=settings.x_max_pages_per_poll,
            page_size=settings.x_page_size,
            token_provider=self._get_bearer_token,
        )
        self.twscrape = TwscrapeClient(
            self._get_twscrape_credentials,
            max_pages=settings.x_max_pages_per_poll,
            page_size=settings.x_page_size,
        )

    async def _provider(self) -> str:
        async with self.session_factory() as session:
            return await get_x_source_provider(session)

    async def _get_bearer_token(self) -> str:
        async with self.session_factory() as session:
            return await get_bearer_token(session, self.redis, self.settings)

    async def _get_twscrape_credentials(self):
        async with self.session_factory() as session:
            return await get_twscrape_credentials(session, self.redis, self.settings)

    async def lookup_user(self, username: str) -> XUser:
        client = self.twscrape if await self._provider() == "twscrape" else self.official
        return await client.lookup_user(username)

    async def get_user_tweets(self, user_id: str, **kwargs: Any) -> TweetBatch:
        client = self.twscrape if await self._provider() == "twscrape" else self.official
        return await client.get_user_tweets(user_id, **kwargs)

    async def aclose(self) -> None:
        await self.official.aclose()
        await self.twscrape.aclose()
