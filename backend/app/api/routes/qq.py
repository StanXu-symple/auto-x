from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query, status
from sqlalchemy import and_, delete, func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentAdmin, DbSession, RedisClient
from app.api.errors import APIError
from app.core.config import get_settings
from app.models.monitored_user import MonitoredUser
from app.models.qq import (
    QQBotAccount,
    QQDelivery,
    QQJoinedGroup,
    QQNotificationTarget,
    QQTargetSubscription,
    QQScheduledTask, QQScheduledTaskBot, QQScheduledTaskGroup,
)
from app.models.tweet import Tweet
from app.schemas.common import MessageResponse, Page
from app.schemas.qq import (
    DEFAULT_QQ_MESSAGE_TEMPLATE,
    QQBatchPushAccepted,
    QQBatchPushCreate,
    QQBotCreate,
    QQBotOut,
    QQBotTestResult,
    QQBotUpdate,
    QQDeliveryAccepted,
    QQDeliveryOut,
    QQJoinedGroupOut,
    QQOverview,
    QQTargetCreate,
    QQTargetOut,
    QQTargetUpdate,
    QQScheduledTaskCreate, QQScheduledTaskOut,
)
from app.services.qq_notifications import (
    QQ_BOT_STATUS,
    QQ_WORKER_HEARTBEAT,
    QQCredentialValidationError,
    chunk_qq_messages,
    create_test_delivery,
    decrypt_app_secret,
    encrypt_app_secret,
    enqueue_qq_delivery_ids,
    render_qq_message,
    secret_fingerprint,
    secret_hint,
    validate_qq_credentials,
)

router = APIRouter(prefix="/qq", tags=["QQ Notifications"])


def _bot_out(row: QQBotAccount, target_count: int = 0) -> QQBotOut:
    return QQBotOut(
        id=row.id,
        name=row.name,
        app_id=row.app_id,
        secret_hint=row.secret_hint,
        is_enabled=row.is_enabled,
        verification_status=row.verification_status,
        last_verified_at=row.last_verified_at,
        last_error=row.last_error,
        version=row.version,
        target_count=target_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _target_out(db: DbSession, row: QQNotificationTarget) -> QQTargetOut:
    bot = await db.get(QQBotAccount, row.bot_id)
    user_ids = list(
        await db.scalars(
            select(QQTargetSubscription.monitored_user_id)
            .where(QQTargetSubscription.target_id == row.id)
            .order_by(QQTargetSubscription.monitored_user_id)
        )
    )
    return QQTargetOut(
        id=row.id,
        bot_id=row.bot_id,
        bot_name=bot.name if bot else "已删除机器人",
        name=row.name,
        group_openid=row.group_openid,
        is_enabled=row.is_enabled,
        all_monitored_users=row.all_monitored_users,
        monitored_user_ids=user_ids,
        message_template=row.message_template,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _get_bot(db: DbSession, bot_id: int) -> QQBotAccount:
    row = await db.get(QQBotAccount, bot_id)
    if row is None:
        raise APIError(404, "qq_bot_not_found", "QQ 机器人不存在")
    return row


async def _get_target(db: DbSession, target_id: int) -> QQNotificationTarget:
    row = await db.get(QQNotificationTarget, target_id)
    if row is None:
        raise APIError(404, "qq_target_not_found", "QQ群通知目标不存在")
    return row


async def _replace_subscriptions(
    db: DbSession,
    target: QQNotificationTarget,
    *,
    all_monitored_users: bool,
    monitored_user_ids: list[int],
) -> None:
    if not all_monitored_users and not monitored_user_ids:
        raise APIError(422, "qq_target_scope_required", "请选择至少一个监听账号")
    existing_ids = set(
        await db.scalars(select(MonitoredUser.id).where(MonitoredUser.id.in_(monitored_user_ids)))
    )
    missing = sorted(set(monitored_user_ids) - existing_ids)
    if missing:
        raise APIError(422, "monitored_users_not_found", "部分监听账号不存在", missing)
    await db.execute(
        delete(QQTargetSubscription).where(QQTargetSubscription.target_id == target.id)
    )
    if not all_monitored_users:
        db.add_all(
            [
                QQTargetSubscription(target_id=target.id, monitored_user_id=user_id)
                for user_id in monitored_user_ids
            ]
        )


@router.get("/overview", response_model=QQOverview)
async def overview(db: DbSession, redis: RedisClient, _: CurrentAdmin) -> QQOverview:
    total_bots = int(await db.scalar(select(func.count(QQBotAccount.id))) or 0)
    enabled_bots = int(
        await db.scalar(
            select(func.count(QQBotAccount.id)).where(QQBotAccount.is_enabled.is_(True))
        )
        or 0
    )
    enabled_targets = int(
        await db.scalar(
            select(func.count(QQNotificationTarget.id)).where(
                QQNotificationTarget.is_enabled.is_(True)
            )
        )
        or 0
    )
    queued_deliveries = int(
        await db.scalar(
            select(func.count(QQDelivery.id)).where(
                QQDelivery.status.in_(["queued", "retry_wait", "sending"])
            )
        )
        or 0
    )
    failed_deliveries = int(
        await db.scalar(select(func.count(QQDelivery.id)).where(QQDelivery.status == "failed")) or 0
    )
    worker_status = "offline"
    worker_last_heartbeat = None
    try:
        raw = await redis.get(QQ_WORKER_HEARTBEAT)
        if raw:
            heartbeat = json.loads(raw)
            worker_status = "online"
            timestamp = heartbeat.get("last_heartbeat") or heartbeat.get("timestamp")
            if timestamp:
                worker_last_heartbeat = datetime.fromisoformat(
                    str(timestamp).replace("Z", "+00:00")
                )
    except Exception:
        worker_status = "unknown"
    return QQOverview(
        total_bots=total_bots,
        enabled_bots=enabled_bots,
        enabled_targets=enabled_targets,
        queued_deliveries=queued_deliveries,
        failed_deliveries=failed_deliveries,
        worker_status=worker_status,
        worker_last_heartbeat=worker_last_heartbeat,
    )


@router.get("/bots", response_model=list[QQBotOut])
async def list_bots(db: DbSession, redis: RedisClient, _: CurrentAdmin) -> list[QQBotOut]:
    bot_status: dict[str, str] = {}
    try:
        raw_status = await redis.get(QQ_BOT_STATUS)
        if raw_status:
            payload = json.loads(raw_status)
            bot_status = payload if isinstance(payload, dict) else {}
    except Exception:
        bot_status = {}
    count = (
        select(func.count(QQNotificationTarget.id))
        .where(QQNotificationTarget.bot_id == QQBotAccount.id)
        .correlate(QQBotAccount)
        .scalar_subquery()
    )
    rows = (
        await db.execute(select(QQBotAccount, count).order_by(QQBotAccount.created_at.desc()))
    ).all()
    result = []
    for bot, target_count in rows:
        output = _bot_out(bot, int(target_count or 0))
        output.online_status = (
            "disabled" if not bot.is_enabled else bot_status.get(bot.app_id, "offline")
        )
        result.append(output)
    return result


@router.get("/bots/{bot_id}/groups", response_model=list[QQJoinedGroupOut])
async def list_joined_groups(
    bot_id: int, db: DbSession, _: CurrentAdmin,
) -> list[QQJoinedGroupOut]:
    bot = await _get_bot(db, bot_id)
    rows = (await db.execute(
        select(QQJoinedGroup, QQNotificationTarget.name, QQNotificationTarget.id)
        .outerjoin(QQNotificationTarget, and_(
            QQNotificationTarget.bot_id == QQJoinedGroup.bot_id,
            QQNotificationTarget.group_openid == QQJoinedGroup.group_openid,
        ))
        .where(
            QQJoinedGroup.bot_id == bot.id,
            QQJoinedGroup.app_id == bot.app_id,
            QQJoinedGroup.is_joined.is_(True),
        )
        .order_by(QQJoinedGroup.last_event_at.desc(), QQJoinedGroup.id.desc())
    )).all()
    return [
        QQJoinedGroupOut(
            group_openid=group.group_openid, name=name,
            target_id=target_id, last_event_at=group.last_event_at,
        )
        for group, name, target_id in rows
    ]


@router.post("/bots", response_model=QQBotOut, status_code=status.HTTP_201_CREATED)
async def create_bot(payload: QQBotCreate, db: DbSession, _: CurrentAdmin) -> QQBotOut:
    settings = get_settings()
    now = datetime.now(UTC)
    row = QQBotAccount(
        name=payload.name.strip(),
        app_id=payload.app_id.strip(),
        encrypted_app_secret=encrypt_app_secret(payload.app_secret, settings),
        secret_hint=secret_hint(payload.app_secret),
        secret_fingerprint=secret_fingerprint(payload.app_secret),
        is_enabled=payload.is_enabled,
        verification_status="unverified",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise APIError(409, "qq_app_id_exists", "该 AppID 已经配置") from None
    await db.refresh(row)
    return _bot_out(row)


@router.patch("/bots/{bot_id}", response_model=QQBotOut)
async def update_bot(bot_id: int, payload: QQBotUpdate, db: DbSession, _: CurrentAdmin) -> QQBotOut:
    row = await _get_bot(db, bot_id)
    updates = payload.model_dump(exclude_unset=True)
    credentials_changed = False
    if "name" in updates:
        row.name = updates["name"].strip()
    if "app_id" in updates:
        row.app_id = updates["app_id"].strip()
        credentials_changed = True
    if secret := updates.get("app_secret"):
        row.encrypted_app_secret = encrypt_app_secret(secret, get_settings())
        row.secret_hint = secret_hint(secret)
        row.secret_fingerprint = secret_fingerprint(secret)
        credentials_changed = True
    if "is_enabled" in updates:
        row.is_enabled = updates["is_enabled"]
    if credentials_changed:
        row.verification_status = "unverified"
        row.last_verified_at = None
        row.last_error = None
        row.version += 1
    row.updated_at = datetime.now(UTC)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise APIError(409, "qq_app_id_exists", "该 AppID 已经配置") from None
    await db.refresh(row)
    count = int(
        await db.scalar(
            select(func.count(QQNotificationTarget.id)).where(QQNotificationTarget.bot_id == row.id)
        )
        or 0
    )
    return _bot_out(row, count)


@router.post("/bots/{bot_id}/test", response_model=QQBotTestResult)
async def test_bot(bot_id: int, db: DbSession, _: CurrentAdmin) -> QQBotTestResult:
    row = await _get_bot(db, bot_id)
    checked_at = datetime.now(UTC)
    try:
        secret = decrypt_app_secret(row.encrypted_app_secret, get_settings())
        await validate_qq_credentials(row.app_id, secret, get_settings())
        valid = True
        verification_status = "valid"
        message = "AppID 与 AppSecret 验证成功"
        row.last_error = None
    except QQCredentialValidationError as exc:
        valid = False
        verification_status = "invalid" if exc.invalid else "error"
        message = str(exc)
        row.last_error = message[:500]
    except Exception as exc:
        valid = False
        verification_status = "error"
        message = str(exc)
        row.last_error = message[:500]
    row.verification_status = verification_status
    row.last_verified_at = checked_at
    await db.commit()
    return QQBotTestResult(
        valid=valid,
        verification_status=verification_status,
        message=message,
        checked_at=checked_at,
    )


@router.delete("/bots/{bot_id}", response_model=MessageResponse)
async def delete_bot(bot_id: int, db: DbSession, _: CurrentAdmin) -> MessageResponse:
    row = await _get_bot(db, bot_id)
    await db.delete(row)
    await db.commit()
    return MessageResponse(message="QQ 机器人及其群目标已删除")


@router.get("/targets", response_model=list[QQTargetOut])
async def list_targets(db: DbSession, _: CurrentAdmin) -> list[QQTargetOut]:
    rows = list(
        await db.scalars(
            select(QQNotificationTarget).order_by(QQNotificationTarget.created_at.desc())
        )
    )
    return [await _target_out(db, row) for row in rows]


@router.post("/targets", response_model=QQTargetOut, status_code=status.HTTP_201_CREATED)
async def create_target(payload: QQTargetCreate, db: DbSession, _: CurrentAdmin) -> QQTargetOut:
    await _get_bot(db, payload.bot_id)
    now = datetime.now(UTC)
    row = QQNotificationTarget(
        bot_id=payload.bot_id,
        name=payload.name.strip(),
        group_openid=payload.group_openid.strip(),
        is_enabled=payload.is_enabled,
        all_monitored_users=payload.all_monitored_users,
        message_template=payload.message_template,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        await db.flush()
        await _replace_subscriptions(
            db,
            row,
            all_monitored_users=payload.all_monitored_users,
            monitored_user_ids=payload.monitored_user_ids,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise APIError(409, "qq_target_exists", "该机器人已经配置了相同群 OpenID") from None
    await db.refresh(row)
    return await _target_out(db, row)


@router.patch("/targets/{target_id}", response_model=QQTargetOut)
async def update_target(
    target_id: int, payload: QQTargetUpdate, db: DbSession, _: CurrentAdmin
) -> QQTargetOut:
    row = await _get_target(db, target_id)
    updates = payload.model_dump(exclude_unset=True)
    if "bot_id" in updates:
        await _get_bot(db, updates["bot_id"])
        row.bot_id = updates["bot_id"]
    for field in ("name", "group_openid"):
        if field in updates:
            setattr(row, field, updates[field].strip())
    for field in ("is_enabled", "all_monitored_users", "message_template"):
        if field in updates:
            setattr(row, field, updates[field])
    final_all = row.all_monitored_users
    if "monitored_user_ids" in updates or "all_monitored_users" in updates:
        if "monitored_user_ids" in updates:
            final_ids = updates["monitored_user_ids"] or []
        else:
            final_ids = list(
                await db.scalars(
                    select(QQTargetSubscription.monitored_user_id).where(
                        QQTargetSubscription.target_id == row.id
                    )
                )
            )
        await _replace_subscriptions(
            db, row, all_monitored_users=final_all, monitored_user_ids=final_ids
        )
    row.updated_at = datetime.now(UTC)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise APIError(409, "qq_target_exists", "该机器人已经配置了相同群 OpenID") from None
    await db.refresh(row)
    return await _target_out(db, row)


@router.delete("/targets/{target_id}", response_model=MessageResponse)
async def delete_target(target_id: int, db: DbSession, _: CurrentAdmin) -> MessageResponse:
    row = await _get_target(db, target_id)
    await db.delete(row)
    await db.commit()
    return MessageResponse(message="QQ群通知目标已删除")


@router.post("/targets/{target_id}/test", response_model=QQDeliveryAccepted)
async def test_target(
    target_id: int, db: DbSession, redis: RedisClient, _: CurrentAdmin
) -> QQDeliveryAccepted:
    target = await _get_target(db, target_id)
    bot = await _get_bot(db, target.bot_id)
    if not target.is_enabled or not bot.is_enabled:
        raise APIError(409, "qq_target_disabled", "请先启用机器人和群通知目标")
    row = await create_test_delivery(
        db, target, bot, max_attempts=get_settings().qq_worker_max_attempts
    )
    await db.commit()
    await enqueue_qq_delivery_ids(redis, [row.id])
    return QQDeliveryAccepted(message="测试消息已进入 QQ 投递队列", delivery_id=row.id)


@router.post("/batch-push", response_model=QQBatchPushAccepted)
async def batch_push(
    payload: QQBatchPushCreate, db: DbSession, redis: RedisClient, _: CurrentAdmin
) -> QQBatchPushAccepted:
    bot = await _get_bot(db, payload.bot_id)
    if not bot.is_enabled:
        raise APIError(409, "qq_bot_disabled", "请先启用 QQ 机器人")
    groups = set(payload.group_openids)
    joined = set(await db.scalars(select(QQJoinedGroup.group_openid).where(
        QQJoinedGroup.bot_id == bot.id, QQJoinedGroup.app_id == bot.app_id,
        QQJoinedGroup.is_joined.is_(True), QQJoinedGroup.group_openid.in_(groups),
    )))
    missing = groups - joined
    if missing:
        raise APIError(422, "qq_group_not_joined", "机器人尚未加入所选群", sorted(missing))
    rows = list(
        await db.scalars(
            select(Tweet).where(Tweet.id.in_(payload.tweet_ids)).order_by(Tweet.posted_at.asc())
        )
    )
    if not rows:
        raise APIError(404, "tweets_not_found", "未找到可推送的内容")
    users = {
        u.id: u
        for u in await db.scalars(
            select(MonitoredUser).where(
                MonitoredUser.id.in_({t.monitored_user_id for t in rows})
            )
        )
    }
    targets = {
        target.group_openid: target
        for target in await db.scalars(
            select(QQNotificationTarget).where(
                QQNotificationTarget.bot_id == bot.id,
                QQNotificationTarget.group_openid.in_(groups),
            )
        )
    }
    messages_by_group = {}
    for group in sorted(joined):
        target = targets.get(group)
        template = target.message_template if target else DEFAULT_QQ_MESSAGE_TEMPLATE
        # {title} is a batch header and must only be emitted once per QQ message.
        body_template = template.replace("{title}", "").strip()
        entries = [
            render_qq_message(
                body_template, tweet=tweet, user=users[tweet.monitored_user_id], title=""
            )
            for tweet in rows
            if tweet.monitored_user_id in users
        ]
        header = "【X Sentinel】内容推送" if "{title}" in template else ""
        messages_by_group[group] = ([header] if header else []) + entries
    if not any(messages_by_group.values()):
        raise APIError(404, "tweets_not_found", "所选内容缺少来源账号，无法推送")
    chunks_by_group = {
        group: chunk_qq_messages(items) for group, items in messages_by_group.items()
    }
    now = datetime.now(UTC)
    deliveries = []
    for group in sorted(joined):
        for body in chunks_by_group[group]:
            deliveries.append(
                QQDelivery(
                    target_id=None,
                    source_tweet_id=None,
                    kind="batch",
                    idempotency_key=f"batch:{uuid.uuid4()}",
                    bot_name=bot.name,
                    bot_app_id=bot.app_id,
                    bot_version=bot.version,
                    target_name=group,
                    group_openid=group,
                    message_body=body,
                    status="queued",
                    attempts=0,
                    max_attempts=get_settings().qq_worker_max_attempts,
                    next_attempt_at=now,
                )
            )
    db.add_all(deliveries)
    await db.commit()
    ids = [row.id for row in deliveries]
    await enqueue_qq_delivery_ids(redis, ids)
    batch_count = sum(len(items) for items in chunks_by_group.values())
    return QQBatchPushAccepted(
        message=f"已按单条模板合并为 {batch_count} 条消息并进入投递队列",
        delivery_ids=ids,
        batch_count=batch_count,
    )

def _task_out(task: QQScheduledTask, bot_ids: list[int], groups: list[dict]) -> QQScheduledTaskOut:
    return QQScheduledTaskOut.model_validate(
        {
            **task.__dict__,
            "weekdays": [int(item) for item in task.weekdays.split(",") if item],
            "bot_ids": bot_ids,
            "groups": groups,
        }
    )

@router.get("/tasks", response_model=list[QQScheduledTaskOut])
async def list_tasks(db: DbSession, _: CurrentAdmin):
    tasks = list(await db.scalars(select(QQScheduledTask).order_by(QQScheduledTask.created_at.desc())))
    result = []
    for task in tasks:
        bot_ids = list(await db.scalars(select(QQScheduledTaskBot.bot_id).where(QQScheduledTaskBot.task_id == task.id)))
        groups = [dict(bot_id=bot_id, group_openid=group) for bot_id, group in (await db.execute(select(QQScheduledTaskGroup.bot_id, QQScheduledTaskGroup.group_openid).where(QQScheduledTaskGroup.task_id == task.id))).tuples()]
        result.append(_task_out(task, bot_ids, groups))
    return result

@router.post("/tasks", response_model=QQScheduledTaskOut, status_code=201)
async def create_task(payload: QQScheduledTaskCreate, db: DbSession, _: CurrentAdmin):
    now = datetime.now(UTC)
    task = QQScheduledTask(name=payload.name.strip(), message=payload.message.strip(), frequency=payload.frequency, run_time=payload.run_time, weekdays=','.join(map(str, payload.weekdays)), month_day=payload.month_day, is_enabled=payload.is_enabled, next_run_at=now, created_at=now, updated_at=now)
    db.add(task); await db.flush()
    db.add_all([QQScheduledTaskBot(task_id=task.id, bot_id=i) for i in set(payload.bot_ids)])
    db.add_all([QQScheduledTaskGroup(task_id=task.id, bot_id=int(g["bot_id"]), group_openid=str(g["group_openid"])) for g in payload.groups])
    await db.commit(); await db.refresh(task)
    return _task_out(task, payload.bot_ids, payload.groups)

@router.patch("/tasks/{task_id}", response_model=QQScheduledTaskOut)
async def update_task(task_id: int, payload: QQScheduledTaskCreate, db: DbSession, _: CurrentAdmin):
    task = await db.get(QQScheduledTask, task_id)
    if not task: raise APIError(404, "qq_task_not_found", "QQ 定时任务不存在")
    for key in ("name", "message", "frequency", "run_time", "month_day", "is_enabled"): setattr(task, key, getattr(payload, key))
    task.weekdays = ','.join(map(str, payload.weekdays)); task.updated_at = datetime.now(UTC)
    await db.execute(delete(QQScheduledTaskBot).where(QQScheduledTaskBot.task_id == task.id)); await db.execute(delete(QQScheduledTaskGroup).where(QQScheduledTaskGroup.task_id == task.id))
    db.add_all([QQScheduledTaskBot(task_id=task.id, bot_id=i) for i in set(payload.bot_ids)]); db.add_all([QQScheduledTaskGroup(task_id=task.id, bot_id=int(g["bot_id"]), group_openid=str(g["group_openid"])) for g in payload.groups]); await db.commit(); await db.refresh(task)
    return _task_out(task, payload.bot_ids, payload.groups)

@router.delete("/tasks/{task_id}", response_model=MessageResponse)
async def delete_task(task_id: int, db: DbSession, _: CurrentAdmin):
    task = await db.get(QQScheduledTask, task_id)
    if not task: raise APIError(404, "qq_task_not_found", "QQ 定时任务不存在")
    await db.delete(task); await db.commit(); return MessageResponse(message="QQ 定时任务已删除")


@router.get("/deliveries", response_model=Page[QQDeliveryOut])
async def list_deliveries(
    db: DbSession,
    _: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    delivery_status: str | None = Query(default=None, alias="status", max_length=24),
) -> Page[QQDeliveryOut]:
    conditions = [QQDelivery.status == delivery_status] if delivery_status else []
    total = int(await db.scalar(select(func.count(QQDelivery.id)).where(*conditions)) or 0)
    rows = list(
        await db.scalars(
            select(QQDelivery)
            .where(*conditions)
            .order_by(QQDelivery.created_at.desc(), QQDelivery.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page(
        items=[QQDeliveryOut.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/deliveries/{delivery_id}/retry", response_model=QQDeliveryAccepted)
async def retry_delivery(
    delivery_id: int, db: DbSession, redis: RedisClient, _: CurrentAdmin
) -> QQDeliveryAccepted:
    row = await db.get(QQDelivery, delivery_id)
    if row is None:
        raise APIError(404, "qq_delivery_not_found", "QQ 投递记录不存在")
    if row.status not in {"failed", "cancelled"}:
        raise APIError(409, "qq_delivery_not_retryable", "当前投递状态不能重试")
    row.status = "queued"
    row.attempts = 0
    row.next_attempt_at = datetime.now(UTC)
    row.completed_at = None
    row.last_error = None
    row.claim_token = None
    row.claimed_by = None
    row.lease_expires_at = None
    await db.commit()
    await enqueue_qq_delivery_ids(redis, [row.id])
    return QQDeliveryAccepted(message="QQ 投递任务已重新排队", delivery_id=row.id)
