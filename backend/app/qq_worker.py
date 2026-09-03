from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import nonebot
from nonebot import get_adapter, get_driver
from nonebot.adapters.qq import (
    ActionFailed,
    Bot,
    NetworkError,
    RateLimitException,
    UnauthorizedException,
)
from nonebot.adapters.qq import Adapter as QQAdapter
from nonebot.adapters.qq.config import BotInfo
from prometheus_client import start_http_server
from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select, text

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.process_stats import ProcessStatsSampler
from app.core.time import as_utc
from app.db.session import AsyncSessionFactory, engine
from app.models.qq import QQBotAccount, QQDelivery, QQNotificationTarget
from app.services.metrics import (
    QQ_DELIVERIES,
    QQ_DELIVERY_DURATION,
    QQ_QUEUE_DUE,
    QQ_WORKER_HEARTBEAT_METRIC,
)
from app.services.qq_notifications import (
    QQ_DELIVERY_QUEUE,
    QQ_WORKER_HEARTBEAT,
    decrypt_app_secret,
    delivery_message_id,
)

logger = logging.getLogger(__name__)

RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""


@dataclass(slots=True)
class QQDeliveryClaim:
    delivery_id: int
    claim_token: str
    bot_id: int
    bot_version: int
    app_id: str
    app_secret: str
    group_openid: str
    message_body: str
    attempts: int
    max_attempts: int


class QQSender(Protocol):
    async def send_group(self, claim: QQDeliveryClaim) -> str | None: ...


class NoneBotQQSender:
    def __init__(self, adapter: QQAdapter, settings: Settings) -> None:
        self.adapter = adapter
        self.settings = settings
        self.bots: dict[tuple[int, int], Bot] = {}

    async def send_group(self, claim: QQDeliveryClaim) -> str | None:
        key = (claim.bot_id, claim.bot_version)
        bot = self.bots.get(key)
        if bot is None:
            for cached_key in [item for item in self.bots if item[0] == claim.bot_id]:
                self.bots.pop(cached_key, None)
            bot = Bot(
                self.adapter,
                claim.app_id,
                BotInfo(
                    id=claim.app_id,
                    token="",
                    secret=claim.app_secret,
                    use_websocket=False,
                ),
            )
            self.bots[key] = bot
        result = await bot.send_to_group(
            group_openid=claim.group_openid,
            message=claim.message_body,
        )
        return delivery_message_id(result)


class QQDeliveryWorker:
    def __init__(self, settings: Settings, sender: QQSender) -> None:
        self.settings = settings
        self.sender = sender
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )
        self.stop_event = asyncio.Event()
        self.active_tasks = 0
        self.process_stats = ProcessStatsSampler()

    def request_stop(self) -> None:
        self.stop_event.set()

    async def check_dependencies(self) -> None:
        await self.redis.ping()
        async with AsyncSessionFactory() as session:
            await session.execute(text("SELECT 1"))

    async def run(self, *, check_dependencies: bool = True) -> None:
        if check_dependencies:
            await self.check_dependencies()
        logger.info("X Sentinel QQ worker started", extra={"worker_id": self.worker_id})
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            while not self.stop_event.is_set():
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("QQ worker scan failed", extra={"worker_id": self.worker_id})
                try:
                    await asyncio.wait_for(
                        self.stop_event.wait(),
                        timeout=self.settings.qq_worker_scan_interval_seconds,
                    )
                except TimeoutError:
                    pass
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
            await self.redis.aclose()
            await engine.dispose()
            logger.info("X Sentinel QQ worker stopped", extra={"worker_id": self.worker_id})

    async def run_once(self) -> int:
        await self._heartbeat()
        now = datetime.now(UTC)
        due_condition = or_(
            and_(
                QQDelivery.status.in_(["queued", "retry_wait"]),
                QQDelivery.next_attempt_at <= now,
            ),
            and_(
                QQDelivery.status == "sending",
                QQDelivery.lease_expires_at.is_not(None),
                QQDelivery.lease_expires_at <= now,
            ),
        )
        queued_ids: list[int] = []
        try:
            raw_ids = await self.redis.lpop(
                QQ_DELIVERY_QUEUE, count=self.settings.qq_worker_batch_size
            )
            if isinstance(raw_ids, str):
                raw_ids = [raw_ids]
            queued_ids = [int(item) for item in raw_ids or []]
        except Exception:
            logger.warning("Unable to read QQ Redis queue; scanning database", exc_info=True)
        async with AsyncSessionFactory() as session:
            due_count = int(
                await session.scalar(select(func.count(QQDelivery.id)).where(due_condition)) or 0
            )
            database_ids = list(
                await session.scalars(
                    select(QQDelivery.id)
                    .where(due_condition)
                    .order_by(QQDelivery.next_attempt_at.asc(), QQDelivery.id.asc())
                    .limit(self.settings.qq_worker_batch_size)
                )
            )
        QQ_QUEUE_DUE.set(due_count)
        delivery_ids = list(dict.fromkeys([*queued_ids, *database_ids]))[
            : self.settings.qq_worker_batch_size
        ]
        if not delivery_ids:
            return 0

        semaphore = asyncio.Semaphore(self.settings.qq_worker_max_concurrency)

        async def deliver_one(delivery_id: int) -> None:
            async with semaphore:
                self.active_tasks += 1
                try:
                    await self.process_delivery(delivery_id)
                finally:
                    self.active_tasks -= 1

        tasks = [asyncio.create_task(deliver_one(delivery_id)) for delivery_id in delivery_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                logger.error(
                    "Unhandled QQ delivery task error",
                    exc_info=(type(result), result, result.__traceback__),
                )
        await self._heartbeat()
        return len(delivery_ids)

    async def process_delivery(self, delivery_id: int) -> bool:
        lock_key = f"xsentinel:qq:lock:{delivery_id}"
        claim_token = str(uuid.uuid4())
        try:
            acquired = await self.redis.set(
                lock_key,
                claim_token,
                nx=True,
                ex=self.settings.qq_worker_lock_ttl_seconds,
            )
        except Exception:
            logger.exception("QQ delivery lock failed", extra={"delivery_id": delivery_id})
            return False
        if not acquired:
            return False

        started_perf = time.perf_counter()
        try:
            claim = await self._claim(delivery_id, claim_token)
            if claim is None:
                return False
            provider_message_id = await self.sender.send_group(claim)
            applied = await self._commit_success(claim, provider_message_id)
            outcome = "sent" if applied else "superseded"
            QQ_DELIVERIES.labels(status=outcome).inc()
            QQ_DELIVERY_DURATION.labels(status=outcome).observe(time.perf_counter() - started_perf)
            return applied
        except Exception as exc:
            retryable = self._retryable(exc)
            outcome = await self._commit_failure(
                delivery_id, claim_token, str(exc), retryable=retryable
            )
            QQ_DELIVERIES.labels(status=outcome).inc()
            QQ_DELIVERY_DURATION.labels(status=outcome).observe(time.perf_counter() - started_perf)
            if outcome in {"failed", "retry_wait"}:
                logger.warning(
                    "QQ delivery failed",
                    extra={"delivery_id": delivery_id, "status": outcome, "error": str(exc)},
                )
            return False
        finally:
            with suppress(Exception):
                await self.redis.eval(RELEASE_LOCK_SCRIPT, 1, lock_key, claim_token)

    async def _claim(self, delivery_id: int, claim_token: str) -> QQDeliveryClaim | None:
        now = datetime.now(UTC)
        async with AsyncSessionFactory() as session, session.begin():
            delivery = await session.get(QQDelivery, delivery_id, with_for_update=True)
            if delivery is None:
                return None
            due = (
                delivery.status in {"queued", "retry_wait"}
                and as_utc(delivery.next_attempt_at) <= now
            )
            stale = (
                delivery.status == "sending"
                and delivery.lease_expires_at is not None
                and as_utc(delivery.lease_expires_at) <= now
            )
            if not due and not stale:
                return None
            target = await session.get(QQNotificationTarget, delivery.target_id)
            bot = await session.get(QQBotAccount, target.bot_id) if target else None
            if target is None or bot is None or not target.is_enabled or not bot.is_enabled:
                delivery.status = "cancelled"
                delivery.last_error = "机器人或群通知目标已删除或停用"
                delivery.completed_at = now
                delivery.claim_token = None
                delivery.claimed_by = None
                delivery.lease_expires_at = None
                return None
            try:
                app_secret = decrypt_app_secret(bot.encrypted_app_secret, self.settings)
            except Exception as exc:
                delivery.status = "failed"
                delivery.attempts += 1
                delivery.last_error = str(exc)[:2000]
                delivery.completed_at = now
                delivery.claim_token = None
                delivery.claimed_by = None
                delivery.lease_expires_at = None
                return None
            delivery.status = "sending"
            delivery.attempts += 1
            delivery.claim_token = claim_token
            delivery.claimed_by = self.worker_id
            delivery.started_at = now
            delivery.lease_expires_at = now + timedelta(
                seconds=self.settings.qq_worker_lock_ttl_seconds
            )
            delivery.last_error = None
            return QQDeliveryClaim(
                delivery_id=delivery.id,
                claim_token=claim_token,
                bot_id=bot.id,
                bot_version=bot.version,
                app_id=bot.app_id,
                app_secret=app_secret,
                group_openid=target.group_openid,
                message_body=delivery.message_body,
                attempts=delivery.attempts,
                max_attempts=delivery.max_attempts,
            )

    async def _commit_success(
        self, claim: QQDeliveryClaim, provider_message_id: str | None
    ) -> bool:
        async with AsyncSessionFactory() as session, session.begin():
            delivery = await session.get(QQDelivery, claim.delivery_id, with_for_update=True)
            if delivery is None or delivery.claim_token != claim.claim_token:
                return False
            delivery.status = "sent"
            delivery.provider_message_id = provider_message_id
            delivery.completed_at = datetime.now(UTC)
            delivery.claim_token = None
            delivery.claimed_by = None
            delivery.lease_expires_at = None
            return True

    async def _commit_failure(
        self, delivery_id: int, claim_token: str, message: str, *, retryable: bool
    ) -> str:
        async with AsyncSessionFactory() as session, session.begin():
            delivery = await session.get(QQDelivery, delivery_id, with_for_update=True)
            if delivery is None or delivery.claim_token != claim_token:
                return "superseded"
            now = datetime.now(UTC)
            if retryable and delivery.attempts < delivery.max_attempts:
                delivery.status = "retry_wait"
                delivery.next_attempt_at = now + timedelta(
                    seconds=min(900, 10 * (2 ** max(0, delivery.attempts - 1)))
                )
                outcome = "retry_wait"
            else:
                delivery.status = "failed"
                delivery.completed_at = now
                outcome = "failed"
            delivery.last_error = message[:2000]
            delivery.claim_token = None
            delivery.claimed_by = None
            delivery.lease_expires_at = None
            return outcome

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        if isinstance(exc, (NetworkError, RateLimitException)):
            return True
        if isinstance(exc, UnauthorizedException):
            return False
        return isinstance(exc, ActionFailed) and (
            exc.status_code == 429 or exc.status_code >= 500
        )

    async def _heartbeat(self) -> None:
        now = datetime.now(UTC)
        payload = json.dumps(
            {
                "worker_id": self.worker_id,
                "timestamp": now.isoformat().replace("+00:00", "Z"),
                "last_heartbeat": now.isoformat().replace("+00:00", "Z"),
                "active_tasks": self.active_tasks,
                **self.process_stats.snapshot(),
            }
        )
        await self.redis.set(
            QQ_WORKER_HEARTBEAT,
            payload,
            ex=self.settings.qq_worker_heartbeat_ttl_seconds,
        )
        QQ_WORKER_HEARTBEAT_METRIC.set(now.timestamp())

    async def _heartbeat_loop(self) -> None:
        interval = max(3.0, self.settings.qq_worker_heartbeat_ttl_seconds / 3)
        while not self.stop_event.is_set():
            try:
                await self._heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("QQ worker heartbeat failed")
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=interval)
            except TimeoutError:
                pass


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.qq_worker_metrics_port:
        start_http_server(settings.qq_worker_metrics_port, addr="0.0.0.0")
    nonebot.init(
        driver="~fastapi+~httpx",
        host="0.0.0.0",
        port=settings.qq_worker_port,
        qq_api_base=settings.qq_api_base_url,
        qq_auth_base=settings.qq_auth_url,
        qq_bots=[],
    )
    driver = get_driver()
    driver.register_adapter(QQAdapter)
    sender = NoneBotQQSender(get_adapter(QQAdapter), settings)
    worker = QQDeliveryWorker(settings, sender)
    worker_task: asyncio.Task[None] | None = None

    @driver.on_startup
    async def start_worker() -> None:
        nonlocal worker_task
        await worker.check_dependencies()
        worker_task = asyncio.create_task(worker.run(check_dependencies=False))

    @driver.on_shutdown
    async def stop_worker() -> None:
        worker.request_stop()
        if worker_task is not None:
            with suppress(TimeoutError):
                await asyncio.wait_for(worker_task, timeout=15)

    nonebot.run()


if __name__ == "__main__":
    main()
