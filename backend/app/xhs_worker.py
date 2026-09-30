from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prometheus_client import start_http_server
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.process_stats import ProcessStatsSampler
from app.db.session import AsyncSessionFactory, engine
from app.services.article_media import article_delivery_media_path
from app.services.camoufox_client import CamoufoxServiceClient
from app.services.metrics import (
    XHS_JOB_DURATION,
    XHS_JOBS,
    XHS_QUEUE_DEPTH,
    XHS_WORKER_HEARTBEAT_METRIC,
)
from app.services.x_credentials import encrypt_token
from app.services.xhs_credentials import get_xhs_credentials
from app.services.xhs_jobs import (
    XHS_JOB_QUEUE,
    XHS_WORKER_HEARTBEAT,
    xhs_response_key,
)

logger = logging.getLogger(__name__)
UPLOAD_DIR = Path(os.getenv("XHS_UPLOAD_DIR", "/var/lib/xsentinel/xhs-uploads"))
RELEASE_HEARTBEAT_SCRIPT = """
local raw = redis.call('get', KEYS[1])
if not raw then
  return 0
end
local ok, heartbeat = pcall(cjson.decode, raw)
if ok and heartbeat['worker_id'] == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


def _validated_image_path(image: object) -> Path | None:
    path = Path(str(image)).resolve()
    if path.is_file() and UPLOAD_DIR in path.parents:
        return path
    return article_delivery_media_path(str(image))


class XiaohongshuWorker:
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
        self.process_stats = ProcessStatsSampler(include_children=True)
        self.browser_client = CamoufoxServiceClient(settings)
        self._browser_status_cache = {}
        self._browser_status_at = 0.0

    async def close(self):
        await self.browser_client.aclose()

    async def browser_status(self):
        if time.monotonic() - self._browser_status_at > 10:
            try:
                async with asyncio.timeout(3):
                    self._browser_status_cache = await self.browser_client.status()
            except TimeoutError:
                self._browser_status_cache = {"installed": False, "status": "offline"}
            self._browser_status_at = time.monotonic()
        return self._browser_status_cache

    def request_stop(self) -> None:
        self.stop_event.set()

    async def run(self) -> None:
        if not await self._wait_for_dependencies():
            return
        logger.info("X Sentinel Xiaohongshu worker started", extra={"worker_id": self.worker_id})
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        job_tasks: set[asyncio.Task[None]] = set()
        try:
            while not self.stop_event.is_set():
                try:
                    finished = {task for task in job_tasks if task.done()}
                    if finished:
                        await asyncio.gather(*finished, return_exceptions=True)
                        job_tasks.difference_update(finished)
                    if len(job_tasks) >= self.settings.xhs_browser_max_concurrency:
                        done, _ = await asyncio.wait(
                            job_tasks,
                            timeout=1,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if done:
                            await asyncio.gather(*done, return_exceptions=True)
                            job_tasks.difference_update(done)
                        await self._heartbeat()
                        continue
                    item = await self.redis.blpop(XHS_JOB_QUEUE, timeout=1)
                    if item:
                        job_tasks.add(asyncio.create_task(self._handle_job(item[1])))
                    await self._heartbeat()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Xiaohongshu worker loop failed")
        finally:
            for task in job_tasks:
                task.cancel()
            if job_tasks:
                await asyncio.gather(*job_tasks, return_exceptions=True)
            try:
                await asyncio.wait_for(self.close(), timeout=10)
            except TimeoutError:
                logger.warning("Timed out closing Camoufox HTTP client")
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
            await self._release_heartbeat()
            await self.redis.aclose()
            await engine.dispose()
            logger.info(
                "X Sentinel Xiaohongshu worker stopped",
                extra={"worker_id": self.worker_id},
            )

    async def _wait_for_dependencies(self, attempts: int = 10) -> bool:
        for attempt in range(1, attempts + 1):
            try:
                await self.redis.ping()
                async with AsyncSessionFactory() as session:
                    await session.execute(text("SELECT 1"))
                return True
            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt >= attempts:
                    logger.exception(
                        "Xiaohongshu worker dependencies unavailable",
                        extra={"attempt": attempt, "attempts": attempts},
                    )
                    raise
                delay = min(5.0, float(attempt))
                logger.warning(
                    "Xiaohongshu worker dependency check failed; retrying",
                    extra={"attempt": attempt, "attempts": attempts, "retry_in_seconds": delay},
                    exc_info=True,
                )
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=delay)
                except TimeoutError:
                    continue
                return False
        return False

    async def _release_heartbeat(self) -> None:
        try:
            await self.redis.eval(
                RELEASE_HEARTBEAT_SCRIPT,
                1,
                XHS_WORKER_HEARTBEAT,
                self.worker_id,
            )
        except Exception:
            logger.warning("Unable to release Xiaohongshu worker heartbeat", exc_info=True)

    async def _handle_job(self, raw: str) -> None:
        started = time.perf_counter()
        operation = "unknown"
        job_id = ""
        status = "failed"
        self.active_tasks += 1
        try:
            job = json.loads(raw)
            job_id = str(job["job_id"])
            operation = str(job["operation"])
            logger.info(
                "Xiaohongshu job started",
                extra={"job_id": job_id, "operation": operation, "admin_id": job.get("admin_id")},
            )
            data = await asyncio.wait_for(
                self._execute_job(job), timeout=self.settings.xhs_job_timeout_seconds
            )
            result = {"ok": True, "data": data}
            XHS_JOBS.labels(operation=operation, status="success").inc()
            status = "success"
        except Exception as exc:
            logger.exception(
                "Xiaohongshu job failed", extra={"job_id": job_id, "operation": operation}
            )
            result = {"ok": False, "error": str(exc)}
            XHS_JOBS.labels(operation=operation, status="failed").inc()
            status = "failed"
        finally:
            duration = time.perf_counter() - started
            XHS_JOB_DURATION.labels(operation=operation, status=status).observe(duration)
            self.active_tasks = max(0, self.active_tasks - 1)
        if job_id:
            await self.redis.set(
                xhs_response_key(job_id),
                json.dumps(result, ensure_ascii=False),
                ex=self.settings.xhs_job_result_ttl_seconds,
            )

    async def _execute_job(self, job: dict[str, Any]) -> dict[str, Any]:
        operation = str(job["operation"])
        admin_id = int(job["admin_id"])
        payload = job.get("payload") or {}
        if operation == "login":
            return await self.browser_client.submit(
                operation="login",
                admin_id=admin_id,
                payload=payload,
                timeout_seconds=self.settings.xhs_job_timeout_seconds,
                job_id=job["job_id"],
            )
        if operation == "post":
            return await self._post(admin_id, payload, job["job_id"])
        raise ValueError(f"Unsupported Xiaohongshu operation: {operation}")

    async def _post(self, admin_id: int, payload: dict[str, Any], job_id: str) -> dict[str, Any]:
        async with AsyncSessionFactory() as session:
            credentials = await get_xhs_credentials(session, self.settings, admin_id=admin_id)
        if credentials is None:
            raise RuntimeError("请先保存小红书登录态")
        image_paths: list[str] = []
        for image in payload.get("images") or []:
            path = await asyncio.to_thread(_validated_image_path, image)
            if path is None:
                raise RuntimeError("图片路径无效")
            image_paths.append(str(path))
        return await self.browser_client.submit(
            operation="post",
            admin_id=admin_id,
            payload={
                "encrypted_a1": encrypt_token(credentials.a1, self.settings),
                "encrypted_web_session": encrypt_token(credentials.web_session, self.settings),
                "cookie_version": credentials.version,
                "title": str(payload["title"]),
                "content": str(payload["content"]),
                "images": image_paths,
            },
            timeout_seconds=self.settings.xhs_job_timeout_seconds,
            job_id=job_id,
        )

    async def _heartbeat(self) -> None:
        now = datetime.now(UTC)
        queue_depth = int(await self.redis.llen(XHS_JOB_QUEUE))
        XHS_QUEUE_DEPTH.set(queue_depth)
        payload = {
            "worker_id": self.worker_id,
            "timestamp": now.isoformat().replace("+00:00", "Z"),
            "last_heartbeat": now.isoformat().replace("+00:00", "Z"),
            "active_tasks": self.active_tasks,
            "queue_depth": queue_depth,
            "browser_service": self.settings.camoufox_service_name,
            "browser_max_concurrency": self.settings.xhs_browser_max_concurrency,
            "installed": bool((await self.browser_status()).get("installed")),
            **self.process_stats.snapshot(),
        }
        await self.redis.set(
            XHS_WORKER_HEARTBEAT,
            json.dumps(payload),
            ex=self.settings.xhs_worker_heartbeat_ttl_seconds,
        )
        XHS_WORKER_HEARTBEAT_METRIC.set(now.timestamp())

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
    worker = XiaohongshuWorker(get_settings())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.request_stop)
    await worker.run()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.xhs_worker_metrics_port:
        start_http_server(settings.xhs_worker_metrics_port, addr="0.0.0.0")
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
