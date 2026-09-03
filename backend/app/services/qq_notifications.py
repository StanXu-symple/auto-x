from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.monitored_user import MonitoredUser
from app.models.qq import QQBotAccount, QQDelivery, QQNotificationTarget, QQTargetSubscription
from app.models.tweet import Tweet
from app.services.x_credentials import (
    XCredentialUnavailableError,
    decrypt_token,
    encrypt_token,
    token_fingerprint,
    token_hint,
)

logger = logging.getLogger(__name__)
QQ_DELIVERY_QUEUE = "xsentinel:qq:delivery-queue"
QQ_WORKER_HEARTBEAT = "xsentinel:qq-worker:heartbeat"
QQ_BOT_STATUS = "xsentinel:qq-worker:bot-status"


class QQCredentialUnavailableError(RuntimeError):
    pass


class QQCredentialValidationError(RuntimeError):
    def __init__(self, message: str, *, invalid: bool = False) -> None:
        super().__init__(message)
        self.invalid = invalid


def encrypt_app_secret(secret: str, settings: Settings) -> str:
    return encrypt_token(secret, settings)


def decrypt_app_secret(ciphertext: str, settings: Settings) -> str:
    try:
        return decrypt_token(ciphertext, settings)
    except XCredentialUnavailableError as exc:
        raise QQCredentialUnavailableError(str(exc)) from exc


def secret_hint(secret: str) -> str:
    return token_hint(secret)


def secret_fingerprint(secret: str) -> str:
    return token_fingerprint(secret)


def render_qq_message(template: str, *, tweet: Tweet, user: MonitoredUser) -> str:
    values = {
        "author": user.display_name or user.username,
        "username": user.username,
        "text": tweet.text.strip(),
        "url": f"https://x.com/{user.username}/status/{tweet.tweet_id}",
        "posted_at": tweet.posted_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC"),
    }
    message = template.format_map(values).strip()
    return message if len(message) <= 3800 else message[:3797].rstrip() + "..."


async def validate_qq_credentials(
    app_id: str,
    app_secret: str,
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    try:
        async with httpx.AsyncClient(
            timeout=settings.qq_request_timeout_seconds,
            follow_redirects=False,
            transport=transport,
        ) as client:
            response = await client.post(
                settings.qq_auth_url,
                json={"appId": app_id, "clientSecret": app_secret},
            )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise QQCredentialValidationError(f"无法连接 QQ 开放平台：{exc}") from None
    if response.status_code in {400, 401, 403}:
        raise QQCredentialValidationError("AppID 或 AppSecret 无效", invalid=True)
    if response.is_error:
        raise QQCredentialValidationError(f"QQ 开放平台返回 HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        raise QQCredentialValidationError("QQ 开放平台没有返回有效 JSON") from None
    if (
        not isinstance(payload, dict)
        or not payload.get("access_token")
        or not payload.get("expires_in")
    ):
        message = payload.get("message") if isinstance(payload, dict) else None
        raise QQCredentialValidationError(
            str(message or "QQ 开放平台未返回完整访问令牌"), invalid=True
        )


async def enqueue_qq_delivery_ids(redis: Redis, delivery_ids: list[int]) -> None:
    if not delivery_ids:
        return
    try:
        await redis.rpush(QQ_DELIVERY_QUEUE, *(str(item) for item in delivery_ids))
    except Exception:
        logger.warning(
            "Unable to notify QQ worker; database outbox scan will recover",
            exc_info=True,
        )


async def create_tweet_deliveries(
    session: AsyncSession,
    tweet_x_ids: list[str],
    *,
    max_attempts: int,
) -> list[int]:
    if not tweet_x_ids:
        return []
    tweets = list(
        await session.scalars(
            select(Tweet).where(Tweet.tweet_id.in_(list(dict.fromkeys(tweet_x_ids))))
        )
    )
    if not tweets:
        return []
    users = {
        user.id: user
        for user in await session.scalars(
            select(MonitoredUser).where(
                MonitoredUser.id.in_({tweet.monitored_user_id for tweet in tweets})
            )
        )
    }
    target_rows = (
        await session.execute(
            select(QQNotificationTarget, QQBotAccount)
            .join(QQBotAccount, QQBotAccount.id == QQNotificationTarget.bot_id)
            .where(
                QQNotificationTarget.is_enabled.is_(True),
                QQBotAccount.is_enabled.is_(True),
            )
        )
    ).all()
    if not target_rows:
        return []
    target_ids = [target.id for target, _ in target_rows]
    bindings = set(
        (
            await session.execute(
                select(
                    QQTargetSubscription.target_id, QQTargetSubscription.monitored_user_id
                ).where(QQTargetSubscription.target_id.in_(target_ids))
            )
        ).tuples()
    )
    keys = [f"tweet:{tweet.id}:target:{target.id}" for tweet in tweets for target, _ in target_rows]
    existing = set(
        await session.scalars(
            select(QQDelivery.idempotency_key).where(QQDelivery.idempotency_key.in_(keys))
        )
    )
    now = datetime.now(UTC)
    rows: list[QQDelivery] = []
    for tweet in tweets:
        user = users.get(tweet.monitored_user_id)
        if user is None:
            continue
        for target, bot in target_rows:
            if not target.all_monitored_users and (target.id, user.id) not in bindings:
                continue
            key = f"tweet:{tweet.id}:target:{target.id}"
            if key in existing:
                continue
            rows.append(
                QQDelivery(
                    target_id=target.id,
                    source_tweet_id=tweet.id,
                    kind="tweet",
                    idempotency_key=key,
                    bot_name=bot.name,
                    bot_app_id=bot.app_id,
                    bot_version=bot.version,
                    target_name=target.name,
                    group_openid=target.group_openid,
                    message_body=render_qq_message(target.message_template, tweet=tweet, user=user),
                    status="queued",
                    attempts=0,
                    max_attempts=max_attempts,
                    next_attempt_at=now,
                )
            )
    session.add_all(rows)
    await session.flush()
    return [row.id for row in rows]


async def create_test_delivery(
    session: AsyncSession,
    target: QQNotificationTarget,
    bot: QQBotAccount,
    *,
    max_attempts: int,
) -> QQDelivery:
    now = datetime.now(UTC)
    row = QQDelivery(
        target_id=target.id,
        source_tweet_id=None,
        kind="test",
        idempotency_key=f"test:{uuid.uuid4()}",
        bot_name=bot.name,
        bot_app_id=bot.app_id,
        bot_version=bot.version,
        target_name=target.name,
        group_openid=target.group_openid,
        message_body="【X Sentinel】QQ 通知通道测试成功。",
        status="queued",
        attempts=0,
        max_attempts=max_attempts,
        next_attempt_at=now,
    )
    session.add(row)
    await session.flush()
    return row


def delivery_message_id(result: Any) -> str | None:
    for field in ("id", "message_id"):
        value = getattr(result, field, None)
        if value:
            return str(value)
    return None
