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
from pathlib import Path
from typing import Any, Protocol

import nonebot
from nonebot import get_adapter, get_driver, on_type
from nonebot.adapters.qq import (
    ActionFailed,
    Bot,
    GroupAddRobotEvent,
    GroupDelRobotEvent,
    GroupMessageCreateEvent,
    MessageSegment,
    NetworkError,
    RateLimitException,
    UnauthorizedException,
)
from nonebot.adapters.qq import Adapter as QQAdapter
from nonebot.adapters.qq.config import BotInfo, Intents
from prometheus_client import start_http_server
from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select, text

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.process_stats import ProcessStatsSampler
from app.core.time import as_utc
from app.db.session import AsyncSessionFactory, engine
from app.models.ai import AIDraft, ArticlePublishAttempt
from app.models.qq import (
    QQBotAccount,
    QQDelivery,
    QQJoinedGroup,
    QQNotificationTarget,
    QQScheduledTask,
    QQScheduledTaskBot,
    QQScheduledTaskGroup,
)
from app.services.article_media import article_delivery_media_path
from app.services.metrics import (
    QQ_DELIVERIES,
    QQ_DELIVERY_DURATION,
    QQ_QUEUE_DUE,
    QQ_WORKER_HEARTBEAT_METRIC,
)
from app.services.qq_groups import record_group_presence
from app.services.qq_notifications import (
    QQ_BOT_STATUS,
    QQ_DELIVERY_QUEUE,
    QQ_WORKER_HEARTBEAT,
    decrypt_app_secret,
    delivery_message_id,
)
from app.services.qq_schedule import next_qq_task_run

logger = logging.getLogger(__name__)

QQ_SEND_GATE = "xsentinel:qq:send-gate"
QQ_LAST_SENT_AT = "xsentinel:qq:last-sent-at"
QQ_SEND_PROGRESS_INTERVAL_SECONDS = 15.0

GROUP_ADD_ROBOT_ACK = "机器人已入群，请在 X Sentinel 中选择本群并配置推送目标。"


async def acknowledge_group_add(bot: Bot, event: GroupAddRobotEvent) -> None:
    """Acknowledge QQ's GROUP_ADD_ROBOT dispatch in the group.

    QQ sends this event only after the group administrator has added the robot;
    there is no API call that approves the robot's own invitation.  Replying
    with the event id provides a visible confirmation to the operator;
    transport acknowledgement is handled separately by the adapter.
    """
    await bot.send(event, GROUP_ADD_ROBOT_ACK)


async def handle_group_event(
    bot: Bot,
    event: GroupAddRobotEvent | GroupDelRobotEvent | GroupMessageCreateEvent,
) -> None:
    # Membership persists even when the optional welcome message is rejected.
    event_at = event.timestamp
    if isinstance(event_at, str):
        event_at = datetime.fromisoformat(event_at.replace("Z", "+00:00"))
    async with AsyncSessionFactory() as session:
        changed = await record_group_presence(
            session,
            app_id=bot.self_id,
            group_openid=event.group_openid,
            is_joined=not isinstance(event, GroupDelRobotEvent),
            event_at=event_at,
        )
        await session.commit()
    if changed and isinstance(event, GroupAddRobotEvent):
        try:
            await acknowledge_group_add(bot, event)
        except Exception:
            logger.exception("Unable to send QQ group welcome message")


def register_group_event_handlers() -> None:
    # GroupAtMessageCreateEvent inherits GroupMessageCreateEvent. Observing an
    # @ message discovers groups joined before this worker was configured.
    matcher = on_type(
        (GroupAddRobotEvent, GroupDelRobotEvent, GroupMessageCreateEvent), block=False
    )
    matcher.append_handler(handle_group_event)


async def load_inbound_bot_infos() -> list[BotInfo]:
    """Build webhook bot entries from the encrypted bot accounts in PostgreSQL."""
    async with AsyncSessionFactory() as session:
        rows = (
            await session.scalars(select(QQBotAccount).where(QQBotAccount.is_enabled.is_(True)))
        ).all()
    settings = get_settings()
    result: list[BotInfo] = []
    for row in rows:
        try:
            secret = decrypt_app_secret(row.encrypted_app_secret, settings)
        except Exception:
            logger.exception(
                "Unable to load QQ bot secret for inbound events", extra={"bot_id": row.id}
            )
            continue
        result.append(
            BotInfo(
                id=row.app_id,
                token="",
                secret=secret,
                # Keep the long-lived gateway connection online. The same
                # BotInfo is also accepted by the webhook handler.
                use_websocket=True,
                intent=Intents(c2c_group_at_messages=True),
            )
        )
    return result


async def refresh_inbound_bots(
    adapter: QQAdapter, websocket_started: set[str] | None = None
) -> None:
    infos = await load_inbound_bot_infos()
    configured = {info.id: info for info in infos}
    adapter.qq_config.qq_bots = infos
    # The adapter prefers connected bots to qq_bots when verifying webhooks.
    # Evict cached credentials when an account is disabled/deleted/rotated.
    for bot in list(adapter.bots.values()):
        info = configured.get(bot.self_id)
        if info is None or bot.bot_info.secret != info.secret:
            adapter.bot_disconnect(bot)
    if websocket_started is None:
        return
    for info in infos:
        if info.id in websocket_started:
            continue
        before = set(adapter.tasks)
        await adapter.run_bot_websocket(info)
        # run_bot_websocket creates one or more reconnecting tasks. Mark the
        # AppID only when at least one task was actually created, so a failed
        # gateway lookup is retried by the next sync pass.
        if set(adapter.tasks) - before:
            websocket_started.add(info.id)


async def publish_bot_status(redis: Redis, adapter: QQAdapter, *, ttl: int = 30) -> None:
    """Publish per-AppID Gateway state for the administration UI."""
    status = {info.id: "connecting" for info in adapter.qq_config.qq_bots}
    for bot in adapter.bots.values():
        status[bot.self_id] = "online" if bot.ready else "connecting"
    await redis.set(QQ_BOT_STATUS, json.dumps(status), ex=ttl)


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
    kind: str = "unknown"
    media_path: str | None = None
    article_id: int | None = None
    sequence: int | None = None


@dataclass(frozen=True, slots=True)
class QQFailureCommitResult:
    outcome: str
    attempt: int | None = None
    max_attempts: int | None = None
    retry_delay_seconds: int | None = None
    next_attempt_at: datetime | None = None


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
        message = claim.message_body
        if claim.media_path:
            path = article_delivery_media_path(claim.media_path)
            if path is None:
                raise RuntimeError("文章图片不存在或路径无效")
            message = MessageSegment.file_image(Path(path))
        result = await bot.send_to_group(
            group_openid=claim.group_openid,
            message=message,
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
        await self._schedule_tasks()
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

    async def _schedule_tasks(self) -> None:
        now = datetime.now(UTC)
        async with AsyncSessionFactory() as session, session.begin():
            tasks = list(
                await session.scalars(
                    select(QQScheduledTask)
                    .where(
                        QQScheduledTask.is_enabled.is_(True),
                        QQScheduledTask.next_run_at <= now,
                    )
                    .with_for_update()
                )
            )
            for task in tasks:
                bots = set(
                    await session.scalars(
                        select(QQScheduledTaskBot.bot_id).where(
                            QQScheduledTaskBot.task_id == task.id
                        )
                    )
                )
                groups = list(
                    (
                        await session.execute(
                            select(
                                QQScheduledTaskGroup.bot_id,
                                QQScheduledTaskGroup.group_openid,
                            ).where(QQScheduledTaskGroup.task_id == task.id)
                        )
                    ).tuples()
                )
                for bot_id, group in groups:
                    if bot_id not in bots:
                        continue
                    bot = await session.get(QQBotAccount, bot_id)
                    if bot and bot.is_enabled:
                        session.add(
                            QQDelivery(
                                task_id=task.id,
                                target_id=None,
                                source_tweet_id=None,
                                kind="scheduled",
                                idempotency_key=(
                                    f"scheduled:{task.id}:{now.isoformat()}:{bot_id}:{group}"
                                ),
                                bot_name=bot.name,
                                bot_app_id=bot.app_id,
                                bot_version=bot.version,
                                target_name=group,
                                group_openid=group,
                                message_body=task.message,
                                status="queued",
                                attempts=0,
                                max_attempts=self.settings.qq_worker_max_attempts,
                                next_attempt_at=now,
                            )
                        )
                task.last_run_at = now
                task.next_run_at = next_qq_task_run(
                    frequency=task.frequency,
                    interval_value=task.interval_value,
                    run_time=task.run_time,
                    weekdays=task.weekdays,
                    month_day=task.month_day,
                    now=now,
                    timezone_name=self.settings.app_timezone,
                )

    async def process_delivery(self, delivery_id: int) -> bool:
        started_perf = time.perf_counter()
        log_context: dict[str, Any] = {
            "delivery_id": delivery_id,
            "worker_id": self.worker_id,
            "attempt": None,
            "max_attempts": None,
        }
        self._log_stage(
            "QQ delivery entered processing",
            "delivery_discovered",
            started_perf,
            log_context,
        )
        self._log_stage(
            "QQ delivery waiting for global send gate",
            "send_gate_wait_started",
            started_perf,
            log_context,
        )
        gate_wait_started = time.perf_counter()
        gate_token = await self._acquire_send_gate(
            started_perf=started_perf,
            log_context=log_context,
        )
        try:
            self._log_stage(
                "QQ global send gate acquired",
                "send_gate_acquired",
                started_perf,
                log_context,
                gate_wait_ms=round((time.perf_counter() - gate_wait_started) * 1000),
            )
            await self._wait_for_send_interval(
                started_perf=started_perf,
                log_context=log_context,
            )
            return await self._process_delivery_locked(
                delivery_id,
                started_perf=started_perf,
                log_context=log_context,
            )
        finally:
            with suppress(Exception):
                await self.redis.eval(RELEASE_LOCK_SCRIPT, 1, QQ_SEND_GATE, gate_token)
            self._log_stage(
                "QQ global send gate released",
                "send_gate_released",
                started_perf,
                log_context,
            )

    async def _process_delivery_locked(
        self,
        delivery_id: int,
        *,
        started_perf: float,
        log_context: dict[str, Any],
    ) -> bool:
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
            logger.exception(
                "QQ delivery lock failed",
                extra=self._stage_extra(
                    "delivery_lock_failed", started_perf, log_context
                ),
            )
            return False
        if not acquired:
            self._log_stage(
                "QQ delivery is already owned by another worker",
                "delivery_lock_not_acquired",
                started_perf,
                log_context,
            )
            return False

        self._log_stage(
            "QQ delivery lock acquired",
            "delivery_lock_acquired",
            started_perf,
            log_context,
        )
        metric_started_perf = time.perf_counter()
        try:
            self._log_stage(
                "QQ delivery claim started",
                "delivery_claim_started",
                started_perf,
                log_context,
            )
            claim = await self._claim(delivery_id, claim_token)
            if claim is None:
                self._log_stage(
                    "QQ delivery was no longer claimable",
                    "delivery_claim_skipped",
                    started_perf,
                    log_context,
                )
                return False
            log_context.update(
                {
                    "attempt": claim.attempts,
                    "max_attempts": claim.max_attempts,
                    "delivery_kind": claim.kind,
                    "delivery_type": "image" if claim.media_path else "text",
                    "message_character_count": len(claim.message_body),
                    "bot_id": claim.bot_id,
                    "article_id": claim.article_id,
                    "sequence": claim.sequence,
                }
            )
            self._log_stage(
                "QQ delivery claimed and credentials resolved",
                "delivery_claimed",
                started_perf,
                log_context,
            )
            await self.redis.set(QQ_LAST_SENT_AT, str(time.time()), ex=3600)
            self._log_stage(
                "QQ API send started",
                "qq_api_send_started",
                started_perf,
                log_context,
            )
            provider_message_id = await self._send_with_progress(
                claim,
                started_perf=started_perf,
                log_context=log_context,
            )
            response_fields: dict[str, Any] = {
                "provider_message_id_present": bool(provider_message_id)
            }
            if provider_message_id:
                response_fields["provider_message_id"] = str(provider_message_id)[:128]
            self._log_stage(
                "QQ API accepted delivery",
                "qq_api_send_completed",
                started_perf,
                log_context,
                **response_fields,
            )
            self._log_stage(
                "QQ delivery result persistence started",
                "delivery_persistence_started",
                started_perf,
                log_context,
            )
            applied = await self._commit_success(claim, provider_message_id)
            outcome = "sent" if applied else "superseded"
            if applied:
                self._log_stage(
                    "QQ delivery completed successfully",
                    "delivery_completed",
                    started_perf,
                    log_context,
                    outcome=outcome,
                    **response_fields,
                )
            else:
                logger.warning(
                    "QQ delivery result was superseded before persistence",
                    extra=self._stage_extra(
                        "delivery_superseded",
                        started_perf,
                        log_context,
                        outcome=outcome,
                    ),
                )
            QQ_DELIVERIES.labels(status=outcome).inc()
            QQ_DELIVERY_DURATION.labels(status=outcome).observe(
                time.perf_counter() - metric_started_perf
            )
            return applied
        except Exception as exc:
            retryable = self._retryable(exc)
            failure = await self._commit_failure(
                delivery_id, claim_token, str(exc), retryable=retryable
            )
            self._log_failure_outcome(
                failure,
                exc,
                retryable=retryable,
                started_perf=started_perf,
                log_context=log_context,
            )
            QQ_DELIVERIES.labels(status=failure.outcome).inc()
            QQ_DELIVERY_DURATION.labels(status=failure.outcome).observe(
                time.perf_counter() - metric_started_perf
            )
            return False
        finally:
            with suppress(Exception):
                await self.redis.eval(RELEASE_LOCK_SCRIPT, 1, lock_key, claim_token)

    async def _acquire_send_gate(
        self,
        *,
        started_perf: float,
        log_context: dict[str, Any],
    ) -> str:
        token = f"{self.worker_id}:{uuid.uuid4().hex}"
        ttl = max(30, int(self.settings.qq_worker_send_interval_seconds) + 30)
        last_progress_at = time.perf_counter()
        while not self.stop_event.is_set():
            try:
                if await self.redis.set(QQ_SEND_GATE, token, nx=True, ex=ttl):
                    return token
            except Exception:
                logger.warning(
                    "QQ send gate unavailable; retrying",
                    exc_info=True,
                    extra=self._stage_extra(
                        "send_gate_error", started_perf, log_context
                    ),
                )
            now_perf = time.perf_counter()
            if now_perf - last_progress_at >= QQ_SEND_PROGRESS_INTERVAL_SECONDS:
                self._log_stage(
                    "QQ delivery is still waiting for global send gate",
                    "send_gate_wait_in_progress",
                    started_perf,
                    log_context,
                )
                last_progress_at = now_perf
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=0.2)
            except TimeoutError:
                pass
        raise asyncio.CancelledError

    async def _wait_for_send_interval(
        self,
        *,
        started_perf: float,
        log_context: dict[str, Any],
    ) -> None:
        interval = self.settings.qq_worker_send_interval_seconds
        if interval <= 0:
            return
        try:
            last_sent = await self.redis.get(QQ_LAST_SENT_AT)
            remaining = interval - (time.time() - float(last_sent)) if last_sent else 0
        except (TypeError, ValueError, OSError):
            remaining = 0
        if remaining > 0:
            self._log_stage(
                "QQ delivery waiting for configured send interval",
                "send_interval_wait_started",
                started_perf,
                log_context,
                wait_seconds=round(remaining, 3),
            )
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=remaining)
            except TimeoutError:
                pass
            self._log_stage(
                "QQ configured send interval elapsed",
                "send_interval_wait_completed",
                started_perf,
                log_context,
                wait_seconds=round(remaining, 3),
            )

    async def _send_with_progress(
        self,
        claim: QQDeliveryClaim,
        *,
        started_perf: float,
        log_context: dict[str, Any],
    ) -> str | None:
        send_started_perf = time.perf_counter()
        send_task = asyncio.create_task(self.sender.send_group(claim))
        try:
            while True:
                done, _ = await asyncio.wait(
                    {send_task},
                    timeout=QQ_SEND_PROGRESS_INTERVAL_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if send_task in done:
                    return await send_task
                self._log_stage(
                    "QQ API send is still in progress",
                    "qq_api_send_in_progress",
                    started_perf,
                    log_context,
                    send_elapsed_ms=round(
                        (time.perf_counter() - send_started_perf) * 1000
                    ),
                )
        finally:
            if not send_task.done():
                send_task.cancel()
            await asyncio.gather(send_task, return_exceptions=True)

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
            cancel_reason = None
            if delivery.kind in {"batch", "scheduled", "article"}:
                # Manual batches select a joined group without a subscription target.
                bot = await session.scalar(
                    select(QQBotAccount).where(
                        QQBotAccount.app_id == delivery.bot_app_id,
                    )
                )
                group_openid = delivery.group_openid
                if bot is None or not bot.is_enabled:
                    cancel_reason = "机器人已删除或停用"
                elif bot.version != delivery.bot_version:
                    cancel_reason = "机器人凭据已变更，请重新提交批量推送"
                else:
                    joined = await session.scalar(
                        select(QQJoinedGroup.id).where(
                            QQJoinedGroup.bot_id == bot.id,
                            QQJoinedGroup.app_id == bot.app_id,
                            QQJoinedGroup.group_openid == group_openid,
                            QQJoinedGroup.is_joined.is_(True),
                        )
                    )
                    if joined is None:
                        cancel_reason = "机器人已退出目标群或未记录入群状态"
                if delivery.kind == "article" and not cancel_reason:
                    prior_status = await session.scalar(
                        select(QQDelivery.status)
                        .where(
                            QQDelivery.article_publish_attempt_id
                            == delivery.article_publish_attempt_id,
                            QQDelivery.group_openid == delivery.group_openid,
                            QQDelivery.sequence < delivery.sequence,
                            QQDelivery.status != "sent",
                        )
                        .order_by(QQDelivery.sequence.desc())
                        .limit(1)
                    )
                    if prior_status in {"failed", "cancelled"}:
                        cancel_reason = "同一文章的前序消息发送失败"
                    elif prior_status is not None:
                        delivery.next_attempt_at = now + timedelta(seconds=5)
                        return None
            else:
                target = (
                    await session.get(QQNotificationTarget, delivery.target_id)
                    if delivery.target_id is not None
                    else None
                )
                bot = await session.get(QQBotAccount, target.bot_id) if target else None
                group_openid = target.group_openid if target else delivery.group_openid
                if target is None or bot is None or not target.is_enabled or not bot.is_enabled:
                    cancel_reason = "机器人或群通知目标已删除或停用"
            if cancel_reason:
                delivery.status = "cancelled"
                delivery.last_error = cancel_reason
                delivery.completed_at = now
                delivery.claim_token = None
                delivery.claimed_by = None
                delivery.lease_expires_at = None
                if delivery.kind == "article" and delivery.article_id:
                    attempt = await session.get(
                        ArticlePublishAttempt, delivery.article_publish_attempt_id
                    )
                    if attempt is not None:
                        attempt.status = "failed"
                        attempt.error = cancel_reason
                        attempt.completed_at = now
                    article = await session.get(AIDraft, delivery.article_id, with_for_update=True)
                    if (
                        article is not None
                        and article.publish_attempt_id == delivery.article_publish_attempt_id
                    ):
                        article.publish_status = "failed"
                        article.publish_error = cancel_reason
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
                if delivery.kind == "article" and delivery.article_id:
                    attempt = await session.get(
                        ArticlePublishAttempt, delivery.article_publish_attempt_id
                    )
                    if attempt is not None:
                        attempt.status = "failed"
                        attempt.error = str(exc)[:2000]
                        attempt.completed_at = now
                    article = await session.get(AIDraft, delivery.article_id, with_for_update=True)
                    if (
                        article is not None
                        and article.publish_attempt_id == delivery.article_publish_attempt_id
                    ):
                        article.publish_status = "failed"
                        article.publish_error = str(exc)[:2000]
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
                group_openid=group_openid,
                message_body=delivery.message_body,
                media_path=getattr(delivery, "media_path", None),
                attempts=delivery.attempts,
                max_attempts=delivery.max_attempts,
                kind=delivery.kind,
                article_id=getattr(delivery, "article_id", None),
                sequence=getattr(delivery, "sequence", None),
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
            if delivery.kind == "article" and delivery.article_id is not None:
                await session.flush()
                remaining = int(
                    await session.scalar(
                        select(func.count(QQDelivery.id)).where(
                            QQDelivery.article_publish_attempt_id
                            == delivery.article_publish_attempt_id,
                            QQDelivery.status != "sent",
                        )
                    )
                    or 0
                )
                if remaining == 0:
                    completed_at = datetime.now(UTC)
                    attempt = await session.get(
                        ArticlePublishAttempt, delivery.article_publish_attempt_id
                    )
                    if attempt is not None:
                        attempt.status = "published"
                        attempt.error = None
                        attempt.completed_at = completed_at
                    article = await session.get(AIDraft, delivery.article_id, with_for_update=True)
                    if (
                        article is not None
                        and article.publish_attempt_id == delivery.article_publish_attempt_id
                    ):
                        article.publish_status = "published"
                        article.published_at = completed_at
                        article.publish_error = None
            return True

    async def _commit_failure(
        self, delivery_id: int, claim_token: str, message: str, *, retryable: bool
    ) -> QQFailureCommitResult:
        async with AsyncSessionFactory() as session, session.begin():
            delivery = await session.get(QQDelivery, delivery_id, with_for_update=True)
            if delivery is None or delivery.claim_token != claim_token:
                return QQFailureCommitResult(outcome="superseded")
            now = datetime.now(UTC)
            if retryable and delivery.attempts < delivery.max_attempts:
                delivery.status = "retry_wait"
                delay = min(900, 10 * (2 ** max(0, delivery.attempts - 1)))
                delivery.next_attempt_at = now + timedelta(seconds=delay)
                outcome = "retry_wait"
            else:
                delivery.status = "failed"
                delivery.completed_at = now
                outcome = "failed"
            delivery.last_error = message[:2000]
            delivery.claim_token = None
            delivery.claimed_by = None
            delivery.lease_expires_at = None
            if outcome == "failed" and delivery.kind == "article" and delivery.article_id:
                attempt = await session.get(
                    ArticlePublishAttempt, delivery.article_publish_attempt_id
                )
                if attempt is not None:
                    attempt.status = "failed"
                    attempt.error = message[:2000]
                    attempt.completed_at = now
                article = await session.get(AIDraft, delivery.article_id, with_for_update=True)
                if (
                    article is not None
                    and article.publish_attempt_id == delivery.article_publish_attempt_id
                ):
                    article.publish_status = "failed"
                    article.publish_error = message[:2000]
            return QQFailureCommitResult(
                outcome=outcome,
                attempt=delivery.attempts,
                max_attempts=delivery.max_attempts,
                retry_delay_seconds=delay if outcome == "retry_wait" else None,
                next_attempt_at=(
                    delivery.next_attempt_at if outcome == "retry_wait" else None
                ),
            )

    def _stage_extra(
        self,
        stage: str,
        started_perf: float,
        log_context: dict[str, Any],
        **fields: Any,
    ) -> dict[str, Any]:
        return {
            **log_context,
            "worker_id": self.worker_id,
            "stage": stage,
            "elapsed_ms": round((time.perf_counter() - started_perf) * 1000),
            **fields,
        }

    def _log_stage(
        self,
        message: str,
        stage: str,
        started_perf: float,
        log_context: dict[str, Any],
        **fields: Any,
    ) -> None:
        logger.info(
            message,
            extra=self._stage_extra(stage, started_perf, log_context, **fields),
        )

    def _log_failure_outcome(
        self,
        failure: QQFailureCommitResult,
        exc: Exception,
        *,
        retryable: bool,
        started_perf: float,
        log_context: dict[str, Any],
    ) -> None:
        if failure.outcome == "retry_wait":
            stage = "delivery_retry_scheduled"
            message = "QQ delivery scheduled for retry"
            log = logger.warning
        elif failure.outcome == "failed":
            stage = "delivery_failed_permanently"
            message = "QQ delivery failed permanently"
            log = logger.error
        else:
            stage = "delivery_failure_superseded"
            message = "QQ delivery failure was superseded"
            log = logger.warning
        status_code = getattr(exc, "status_code", None)
        log(
            message,
            extra=self._stage_extra(
                stage,
                started_perf,
                log_context,
                outcome=failure.outcome,
                attempt=failure.attempt or log_context.get("attempt"),
                max_attempts=failure.max_attempts or log_context.get("max_attempts"),
                error_type=type(exc).__name__,
                error_summary=self._safe_error_summary(exc),
                retryable=retryable,
                qq_status_code=status_code if isinstance(status_code, int) else None,
                retry_delay_seconds=failure.retry_delay_seconds,
                next_attempt_at=(
                    failure.next_attempt_at.isoformat() if failure.next_attempt_at else None
                ),
            ),
        )

    @staticmethod
    def _safe_error_summary(exc: Exception) -> str:
        return str(exc).split(":", 1)[0][:200]

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        if isinstance(exc, (NetworkError, RateLimitException)):
            return True
        if isinstance(exc, UnauthorizedException):
            return False
        return isinstance(exc, ActionFailed) and (exc.status_code == 429 or exc.status_code >= 500)

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
        # FastAPI serves QQ webhooks; httpx handles REST calls and the
        # websockets driver keeps each authorized bot online on the gateway.
        driver="~fastapi+~httpx+~websockets",
        host="0.0.0.0",
        port=settings.qq_worker_port,
        qq_api_base=settings.qq_api_base_url,
        qq_auth_base=settings.qq_auth_url,
        qq_bots=[],
    )
    driver = get_driver()
    driver.register_adapter(QQAdapter)
    register_group_event_handlers()

    sender = NoneBotQQSender(get_adapter(QQAdapter), settings)
    worker = QQDeliveryWorker(settings, sender)
    worker_task: asyncio.Task[None] | None = None
    inbound_sync_task: asyncio.Task[None] | None = None
    bot_status_task: asyncio.Task[None] | None = None
    websocket_started: set[str] = set()

    @driver.on_startup
    async def start_worker() -> None:
        nonlocal bot_status_task, inbound_sync_task, worker_task
        await worker.check_dependencies()
        try:
            await refresh_inbound_bots(get_adapter(QQAdapter), websocket_started)
        except Exception:
            logger.exception("Unable to load QQ bot accounts for inbound events")

        # Keep the adapter's webhook allow-list in sync with the UI-managed
        # accounts so GROUP_ADD_ROBOT callbacks can be dispatched per bot.
        async def sync_inbound_bots() -> None:
            while not worker.stop_event.is_set():
                try:
                    await refresh_inbound_bots(get_adapter(QQAdapter), websocket_started)
                    await publish_bot_status(worker.redis, get_adapter(QQAdapter))
                except Exception:
                    logger.exception("Unable to load QQ bot accounts for inbound events")
                try:
                    await asyncio.wait_for(worker.stop_event.wait(), timeout=15)
                except TimeoutError:
                    pass

        inbound_sync_task = asyncio.create_task(sync_inbound_bots())

        async def publish_status_loop() -> None:
            while not worker.stop_event.is_set():
                try:
                    await publish_bot_status(worker.redis, get_adapter(QQAdapter))
                except Exception:
                    logger.exception("Unable to publish QQ bot status")
                try:
                    await asyncio.wait_for(worker.stop_event.wait(), timeout=5)
                except TimeoutError:
                    pass

        bot_status_task = asyncio.create_task(publish_status_loop())
        worker_task = asyncio.create_task(worker.run(check_dependencies=False))

    @driver.on_shutdown
    async def stop_worker() -> None:
        worker.request_stop()
        if bot_status_task is not None:
            bot_status_task.cancel()
            with suppress(asyncio.CancelledError):
                await bot_status_task
        if inbound_sync_task is not None:
            inbound_sync_task.cancel()
            with suppress(asyncio.CancelledError):
                await inbound_sync_task
        if worker_task is not None:
            with suppress(TimeoutError):
                await asyncio.wait_for(worker_task, timeout=15)

    nonebot.run()


if __name__ == "__main__":
    main()
