from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import signal
import socket
import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from prometheus_client import start_http_server
from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select, text

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.time import as_utc
from app.db.session import AsyncSessionFactory, engine
from app.models.ai import AIDraft, AIGenerationJob, AISetting, AIUserProfile
from app.models.ai_data_source import AIDataSource
from app.services.ai_data_source import (
    AIDataSourceUnavailableError,
    get_ai_data_source,
)
from app.services.ai_provider import AIProviderClient, AIProviderError, ProviderRequest
from app.services.metrics import (
    AI_DRAFTS,
    AI_JOB_DURATION,
    AI_JOBS,
    AI_QUEUE_DUE,
    AI_WORKER_HEARTBEAT,
)

logger = logging.getLogger(__name__)
AI_HEARTBEAT_KEY = "xsentinel:ai-worker:heartbeat"

RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""
RENEW_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
else
  return 0
end
"""


class AILockLostError(RuntimeError):
    pass


class AIGenerationWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )
        self.provider = AIProviderClient(settings)
        self.stop_event = asyncio.Event()
        self.active_tasks = 0

    def request_stop(self) -> None:
        self.stop_event.set()

    async def run(self) -> None:
        await self.redis.ping()
        async with AsyncSessionFactory() as session:
            await session.execute(text("SELECT 1"))
        logger.info("X Sentinel AI worker started", extra={"worker_id": self.worker_id})
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            while not self.stop_event.is_set():
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("AI worker scan failed", extra={"worker_id": self.worker_id})
                try:
                    await asyncio.wait_for(
                        self.stop_event.wait(),
                        timeout=self.settings.ai_worker_scan_interval_seconds,
                    )
                except TimeoutError:
                    pass
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
            await self.provider.aclose()
            await self.redis.aclose()
            await engine.dispose()
            logger.info("X Sentinel AI worker stopped", extra={"worker_id": self.worker_id})

    async def run_once(self) -> int:
        await self._heartbeat()
        now = datetime.now(UTC)
        due_condition = or_(
            and_(
                AIGenerationJob.status.in_(["queued", "retry_wait"]),
                AIGenerationJob.next_attempt_at <= now,
            ),
            and_(
                AIGenerationJob.status == "running",
                AIGenerationJob.lease_expires_at.is_not(None),
                AIGenerationJob.lease_expires_at <= now,
            ),
        )
        async with AsyncSessionFactory() as session:
            ai_setting = await session.get(AISetting, 1)
            due_count = int(
                await session.scalar(select(func.count(AIGenerationJob.id)).where(due_condition))
                or 0
            )
            if ai_setting is not None and ai_setting.enabled:
                job_ids = list(
                    await session.scalars(
                        select(AIGenerationJob.id)
                        .where(due_condition)
                        .order_by(
                            AIGenerationJob.next_attempt_at.asc(),
                            AIGenerationJob.id.asc(),
                        )
                        .limit(self.settings.ai_worker_batch_size)
                    )
                )
            else:
                job_ids = []
        AI_QUEUE_DUE.set(due_count)
        if not job_ids:
            return 0

        semaphore = asyncio.Semaphore(self.settings.ai_worker_max_concurrency)

        async def generate_one(job_id: int) -> None:
            async with semaphore:
                self.active_tasks += 1
                try:
                    await self.process_job(job_id)
                finally:
                    self.active_tasks -= 1

        tasks = [asyncio.create_task(generate_one(job_id)) for job_id in job_ids]
        await self._wait_for_tasks(tasks)
        await self._heartbeat()
        return len(job_ids)

    async def _wait_for_tasks(self, tasks: list[asyncio.Task[None]]) -> None:
        async def wait_batch() -> None:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(
                    result, asyncio.CancelledError
                ):
                    logger.error(
                        "AI generation task escaped its error boundary",
                        extra={"error": str(result)},
                    )

        batch_task = asyncio.create_task(wait_batch())
        stop_task = asyncio.create_task(self.stop_event.wait())
        done, _ = await asyncio.wait({batch_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if stop_task in done and not batch_task.done():
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            batch_task.cancel()
            await asyncio.gather(batch_task, return_exceptions=True)
        else:
            await batch_task
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)

    async def process_job(self, job_id: int) -> bool:
        lock_key = f"xsentinel:ai:lock:{job_id}"
        claim_token = str(uuid.uuid4())
        try:
            acquired = await self.redis.set(
                lock_key,
                claim_token,
                nx=True,
                ex=self.settings.ai_worker_lock_ttl_seconds,
            )
        except Exception:
            logger.exception("AI lock acquisition failed", extra={"ai_job_id": job_id})
            return False
        if not acquired:
            return False

        started_perf = time.perf_counter()
        lost_lock = asyncio.Event()
        renew_task: asyncio.Task[None] | None = None
        claimed = False
        provider_name = "unknown"
        try:
            job = await self._claim(job_id, claim_token)
            if job is None:
                return False
            claimed = True
            provider_name = job.provider
            renew_task = asyncio.create_task(
                self._renew_lease(job_id, lock_key, claim_token, lost_lock)
            )
            request = await self._provider_request(job)
            provider_name = request.provider
            result = await self._generate_with_lease(request, lost_lock)
            await self._assert_lock(lock_key, claim_token, lost_lock)
            applied = await self._commit_success(
                job_id,
                claim_token,
                lock_key,
                lost_lock,
                result.draft.model_dump(),
                result.response_snapshot,
                result.prompt_hash,
                result.source_text_hash,
            )
            outcome = "succeeded" if applied else "superseded"
            self._observe(outcome, provider_name, started_perf)
            return applied
        except AIProviderError as exc:
            outcome = await self._commit_failure(
                job_id,
                claim_token,
                lock_key,
                lost_lock,
                message=str(exc),
                retryable=exc.retryable,
                status_code=exc.status_code,
            )
            self._observe(outcome, provider_name, started_perf)
            return False
        except AILockLostError:
            logger.warning(
                "AI generation lease lost; result discarded",
                extra={"ai_job_id": job_id, "worker_id": self.worker_id},
            )
            self._observe("lock_lost", provider_name, started_perf)
            return False
        except asyncio.CancelledError:
            if claimed:
                with suppress(Exception):
                    await self._requeue_cancelled(job_id, claim_token, lock_key, lost_lock)
            raise
        except Exception as exc:
            logger.exception("Unexpected AI generation failure", extra={"ai_job_id": job_id})
            outcome = await self._commit_failure(
                job_id,
                claim_token,
                lock_key,
                lost_lock,
                message=f"Unexpected AI generation error: {exc}",
                retryable=True,
                status_code=None,
            )
            self._observe(outcome, provider_name, started_perf)
            return False
        finally:
            if renew_task is not None:
                renew_task.cancel()
                with suppress(asyncio.CancelledError):
                    await renew_task
            with suppress(Exception):
                await self.redis.eval(RELEASE_LOCK_SCRIPT, 1, lock_key, claim_token)

    async def _claim(self, job_id: int, claim_token: str) -> AIGenerationJob | None:
        now = datetime.now(UTC)
        async with AsyncSessionFactory() as session, session.begin():
            job = await session.get(AIGenerationJob, job_id, with_for_update=True)
            if job is None:
                return None
            due = job.status in {"queued", "retry_wait"} and as_utc(job.next_attempt_at) <= now
            stale = (
                job.status == "running"
                and job.lease_expires_at is not None
                and as_utc(job.lease_expires_at) <= now
            )
            if not due and not stale:
                return None
            if job.attempts >= job.max_attempts:
                job.status = "failed"
                job.last_error = "Generation lease expired after the maximum attempt count"
                job.completed_at = now
                job.claim_token = None
                job.claimed_by = None
                job.lease_expires_at = None
                return None
            job.status = "running"
            job.attempts += 1
            job.claim_token = claim_token
            job.claimed_by = self.worker_id
            job.started_at = now
            job.completed_at = None
            job.last_error = None
            job.lease_expires_at = now + timedelta(seconds=self.settings.ai_worker_lock_ttl_seconds)
            await session.flush()
            # Materialize everything needed before leaving the session; generation never
            # lazy-loads mutable configuration after the audited enqueue snapshot.
            session.expunge(job)
            return job

    async def _provider_request(self, job: AIGenerationJob) -> ProviderRequest:
        snapshot = job.request_snapshot or {}
        config = snapshot.get("config")
        source = snapshot.get("source")
        if not isinstance(config, dict) or not isinstance(source, dict):
            raise AIProviderError("Generation job has no valid audit snapshot", retryable=False)
        try:
            async with AsyncSessionFactory() as session:
                data_source = await get_ai_data_source(session, self.redis, self.settings)
                current_job = await session.get(AIGenerationJob, job.id, with_for_update=True)
                if current_job is None or current_job.claim_token != job.claim_token:
                    raise AIProviderError("Generation job claim is no longer valid", retryable=True)
                current_snapshot = dict(current_job.request_snapshot or {})
                current_config = dict(current_snapshot.get("config") or {})
                current_config.update(
                    {
                        "provider": data_source.protocol,
                        "model": data_source.model,
                        "base_url": data_source.base_url,
                        "bridge_url": None,
                        "ai_data_source_name": data_source.name,
                        "ai_data_source_version": data_source.version,
                    }
                )
                current_snapshot["config"] = current_config
                current_job.provider = data_source.protocol
                current_job.model_name = data_source.model
                current_job.request_snapshot = current_snapshot
                await session.commit()
        except AIDataSourceUnavailableError as exc:
            raise AIProviderError(str(exc), retryable=False) from exc
        job.provider = data_source.protocol
        job.model_name = data_source.model
        job.request_snapshot = current_snapshot
        return ProviderRequest(
            provider=data_source.protocol,
            model=data_source.model,
            base_url=data_source.base_url,
            bridge_url=None,
            prompt_template=(
                str(config["prompt_template"]) if config.get("prompt_template") else None
            ),
            language=str(config.get("language") or "zh-CN"),
            tone=str(config.get("tone") or "专业自然"),
            reasoning_effort=str(config.get("reasoning_effort") or "medium"),
            max_output_tokens=int(config.get("max_output_tokens") or 2500),
            timeout_seconds=int(config.get("request_timeout_seconds") or 60),
            skill_snapshot=job.skill_snapshot or [],
            feature_snapshot=dict(snapshot.get("feature") or {}),
            author_context=dict(snapshot.get("author_context") or {}),
            source=source,
            job_id=job.id,
            api_key=data_source.api_key,
        )

    async def _generate_with_lease(self, request: ProviderRequest, lost_lock: asyncio.Event):
        generation_task = asyncio.create_task(self.provider.generate(request))
        lost_task = asyncio.create_task(lost_lock.wait())
        done, _ = await asyncio.wait(
            {generation_task, lost_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if lost_task in done and lost_lock.is_set() and not generation_task.done():
            generation_task.cancel()
            await asyncio.gather(generation_task, return_exceptions=True)
            raise AILockLostError("AI generation lease was lost")
        lost_task.cancel()
        await asyncio.gather(lost_task, return_exceptions=True)
        return await generation_task

    async def _commit_success(
        self,
        job_id: int,
        claim_token: str,
        lock_key: str,
        lost_lock: asyncio.Event,
        draft_payload: dict[str, Any],
        response_snapshot: dict[str, Any],
        prompt_hash: str,
        source_text_hash: str,
    ) -> bool:
        await self._assert_lock(lock_key, claim_token, lost_lock)
        now = datetime.now(UTC)
        async with AsyncSessionFactory() as session, session.begin():
            job = await session.get(AIGenerationJob, job_id, with_for_update=True)
            if job is None or job.claim_token != claim_token or job.status != "running":
                return False
            await self._assert_lock(lock_key, claim_token, lost_lock)
            config = (job.request_snapshot or {}).get("config") or {}
            require_review = bool(config.get("require_review", True))
            draft = await session.scalar(
                select(AIDraft).where(AIDraft.job_id == job.id).with_for_update()
            )
            metadata = dict(draft_payload.get("metadata") or {})
            metadata.update(
                {
                    "provider": job.provider,
                    "model": job.model_name,
                    "require_review": require_review,
                    "skill_ids": job.skill_ids or [],
                }
            )
            if draft is None:
                draft = AIDraft(
                    job_id=job.id,
                    source_tweet_id=job.source_tweet_id,
                    title=draft_payload["title"],
                    content=draft_payload["content"],
                    excerpt=draft_payload.get("excerpt"),
                    status="draft" if require_review else "approved",
                    draft_metadata=metadata,
                    revision=1,
                )
                session.add(draft)
            else:
                draft.title = draft_payload["title"]
                draft.content = draft_payload["content"]
                draft.excerpt = draft_payload.get("excerpt")
                draft.draft_metadata = metadata
                draft.revision += 1
            profile_payload = dict(draft_payload.get("author_profile") or {})
            author = ((job.request_snapshot or {}).get("author_context") or {}).get("author") or {}
            monitored_user_id = int(author.get("monitored_user_id") or 0)
            if monitored_user_id:
                profile = await session.get(
                    AIUserProfile, monitored_user_id, with_for_update=True
                )
                if profile is None:
                    profile = AIUserProfile(
                        monitored_user_id=monitored_user_id,
                        identity_summary=profile_payload.get("identity_summary", ""),
                        focus_summary=profile_payload.get("focus_summary", ""),
                        relationship_summary=profile_payload.get("relationship_summary", ""),
                        recurring_topics=profile_payload.get("recurring_topics", []),
                        evidence=profile_payload.get("evidence", []),
                        confidence=float(profile_payload.get("confidence") or 0),
                        version=1,
                        last_source_tweet_id=job.source_tweet_id,
                    )
                    session.add(profile)
                else:
                    profile.identity_summary = profile_payload.get("identity_summary", "")
                    profile.focus_summary = profile_payload.get("focus_summary", "")
                    profile.relationship_summary = profile_payload.get(
                        "relationship_summary", ""
                    )
                    profile.recurring_topics = profile_payload.get("recurring_topics", [])
                    profile.evidence = profile_payload.get("evidence", [])
                    profile.confidence = float(profile_payload.get("confidence") or 0)
                    profile.version += 1
                    profile.last_source_tweet_id = job.source_tweet_id
                    profile.updated_at = now
            job.status = "succeeded"
            job.response_snapshot = response_snapshot
            job.prompt_hash = prompt_hash
            job.source_text_hash = source_text_hash
            job.last_error = None
            job.completed_at = now
            job.claim_token = None
            job.claimed_by = None
            job.lease_expires_at = None
        AI_DRAFTS.labels(provider=job.provider).inc()
        return True

    async def _commit_failure(
        self,
        job_id: int,
        claim_token: str,
        lock_key: str,
        lost_lock: asyncio.Event,
        *,
        message: str,
        retryable: bool,
        status_code: int | None,
    ) -> str:
        await self._assert_lock(lock_key, claim_token, lost_lock)
        now = datetime.now(UTC)
        async with AsyncSessionFactory() as session, session.begin():
            job = await session.get(AIGenerationJob, job_id, with_for_update=True)
            if job is None or job.claim_token != claim_token or job.status != "running":
                return "superseded"
            await self._assert_lock(lock_key, claim_token, lost_lock)
            should_retry = retryable and job.attempts < job.max_attempts
            if should_retry:
                base = min(1800, 10 * (2 ** max(0, job.attempts - 1)))
                delay = max(5, round(base * random.uniform(0.8, 1.2)))
                job.status = "retry_wait"
                job.next_attempt_at = now + timedelta(seconds=delay)
                outcome = "retry_wait"
            else:
                job.status = "failed"
                job.completed_at = now
                outcome = "failed"
            job.last_error = message[:4000]
            job.response_snapshot = {
                "error": message[:4000],
                "retryable": retryable,
                "status_code": status_code,
            }
            job.claim_token = None
            job.claimed_by = None
            job.lease_expires_at = None
            return outcome

    async def _requeue_cancelled(
        self,
        job_id: int,
        claim_token: str,
        lock_key: str,
        lost_lock: asyncio.Event,
    ) -> None:
        await self._assert_lock(lock_key, claim_token, lost_lock)
        async with AsyncSessionFactory() as session, session.begin():
            job = await session.get(AIGenerationJob, job_id, with_for_update=True)
            if job is None or job.claim_token != claim_token or job.status != "running":
                return
            job.status = "retry_wait"
            job.next_attempt_at = datetime.now(UTC)
            job.last_error = "AI worker stopped during generation; job was requeued"
            job.claim_token = None
            job.claimed_by = None
            job.lease_expires_at = None

    async def _renew_lease(
        self,
        job_id: int,
        lock_key: str,
        claim_token: str,
        lost_lock: asyncio.Event,
    ) -> None:
        interval = max(5.0, self.settings.ai_worker_lock_ttl_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = await self.redis.eval(
                    RENEW_LOCK_SCRIPT,
                    1,
                    lock_key,
                    claim_token,
                    self.settings.ai_worker_lock_ttl_seconds,
                )
                if not renewed:
                    raise AILockLostError("Redis AI lease is no longer owned")
                async with AsyncSessionFactory() as session, session.begin():
                    job = await session.get(AIGenerationJob, job_id, with_for_update=True)
                    if job is None or job.claim_token != claim_token or job.status != "running":
                        raise AILockLostError("Database AI claim was superseded")
                    job.lease_expires_at = datetime.now(UTC) + timedelta(
                        seconds=self.settings.ai_worker_lock_ttl_seconds
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                lost_lock.set()
                logger.exception("AI lease renewal failed", extra={"ai_job_id": job_id})
                return

    async def _assert_lock(self, lock_key: str, claim_token: str, lost_lock: asyncio.Event) -> None:
        if lost_lock.is_set():
            raise AILockLostError("AI generation lease was lost")
        try:
            renewed = await self.redis.eval(
                RENEW_LOCK_SCRIPT,
                1,
                lock_key,
                claim_token,
                self.settings.ai_worker_lock_ttl_seconds,
            )
        except Exception as exc:
            lost_lock.set()
            raise AILockLostError("Could not verify AI generation lease") from exc
        if not renewed:
            lost_lock.set()
            raise AILockLostError("AI generation lease is no longer owned")

    async def _heartbeat(self) -> None:
        provider = "unknown"
        key_required: bool | None = None
        key_configured: bool | None = None
        provider_ready = False
        try:
            async with AsyncSessionFactory() as session:
                setting = await session.get(AISetting, 1)
                if setting is None:
                    raise RuntimeError("AI settings row is missing; apply migration 0003")
                data_source = await session.get(AIDataSource, 1)
                provider = "openai_responses"
                key_required = True
                key_configured = data_source is not None
                if data_source is not None:
                    hostname = urlsplit(data_source.base_url).hostname or ""
                    try:
                        self.provider._validate_destination(
                            data_source.base_url.rstrip("/") + "/responses",
                            allowed_hosts=[*self.settings.ai_allowed_provider_hosts, hostname],
                            sends_credential=True,
                        )
                        destination_ready = True
                    except AIProviderError:
                        destination_ready = False
                    provider_ready = key_configured and destination_ready
        except Exception:
            logger.exception("AI heartbeat readiness check failed")
        now = datetime.now(UTC)
        timestamp = now.isoformat().replace("+00:00", "Z")
        payload = json.dumps(
            {
                "worker_id": self.worker_id,
                "status": "running",
                "timestamp": timestamp,
                "last_heartbeat": timestamp,
                "active_tasks": self.active_tasks,
                "provider": provider,
                "provider_ready": provider_ready,
                "key_required": key_required,
                "key_configured": key_configured,
            }
        )
        await self.redis.set(
            AI_HEARTBEAT_KEY,
            payload,
            ex=self.settings.ai_worker_heartbeat_ttl_seconds,
        )
        AI_WORKER_HEARTBEAT.set(now.timestamp())

    async def _heartbeat_loop(self) -> None:
        interval = max(3.0, self.settings.ai_worker_heartbeat_ttl_seconds / 3)
        while not self.stop_event.is_set():
            try:
                await self._heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("AI worker heartbeat failed", extra={"worker_id": self.worker_id})
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=interval)
            except TimeoutError:
                pass

    @staticmethod
    def _observe(status: str, provider: str, started_perf: float) -> None:
        AI_JOBS.labels(status=status, provider=provider).inc()
        AI_JOB_DURATION.labels(status=status, provider=provider).observe(
            time.perf_counter() - started_perf
        )


async def async_main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.ai_worker_metrics_port:
        start_http_server(settings.ai_worker_metrics_port, addr="0.0.0.0")
        logger.info(
            "AI worker Prometheus endpoint started",
            extra={"port": settings.ai_worker_metrics_port},
        )
    worker = AIGenerationWorker(settings)
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, worker.request_stop)
        except NotImplementedError:
            pass
    await worker.run()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
