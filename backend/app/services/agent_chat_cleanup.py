"""Conversation deletion and bounded retention cleanup, hosted by polling worker."""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_plane.nacos_config import AsyncNacosConfigClient
from app.core.config import Settings, _flatten_config
from app.db.base import utcnow
from app.models.agent_chat import AgentConversation, AgentTurn
from app.services.agent_chat_worker import event_stream_key

logger = logging.getLogger(__name__)
CLEANUP_BATCH_SIZE = 100


async def delete_conversation_rows(db: AsyncSession, ids: list[str]) -> list[str]:
    """Caller holds conversation locks, serializing sends/retries/saves/deletes."""
    if not ids:
        return []
    turn_ids = list(
        await db.scalars(
            delete(AgentTurn).where(AgentTurn.conversation_id.in_(ids)).returning(AgentTurn.id)
        )
    )
    await db.execute(delete(AgentConversation).where(AgentConversation.id.in_(ids)))
    return turn_ids


async def delete_event_streams(redis, turn_ids: list[str]) -> None:
    if redis is None or not turn_ids:
        return
    try:
        for offset in range(0, len(turn_ids), 500):
            await redis.delete(*(event_stream_key(id_) for id_ in turn_ids[offset : offset + 500]))
    except Exception as exc:
        # SQL deletion has committed. Replay buffers also have a 24-hour TTL.
        logger.warning("Agent chat stream cleanup failed", extra={"error_type": type(exc).__name__})


class AgentChatCleanup:
    def __init__(self, settings: Settings, session_factory, redis, stop_event: asyncio.Event):
        self.settings = settings
        self.session_factory = session_factory
        self.redis = redis
        self.stop_event = stop_event
        self.config = None

    async def retention_days(self) -> int:
        if not self.settings.nacos_config_enabled or not self.settings.nacos_server_addr:
            return self.settings.agent_chat_retention_days
        if self.config is None:
            self.config = AsyncNacosConfigClient(
                self.settings.nacos_server_addr,
                namespace=self.settings.nacos_namespace,
                group=self.settings.nacos_config_group or self.settings.nacos_group,
                username=self.settings.nacos_username,
                password=self.settings.nacos_password,
                timeout=self.settings.nacos_config_timeout_seconds,
            )
        payload = await self.config.get_json_config(
            self.settings.nacos_config_data_id,
            group=self.settings.nacos_config_group or self.settings.nacos_group,
        )
        if payload is None:
            raise ValueError("Retention configuration document is missing")
        value = _flatten_config(payload).get("agent_chat_retention_days")
        if value is None:
            return 7
        # Reject bools/fractions rather than silently shortening a retention window.
        if isinstance(value, bool) or not str(value).strip().isdigit():
            raise ValueError("Invalid retention days")
        days = int(value)
        if not 1 <= days <= 3650:
            raise ValueError("Retention days outside range")
        return days

    async def run_once(self, *, now: datetime | None = None) -> int:
        days = await self.retention_days()
        cutoff = (now or utcnow()) - timedelta(days=days)
        # SKIP LOCKED also makes multiple polling workers safe to run together.
        # Active and recently modified turns protect in-flight replies, including
        # conversations created by versions which did not touch the parent timestamp.
        protected_turn = exists().where(
            AgentTurn.conversation_id == AgentConversation.id,
            (AgentTurn.status.in_(("queued", "running"))) | (AgentTurn.updated_at >= cutoff),
        )
        async with self.session_factory() as db, db.begin():
            ids = list(
                await db.scalars(
                    select(AgentConversation.id)
                    .where(AgentConversation.updated_at < cutoff, ~protected_turn)
                    .order_by(AgentConversation.updated_at, AgentConversation.id)
                    .limit(CLEANUP_BATCH_SIZE)
                    .with_for_update(skip_locked=True)
                )
            )
            turn_ids = await delete_conversation_rows(db, ids)
        await delete_event_streams(self.redis, turn_ids)
        if ids:
            logger.info(
                "Expired agent conversations deleted",
                extra={
                    "conversations": len(ids),
                    "turns": len(turn_ids),
                    "retention_days": days,
                },
            )
        return len(ids)

    async def run(self) -> None:
        try:
            while not self.stop_event.is_set():
                try:
                    if await self.run_once() == CLEANUP_BATCH_SIZE:
                        continue
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # A bad/unavailable remote policy skips deletion for this cycle.
                    logger.warning(
                        "Agent chat retention cleanup skipped",
                        extra={
                            "error_type": type(exc).__name__,
                        },
                    )
                try:
                    await asyncio.wait_for(
                        self.stop_event.wait(), self.settings.agent_chat_cleanup_interval_seconds
                    )
                except TimeoutError:
                    pass
        finally:
            if self.config is not None:
                await self.config.aclose()
