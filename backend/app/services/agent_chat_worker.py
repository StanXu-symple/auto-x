"""Durable backend -> ai-worker invocation with fenced, bounded claims."""

import asyncio
import logging
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select, update

from app.db.base import utcnow
from app.models.agent_chat import AgentTurn
from app.models.ai import AISetting
from app.services.agent_provider import generate_agent_reply
from app.services.ai_data_source import AIDataSourceUnavailableError, get_ai_data_source
from app.services.ai_provider import AIProviderError

logger = logging.getLogger(__name__)


class AgentChatWorker:
    def __init__(self, settings, redis, provider, session_factory, semaphore, stop_event):
        self.settings = settings
        self.redis = redis
        self.provider = provider
        self.session_factory = session_factory
        self.semaphore = semaphore
        self.stop_event = stop_event
        self.active_tasks = 0

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
            finally:
                self.active_tasks -= 1

    async def _process(self, turn_id: str) -> None:
        token = str(uuid4())
        async with self.session_factory() as db, db.begin():
            turn = await db.get(AgentTurn, turn_id)
            if turn is None or turn.status != "queued":
                return
            timeout = int(turn.config_snapshot["timeout_seconds"]) + 15
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
                result = await generate_agent_reply(self.provider, runtime, history, turn)
            await self.finish(
                turn_id,
                token,
                status="succeeded",
                reply=result.reply,
                article=result.article.model_dump() if result.article else None,
                error=None,
            )
        except asyncio.CancelledError:
            await self.finish(turn_id, token, status="queued", error=None)
            raise
        except AIDataSourceUnavailableError:
            await self.finish(
                turn_id,
                token,
                status="failed",
                error="AI 未启用或数据源不可用，请检查 AI 创作设置与 AI 数据源后重试",
            )
        except (AIProviderError, TimeoutError) as exc:
            # Never persist upstream response bodies: they can echo secrets or prompts.
            if isinstance(exc, TimeoutError):
                error = "模型请求超时，请重试"
            elif exc.status_code:
                error = f"模型服务请求失败（HTTP {exc.status_code}），请检查数据源配置后重试"
            else:
                error = "模型回复失败、被拒绝或未完整返回，请检查输出长度设置或调整要求后重试"
            await self.finish(turn_id, token, status="failed", error=error)
        except Exception as exc:
            logger.error(
                "Agent chat generation failed",
                extra={
                    "agent_turn_id": turn_id,
                    "error_type": type(exc).__name__,
                },
            )
            await self.finish(turn_id, token, status="failed", error="生成失败，请稍后重试")

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
