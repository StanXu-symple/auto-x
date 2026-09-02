from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import socket
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select, text

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.time import as_utc
from app.db.session import AsyncSessionFactory, engine
from app.models.xiaohongshu import XiaohongshuPublishJob, XiaohongshuPublishSetting
from app.services.xiaohongshu_connection import get_runtime
from app.services.xiaohongshu_mcp import XiaohongshuMCPClient, XiaohongshuMCPError

logger = logging.getLogger(__name__)
HEARTBEAT_KEY = "xsentinel:xhs-worker:heartbeat"
RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""


class XiaohongshuPublishWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )
        self.stop_event = asyncio.Event()
        self.active_tasks = 0

    def request_stop(self) -> None:
        self.stop_event.set()

    async def run(self) -> None:
        await self.redis.ping()
        async with AsyncSessionFactory() as session:
            await session.execute(text("SELECT 1"))
        logger.info("Xiaohongshu publish worker started", extra={"worker_id": self.worker_id})
        heartbeat = asyncio.create_task(self._heartbeat_loop())
        try:
            while not self.stop_event.is_set():
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Xiaohongshu publish scan failed")
                try:
                    await asyncio.wait_for(
                        self.stop_event.wait(),
                        timeout=self.settings.xhs_worker_scan_interval_seconds,
                    )
                except TimeoutError:
                    pass
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            await self.redis.aclose()
            await engine.dispose()
            logger.info("Xiaohongshu publish worker stopped")

    async def run_once(self) -> int:
        now = datetime.now(UTC)
        due = or_(
            and_(
                XiaohongshuPublishJob.status.in_(["queued", "retry_wait"]),
                XiaohongshuPublishJob.next_attempt_at <= now,
            ),
            and_(
                XiaohongshuPublishJob.status == "publishing",
                XiaohongshuPublishJob.lease_expires_at <= now,
            ),
        )
        async with AsyncSessionFactory() as session:
            settings = await session.get(XiaohongshuPublishSetting, 1)
            if settings is None or not settings.enabled:
                return 0
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            published_today = int(
                await session.scalar(
                    select(func.count(XiaohongshuPublishJob.id)).where(
                        XiaohongshuPublishJob.status == "published",
                        XiaohongshuPublishJob.published_at >= start_of_day,
                    )
                )
                or 0
            )
            remaining = max(0, settings.daily_publish_limit - published_today)
            if remaining == 0:
                return 0
            job_ids = list(
                await session.scalars(
                    select(XiaohongshuPublishJob.id)
                    .where(due)
                    .order_by(XiaohongshuPublishJob.next_attempt_at.asc())
                    .limit(remaining)
                )
            )
        for job_id in job_ids:
            if self.stop_event.is_set():
                break
            await self.process_job(job_id)
        return len(job_ids)

    async def process_job(self, job_id: int) -> bool:
        claim_token = str(uuid.uuid4())
        lock_key = f"xsentinel:xhs:publish-lock:{job_id}"
        acquired = await self.redis.set(
            lock_key,
            claim_token,
            nx=True,
            ex=self.settings.xhs_worker_lock_ttl_seconds,
        )
        if not acquired:
            return False
        self.active_tasks += 1
        try:
            job = await self._claim(job_id, claim_token)
            if job is None:
                return False
            async with AsyncSessionFactory() as session:
                runtime = await get_runtime(session, self.redis, self.settings)
            client = XiaohongshuMCPClient(
                runtime.mcp_url,
                runtime.auth_token,
                timeout_seconds=self.settings.xhs_request_timeout_seconds,
            )
            result = await client.publish_content(
                {
                    "title": job.title,
                    "content": job.content,
                    "images": job.images,
                    "tags": job.tags,
                    "visibility": job.visibility,
                    "is_original": job.is_original,
                    "products": job.products,
                }
            )
            await self._succeed(job_id, claim_token, result.raw, result.text)
            return True
        except XiaohongshuMCPError as exc:
            await self._fail(job_id, claim_token, str(exc), exc.retryable)
            return False
        except Exception as exc:
            logger.exception("Unexpected Xiaohongshu publishing failure", extra={"job_id": job_id})
            await self._fail(job_id, claim_token, str(exc), True)
            return False
        finally:
            self.active_tasks -= 1
            with suppress(Exception):
                await self.redis.eval(RELEASE_LOCK_SCRIPT, 1, lock_key, claim_token)

    async def _claim(self, job_id: int, claim_token: str) -> XiaohongshuPublishJob | None:
        now = datetime.now(UTC)
        async with AsyncSessionFactory() as session, session.begin():
            job = await session.get(XiaohongshuPublishJob, job_id, with_for_update=True)
            if job is None:
                return None
            if job.status not in {"queued", "retry_wait", "publishing"}:
                return None
            if (
                job.status != "publishing"
                and job.next_attempt_at
                and as_utc(job.next_attempt_at) > now
            ):
                return None
            if (
                job.status == "publishing"
                and job.lease_expires_at
                and as_utc(job.lease_expires_at) > now
            ):
                return None
            job.status = "publishing"
            job.claim_token = claim_token
            job.claimed_by = self.worker_id
            job.attempts += 1
            job.lease_expires_at = now + timedelta(
                seconds=self.settings.xhs_worker_lock_ttl_seconds
            )
            job.updated_at = now
        return job

    async def _succeed(
        self, job_id: int, claim_token: str, response: dict, text_result: str
    ) -> None:
        now = datetime.now(UTC)
        url_match = re.search(r"https?://[^\s\]\)\"']+", text_result)
        id_match = re.search(r"(?:note[_ ]?id|笔记ID)[：:= ]+([A-Za-z0-9_-]+)", text_result, re.I)
        async with AsyncSessionFactory() as session, session.begin():
            job = await session.get(XiaohongshuPublishJob, job_id, with_for_update=True)
            if job is None or job.claim_token != claim_token:
                return
            job.status = "published"
            job.published_at = now
            job.next_attempt_at = None
            job.lease_expires_at = None
            job.claim_token = None
            job.claimed_by = None
            job.last_error = None
            job.platform_url = url_match.group(0) if url_match else None
            job.platform_note_id = id_match.group(1) if id_match else None
            job.response_snapshot = {"result": response, "text": text_result[:4000]}
            job.updated_at = now

    async def _fail(
        self, job_id: int, claim_token: str, message: str, retryable: bool
    ) -> None:
        now = datetime.now(UTC)
        async with AsyncSessionFactory() as session, session.begin():
            job = await session.get(XiaohongshuPublishJob, job_id, with_for_update=True)
            if job is None or job.claim_token != claim_token:
                return
            if retryable and job.attempts < job.max_attempts:
                job.status = "retry_wait"
                job.next_attempt_at = now + timedelta(minutes=min(30, 2 ** job.attempts))
            else:
                job.status = "failed"
                job.next_attempt_at = None
            job.last_error = message[:2000]
            job.lease_expires_at = None
            job.claim_token = None
            job.claimed_by = None
            job.updated_at = now

    async def _heartbeat(self) -> None:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        await self.redis.set(
            HEARTBEAT_KEY,
            json.dumps(
                {
                    "worker_id": self.worker_id,
                    "status": "running",
                    "last_heartbeat": now,
                    "active_tasks": self.active_tasks,
                }
            ),
            ex=self.settings.xhs_worker_heartbeat_ttl_seconds,
        )

    async def _heartbeat_loop(self) -> None:
        interval = max(3.0, self.settings.xhs_worker_heartbeat_ttl_seconds / 3)
        while not self.stop_event.is_set():
            try:
                await self._heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Xiaohongshu worker heartbeat failed")
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=interval)
            except TimeoutError:
                pass


async def async_main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    worker = XiaohongshuPublishWorker(settings)
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
