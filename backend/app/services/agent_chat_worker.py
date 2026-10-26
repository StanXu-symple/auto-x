"""Durable backend -> ai-worker invocation with fenced, bounded claims."""

import asyncio
import json
import logging
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select, update

from app.db.base import utcnow
from app.models.agent_chat import AgentTurn
from app.models.ai import AISetting
from app.services.agent_provider import stream_agent_reply
from app.services.ai_data_source import AIDataSourceUnavailableError, get_ai_data_source
from app.services.ai_provider import AIProviderError

logger = logging.getLogger(__name__)
EVENT_STREAM_TTL_SECONDS = 24 * 60 * 60


def event_stream_key(turn_id: str) -> str:
    return f"xsentinel:agent-chat:events:{turn_id}"


class AgentTurnRemoved(Exception):
    """Stop streaming a removed turn without recreating SQL or Redis data."""


class AgentChatWorker:
    def __init__(self, settings, redis, provider, session_factory, semaphore, stop_event):
        self.settings = settings
        self.redis = redis
        self.provider = provider
        self.session_factory = session_factory
        self.semaphore = semaphore
        self.stop_event = stop_event
        self.active_tasks = 0

    async def publish_event(
        self, turn_id: str, event: dict[str, object], *, attempts: int | None = None
    ) -> None:
        if self.redis is None:
            return
        payload = {
            **event,
            "turn_id": turn_id,
            "timestamp": utcnow().isoformat(),
        }
        if attempts is not None:
            payload["attempts"] = attempts
        # Serialize emission with deletion. Once DELETE commits, a late model
        # chunk cannot recreate the replay stream. Keep the lock only for Redis I/O.
        async with self.session_factory() as db, db.begin():
            query = select(AgentTurn.id).where(AgentTurn.id == turn_id)
            if attempts is not None:
                query = query.where(AgentTurn.attempts == attempts)
            if await db.scalar(query.with_for_update()) is None:
                raise AgentTurnRemoved()
            try:
                async with self.redis.pipeline(transaction=True) as pipeline:
                    pipeline.xadd(
                        event_stream_key(turn_id),
                        {"payload": json.dumps(payload, ensure_ascii=False)},
                        maxlen=2000,
                        approximate=True,
                    )
                    pipeline.expire(event_stream_key(turn_id), EVENT_STREAM_TTL_SECONDS)
                    await pipeline.execute()
            except Exception:
                logger.warning(
                    "Agent chat event publish failed",
                    extra={"agent_turn_id": turn_id, "error_type": "redis"},
                )

    async def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Exception bodies can include model data or credentials.
                logger.error("Agent chat scan failed", extra={"error_type": type(exc).__name__})
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(), self.settings.ai_worker_scan_interval_seconds
                )
            except TimeoutError:
                pass

    async def run_once(self) -> None:
        async with self.session_factory() as db, db.begin():
            await db.execute(
                update(AgentTurn)
                .where(
                    AgentTurn.status == "running",
                    AgentTurn.lease_expires_at <= utcnow(),
                )
                .values(
                    status="failed",
                    error="生成任务中断或超时，请重试",
                    claim_token=None,
                    lease_expires_at=None,
                    updated_at=utcnow(),
                )
            )
            ids = list(
                await db.scalars(
                    select(AgentTurn.id)
                    .where(AgentTurn.status == "queued")
                    .order_by(AgentTurn.created_at)
                    .limit(self.settings.ai_worker_max_concurrency)
                )
            )
        tasks = [asyncio.create_task(self.process(turn_id)) for turn_id in ids]
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error(
                        "Agent chat task failed", extra={"error_type": type(result).__name__}
                    )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def process(self, turn_id: str) -> None:
        async with self.semaphore:
            if self.stop_event.is_set():
                return
            self.active_tasks += 1
            try:
                await self._process(turn_id)
            except AgentTurnRemoved:
                pass
            finally:
                self.active_tasks -= 1

    async def _process(self, turn_id: str) -> None:
        token = str(uuid4())
        async with self.session_factory() as db, db.begin():
            turn = await db.get(AgentTurn, turn_id)
            if turn is None or turn.status != "queued":
                return
            timeout = int(turn.config_snapshot["timeout_seconds"]) + 15
            attempt_number = turn.attempts + 1
            claimed = await db.execute(
                update(AgentTurn)
                .where(AgentTurn.id == turn_id, AgentTurn.status == "queued")
                .values(
                    status="running",
                    claim_token=token,
                    attempts=AgentTurn.attempts + 1,
                    lease_expires_at=utcnow() + timedelta(seconds=timeout + 30),
                    updated_at=utcnow(),
                )
            )
            if claimed.rowcount != 1:
                return

        async def emit(event: dict[str, object]) -> None:
            await self.publish_event(turn_id, event, attempts=attempt_number)

        await emit({"type": "status", "phase": "preparing", "label": "正在准备上下文"})
        try:
            # Entire execution is bounded below the lease, including config/history reads.
            async with asyncio.timeout(timeout):
                async with self.session_factory() as db:
                    setting = await db.get(AISetting, 1)
                    if setting is None or not setting.enabled:
                        raise AIDataSourceUnavailableError("AI disabled")
                    runtime = await get_ai_data_source(db, self.redis, self.settings)
                    history = list(
                        await db.scalars(
                            select(AgentTurn)
                            .where(
                                AgentTurn.conversation_id == turn.conversation_id,
                                AgentTurn.sequence < turn.sequence,
                                AgentTurn.status == "succeeded",
                            )
                            .order_by(AgentTurn.sequence)
                        )
                    )
                result = await stream_agent_reply(
                    self.provider,
                    runtime,
                    history,
                    turn,
                    emit,
                )
            finished = await self.finish(
                turn_id,
                token,
                status="succeeded",
                reply=result.reply,
                article=result.article.model_dump() if result.article else None,
                error=None,
            )
            if finished:
                await emit({"type": "done", "status": "succeeded"})
        except AgentTurnRemoved:
            return
        except asyncio.CancelledError:
            await self.finish(turn_id, token, status="queued", error=None)
            raise
        except AIDataSourceUnavailableError:
            finished = await self.finish(
                turn_id,
                token,
                status="failed",
                error="AI 未启用或数据源不可用，请检查 AI 创作设置与 AI 数据源后重试",
            )
            if finished:
                await emit(
                    {
                        "type": "error",
                        "phase": "failed",
                        "message": "AI 未启用或数据源不可用，请检查 AI 创作设置与 AI 数据源后重试",
                    },
                )
        except (AIProviderError, TimeoutError) as exc:
            # Never persist upstream response bodies: they can echo secrets or prompts.
            if isinstance(exc, TimeoutError):
                error = "模型请求超时，请重试"
            elif exc.status_code:
                error = f"模型服务请求失败（HTTP {exc.status_code}），请检查数据源配置后重试"
            else:
                error = "模型回复失败、被拒绝或未完整返回，请检查输出长度设置或调整要求后重试"
            finished = await self.finish(turn_id, token, status="failed", error=error)
            if finished:
                await emit({"type": "error", "phase": "failed", "message": error})
        except Exception as exc:
            logger.error(
                "Agent chat generation failed",
                extra={
                    "agent_turn_id": turn_id,
                    "error_type": type(exc).__name__,
                },
            )
            error = "生成失败，请稍后重试"
            finished = await self.finish(turn_id, token, status="failed", error=error)
            if finished:
                await emit({"type": "error", "phase": "failed", "message": error})

    async def finish(self, turn_id: str, token: str, **values) -> bool:
        async with self.session_factory() as db, db.begin():
            result = await db.execute(
                update(AgentTurn)
                .where(
                    AgentTurn.id == turn_id,
                    AgentTurn.status == "running",
                    AgentTurn.claim_token == token,
                    AgentTurn.lease_expires_at > utcnow(),
                )
                .values(**values, claim_token=None, lease_expires_at=None, updated_at=utcnow())
            )
            return result.rowcount == 1
