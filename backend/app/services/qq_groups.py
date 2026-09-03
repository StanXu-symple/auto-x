from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import as_utc
from app.models.qq import QQBotAccount, QQJoinedGroup


async def record_group_presence(
    db: AsyncSession, *, app_id: str, group_openid: str,
    is_joined: bool, event_at: datetime,
) -> bool:
    """Record verified platform events; caller owns the transaction.

    Lock the bot before looking up the group to serialize concurrent first
    observations too. Delayed or duplicate events cannot resurrect a left group.
    Returns whether a new observation was recorded.
    """
    bot = await db.scalar(
        select(QQBotAccount)
        .where(QQBotAccount.app_id == app_id, QQBotAccount.is_enabled.is_(True))
        .with_for_update()
    )
    if bot is None:
        return False
    row = await db.scalar(
        select(QQJoinedGroup).where(
            QQJoinedGroup.bot_id == bot.id,
            QQJoinedGroup.app_id == app_id,
            QQJoinedGroup.group_openid == group_openid,
        ).with_for_update()
    )
    timestamp = as_utc(event_at)
    if row is None:
        db.add(QQJoinedGroup(
            bot_id=bot.id, app_id=app_id, group_openid=group_openid,
            is_joined=is_joined, last_event_at=timestamp,
        ))
        return True
    previous = as_utc(row.last_event_at)
    # At equal timestamps prefer removal, including delayed group messages.
    if timestamp < previous or (timestamp == previous and (is_joined or not row.is_joined)):
        return False
    row.is_joined = is_joined
    row.last_event_at = timestamp
    return True
