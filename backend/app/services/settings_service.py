from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.setting import AppSetting

POLLING_KEY = "polling"


async def get_polling_settings(
    session: AsyncSession,
    config: Settings,
    *,
    for_update: bool = False,
) -> dict[str, int]:
    if for_update:
        row = await session.scalar(
            select(AppSetting).where(AppSetting.key == POLLING_KEY).with_for_update()
        )
    else:
        row = await session.get(AppSetting, POLLING_KEY)
    value = row.value if row else {}
    return {
        "global_poll_interval_seconds": int(
            value.get("global_poll_interval_seconds", config.default_poll_interval_seconds)
        ),
        "max_concurrency": int(value.get("max_concurrency", config.worker_max_concurrency)),
    }


def effective_interval(user_interval: int | None, polling_settings: dict[str, int]) -> int:
    return user_interval or polling_settings["global_poll_interval_seconds"]
