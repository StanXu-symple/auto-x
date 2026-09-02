from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import uuid
from contextlib import suppress
from datetime import UTC, datetime

from prometheus_client import start_http_server
from redis.asyncio import Redis
from sqlalchemy import case, or_, select, text

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.process_stats import ProcessStatsSampler
from app.db.session import AsyncSessionFactory, engine
from app.models.monitored_user import MonitoredUser
from app.services.metrics import POLL_QUEUE_DUE, WORKER_HEARTBEAT
from app.services.poller import GLOBAL_X_GATE_KEY, PollingService
from app.services.settings_service import get_polling_settings
from app.services.x_source_client import XSourceClient

logger = logging.getLogger(__name__)


class PollingWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )
        self.x_client = XSourceClient(
            session_factory=AsyncSessionFactory,
            redis=self.redis,
            settings=settings,
        )
        self.poller = PollingService(
            session_factory=AsyncSessionFactory,
            redis=self.redis,
            x_client=self.x_client,
            settings=settings,
            worker_id=self.worker_id,
        )
        self.stop_event = asyncio.Event()
        self.active_tasks = 0
        self.process_stats = ProcessStatsSampler()

    def request_stop(self) -> None:
        self.stop_event.set()

    async def run(self) -> None:
        await self.redis.ping()
        async with AsyncSessionFactory() as session:
            await session.execute(text("SELECT 1"))
        logger.info("X Sentinel polling worker started", extra={"worker_id": self.worker_id})
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            while not self.stop_event.is_set():
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Worker scan failed", extra={"worker_id": self.worker_id})
                try:
                    await asyncio.wait_for(
                        self.stop_event.wait(), timeout=self.settings.worker_scan_interval_seconds
                    )
                except TimeoutError:
                    pass
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
            logger.info("X Sentinel polling worker stopping", extra={"worker_id": self.worker_id})
            await self.x_client.aclose()
            await self.redis.aclose()
            await engine.dispose()

    async def run_once(self) -> int:
        await self._heartbeat()
        if await self.redis.exists(GLOBAL_X_GATE_KEY):
            POLL_QUEUE_DUE.set(0)
            logger.warning("Global X API gate is active; dispatch is paused")
            return 0
        now = datetime.now(UTC)
        async with AsyncSessionFactory() as session:
            polling_settings = await get_polling_settings(session, self.settings)
            user_ids = list(
                await session.scalars(
                    select(MonitoredUser.id)
                    .where(
                        MonitoredUser.is_active.is_(True),
                        or_(
                            MonitoredUser.manual_poll_token.is_not(None),
                            MonitoredUser.next_poll_at.is_(None),
                            MonitoredUser.next_poll_at <= now,
                        ),
                    )
                    .order_by(
                        case((MonitoredUser.manual_poll_token.is_not(None), 1), else_=0).desc(),
                        MonitoredUser.next_poll_at.asc(),
                    )
                    .limit(self.settings.worker_batch_size)
                )
            )
        POLL_QUEUE_DUE.set(len(user_ids))
        if not user_ids:
            return 0

        semaphore = asyncio.Semaphore(polling_settings["max_concurrency"])

        async def poll_one(user_id: int) -> None:
            async with semaphore:
                self.active_tasks += 1
                try:
                    if not await self.redis.exists(GLOBAL_X_GATE_KEY):
                        await self.poller.poll_user(user_id)
                finally:
                    self.active_tasks -= 1

        poll_tasks = [asyncio.create_task(poll_one(user_id)) for user_id in user_ids]
        await self._wait_for_poll_tasks(poll_tasks)
        await self._heartbeat()
        return len(user_ids)

    async def _wait_for_poll_tasks(self, poll_tasks: list[asyncio.Task[None]]) -> None:
        async def wait_batch() -> None:
            await asyncio.gather(*poll_tasks)

        batch_task = asyncio.create_task(wait_batch())
        stop_task = asyncio.create_task(self.stop_event.wait())
        done, _ = await asyncio.wait({batch_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if stop_task in done and not batch_task.done():
            batch_task.cancel()
            await asyncio.gather(batch_task, return_exceptions=True)
        else:
            await batch_task
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)

    async def _heartbeat(self) -> None:
        now = datetime.now(UTC)
        process_stats = self.process_stats.snapshot()
        payload = json.dumps(
            {
                "worker_id": self.worker_id,
                "timestamp": now.isoformat().replace("+00:00", "Z"),
                "last_heartbeat": now.isoformat().replace("+00:00", "Z"),
                "active_tasks": self.active_tasks,
                **process_stats,
            }
        )
        await self.redis.set(
            "xsentinel:worker:heartbeat",
            payload,
            ex=self.settings.worker_heartbeat_ttl_seconds,
        )
        WORKER_HEARTBEAT.set(now.timestamp())

    async def _heartbeat_loop(self) -> None:
        interval = max(3.0, self.settings.worker_heartbeat_ttl_seconds / 3)
        while not self.stop_event.is_set():
            try:
                await self._heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Worker heartbeat failed", extra={"worker_id": self.worker_id})
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=interval)
            except TimeoutError:
                pass


async def async_main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.worker_metrics_port:
        start_http_server(settings.worker_metrics_port, addr="0.0.0.0")
        logger.info(
            "Worker Prometheus endpoint started",
            extra={"port": settings.worker_metrics_port},
        )
    worker = PollingWorker(settings)
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
