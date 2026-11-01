"""Durable prompt authoring jobs executed by ai-worker."""

import asyncio
import logging
from datetime import timedelta
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select, update

from app.core.time import as_utc
from app.db.base import utcnow
from app.models.ai import AISetting
from app.models.prompt_generation import PromptGeneration
from app.schemas.ai import PromptGenerationResult
from app.services.ai_data_source import AIDataSourceUnavailableError, get_ai_data_source
from app.services.ai_provider import AIProviderError

logger = logging.getLogger(__name__)


class PromptGenerationStopped(Exception):
    pass


class PromptGenerationWorker:
    def __init__(self, settings, redis, provider, session_factory, semaphore, stop_event):
        self.settings = settings
        self.redis = redis
        self.provider = provider
        self.session_factory = session_factory
        self.semaphore = semaphore
        self.stop_event = stop_event
        self.active_tasks = 0

    async def run(self):
        while not self.stop_event.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Prompt generation scan failed", extra={"error_type": type(exc).__name__}
                )
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(), self.settings.ai_worker_scan_interval_seconds
                )
            except TimeoutError:
                pass

    async def run_once(self):
        async with self.session_factory() as db, db.begin():
            await db.execute(
                update(PromptGeneration)
                .where(
                    PromptGeneration.status == "running",
                    PromptGeneration.lease_expires_at <= utcnow(),
                )
                .values(
                    status="failed",
                    error="生成任务中断或超时，请重新生成",
                    claim_token=None,
                    lease_expires_at=None,
                    updated_at=utcnow(),
                )
            )
            await db.execute(
                update(PromptGeneration)
                .where(
                    PromptGeneration.status == "queued",
                    PromptGeneration.created_at < utcnow() - timedelta(minutes=10),
                )
                .values(status="failed", error="等待生成超时，请检查 AI Worker 后重试")
            )
            ids = list(
                await db.scalars(
                    select(PromptGeneration.id)
                    .where(PromptGeneration.status == "queued")
                    .order_by(PromptGeneration.created_at)
                    .limit(self.settings.ai_worker_max_concurrency)
                )
            )
        tasks = [asyncio.create_task(self.process(generation_id)) for generation_id in ids]
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error(
                        "Prompt generation task failed",
                        extra={"error_type": type(result).__name__},
                    )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def process(self, generation_id: str):
        async with self.semaphore:
            if self.stop_event.is_set():
                return
            token = str(uuid4())
            async with self.session_factory() as db, db.begin():
                row = await db.get(PromptGeneration, generation_id)
                if row is None or row.status != "queued":
                    return
                timeout = int(row.config_snapshot["timeout_seconds"]) + 15
                claimed = await db.execute(
                    update(PromptGeneration)
                    .where(
                        PromptGeneration.id == generation_id,
                        PromptGeneration.status == "queued",
                    )
                    .values(
                        status="running",
                        claim_token=token,
                        lease_expires_at=utcnow() + timedelta(seconds=timeout + 30),
                        updated_at=utcnow(),
                    )
                )
                if claimed.rowcount != 1:
                    return
            self.active_tasks += 1
            try:
                async with asyncio.timeout(timeout):
                    await self.execute(generation_id, token)
            except PromptGenerationStopped:
                pass
            except asyncio.CancelledError:
                await self.finish(generation_id, token, status="queued", error=None)
                raise
            except (AIDataSourceUnavailableError, AIProviderError, TimeoutError) as exc:
                if isinstance(exc, TimeoutError):
                    error = "模型请求超时，请重试"
                elif isinstance(exc, AIProviderError) and exc.status_code:
                    error = f"模型请求失败（HTTP {exc.status_code}），请检查数据源配置后重试"
                else:
                    error = "模型回复失败，请检查 AI 数据源后重试"
                await self.finish(generation_id, token, status="failed", error=error)
            except ValidationError:
                await self.finish(
                    generation_id,
                    token,
                    status="failed",
                    error="模型返回的提示词格式或长度不符合要求，请调整需求或输出长度后重试",
                )
            except Exception as exc:
                logger.error(
                    "Prompt generation failed",
                    extra={"prompt_generation_id": generation_id, "error_type": type(exc).__name__},
                )
                await self.finish(
                    generation_id, token, status="failed", error="生成失败，请稍后重试"
                )
            finally:
                self.active_tasks -= 1

    async def execute(self, generation_id: str, token: str):
        async with self.session_factory() as db:
            row = await db.get(PromptGeneration, generation_id)
            if row is None or row.claim_token != token or row.status != "running":
                return
            config = dict(row.config_snapshot or {})
            setting = await db.get(AISetting, 1)
            if not setting or not setting.enabled:
                raise AIDataSourceUnavailableError("AI disabled")
            runtime = await get_ai_data_source(db, self.redis, self.settings)
            if any(
                getattr(runtime, key) != config[key] for key in ("base_url", "model", "protocol")
            ):
                raise AIProviderError("AI data source changed", retryable=False)
            requirement = row.requirement

        async def generate():
            async with asyncio.timeout(config["timeout_seconds"]):
                raw = await self.provider.generate_prompt(
                    base_url=runtime.base_url,
                    model=runtime.model,
                    api_key=runtime.api_key,
                    requirement=requirement,
                    language=config["language"],
                    tone=config["tone"],
                    reasoning_effort=config["reasoning_effort"],
                    max_output_tokens=config["max_output_tokens"],
                    timeout_seconds=config["timeout_seconds"],
                )
            return PromptGenerationResult.model_validate(raw).model_dump(mode="json")

        async def watch_claim():
            while True:
                await asyncio.sleep(1)
                async with self.session_factory() as db:
                    current = await db.get(PromptGeneration, generation_id)
                    if (
                        not current
                        or current.status != "running"
                        or current.claim_token != token
                        or not current.lease_expires_at
                        or as_utc(current.lease_expires_at) <= utcnow()
                    ):
                        raise PromptGenerationStopped()

        model_task = asyncio.create_task(generate())
        guard_task = asyncio.create_task(watch_claim())
        try:
            done, _ = await asyncio.wait(
                {model_task, guard_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if guard_task in done:
                await guard_task
            result = await model_task
        finally:
            model_task.cancel()
            guard_task.cancel()
            await asyncio.gather(model_task, guard_task, return_exceptions=True)
        await self.finish(generation_id, token, status="succeeded", result=result, error=None)

    async def finish(self, generation_id: str, token: str, *, status: str, **values):
        async with self.session_factory() as db, db.begin():
            row = await db.get(PromptGeneration, generation_id, with_for_update=True)
            if (
                row is None
                or row.status != "running"
                or row.claim_token != token
                or not row.lease_expires_at
                or as_utc(row.lease_expires_at) <= utcnow()
            ):
                return False
            row.status = status
            row.claim_token = None
            row.lease_expires_at = None
            row.updated_at = utcnow()
            for key, value in values.items():
                setattr(row, key, value)
            return True
