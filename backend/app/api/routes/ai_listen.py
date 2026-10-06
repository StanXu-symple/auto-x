"""Configuration and audit API for durable AI listening tasks."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Query, status
from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentAdmin, DbSession, RedisClient
from app.api.errors import APIError
from app.models.ai import (
    AIGenerationJob,
    AIListenTask,
    AIListenTaskBackfill,
    AIListenTaskEvent,
    AIListenTaskSkill,
    AIListenTaskSubscription,
)
from app.models.ai_data_source import AIDataSource
from app.models.monitored_user import MonitoredUser
from app.schemas.ai import (
    AIListenTaskBackfillCreate,
    AIListenTaskBackfillOut,
    AIListenTaskCreate,
    AIListenTaskEventOut,
    AIListenTaskOut,
    AIListenTaskPage,
    AIListenTaskPatch,
    AIListenTaskPreview,
    AIListenTaskPreviewOut,
    AIListenTaskQueueDecision,
)
from app.schemas.common import Page
from app.services.ai_jobs import get_ai_setting
from app.services.ai_listen_backfill import enqueue_backfill_request
from app.services.ai_listen_tasks import (
    TASK_LOAD,
    active_all_user_ids,
    get_task,
    initial_window,
    preview_task,
    summarize_stats,
    task_out,
    task_stats,
    validate_qq_publish_target,
    validate_selection,
)

router = APIRouter(prefix="/listen-tasks", tags=["AI Listening"])
AI_HEARTBEAT_KEY = "xsentinel:ai-worker:heartbeat"


def _require_task_owner(task: AIListenTask, admin: CurrentAdmin) -> None:
    if task.owner_admin_id is not None and task.owner_admin_id != admin.id:
        raise APIError(403, "ai_listen_task_owner_required", "只有任务归属管理员可以操作该任务")


async def _worker_online(redis: RedisClient) -> bool:
    try:
        raw = await redis.get(AI_HEARTBEAT_KEY)
        if not raw:
            return False
        data = json.loads(raw)
        timestamp = datetime.fromisoformat(
            str(data.get("last_heartbeat") or data.get("timestamp")).replace("Z", "+00:00")
        )
        return timestamp.tzinfo is not None and (datetime.now(UTC) - timestamp).total_seconds() < 90
    except Exception:
        # Task configuration and audit remain readable during a Redis outage.
        return False


async def _out(db: AsyncSession, redis: RedisClient, task: AIListenTask) -> AIListenTaskOut:
    setting = await get_ai_setting(db)
    source = await db.get(AIDataSource, 1)
    stats = (await task_stats(db, [task.id])).get(task.id)
    return await task_out(
        db,
        task,
        setting=setting,
        source=source,
        worker_online=await _worker_online(redis),
        stats=stats,
    )


def _event(
    task: AIListenTask, event_type: str, summary: str, **details: object
) -> AIListenTaskEvent:
    return AIListenTaskEvent(
        task_id=task.id,
        event_type=event_type,
        summary=summary,
        details=details or None,
        created_at=datetime.now(UTC),
    )


async def _activate(db: AsyncSession, task: AIListenTask, *, switch_from_legacy: bool) -> None:
    # The worker uses the same lock order: setting, task, then jobs.
    setting = await get_ai_setting(db, for_update=True)
    if setting.auto_trigger_mode == "legacy_all":
        if not switch_from_legacy:
            raise APIError(
                409,
                "legacy_mode_switch_required",
                "启用监听任务将停止旧模式对全部新推文自动生成，请确认切换",
                {"legacy_enabled": setting.enabled and setting.auto_generate},
            )
        setting.auto_trigger_mode = "listening_tasks"
    now = datetime.now(UTC)
    first_activation = task.activated_at is None
    if first_activation:
        task.activated_at = now
        window = initial_window(task, now)
        if window:
            task.initial_backfill_from, task.initial_backfill_to = window
    task.desired_state = "enabled"
    task.effective_from = now
    for subscription in task.subscriptions:
        if subscription.effective_from is None:
            subscription.effective_from = now
    if first_activation and task.initial_backfill_from is not None:
        await enqueue_backfill_request(
            db,
            task,
            from_at=task.initial_backfill_from,
            to_at=task.initial_backfill_to,
            request_id="initial",
            all_user_ids=await active_all_user_ids(db) if task.all_monitored_users else None,
        )
    db.add(_event(task, "resumed" if not first_activation else "activated", "监听任务已启用"))


async def _create_task(
    db: AsyncSession,
    payload: AIListenTaskCreate,
    *,
    owner_admin_id: int | None = None,
    copied_from: int | None = None,
) -> AIListenTask:
    # Lock before selection validation and FK writes; Skill mutations take this
    # singleton lock first as well.
    setting = await get_ai_setting(db, for_update=True)
    users, skills = await validate_selection(
        db,
        all_monitored_users=payload.all_monitored_users,
        user_ids=payload.monitored_user_ids,
        skill_ids=payload.skill_ids,
        lock_rows=True,
    )
    await validate_qq_publish_target(
        db,
        channels=payload.auto_publish_channels,
        bot_id=payload.qq_bot_id,
        group_openids=payload.qq_group_openids,
    )
    if payload.desired_state == "enabled" and setting.auto_trigger_mode == "legacy_all":
        if not payload.switch_from_legacy:
            raise APIError(
                409,
                "legacy_mode_switch_required",
                "启用监听任务将停止旧模式对全部新推文自动生成，请确认切换",
                {"legacy_enabled": setting.enabled and setting.auto_generate},
            )
        setting.auto_trigger_mode = "listening_tasks"
    now = datetime.now(UTC)
    task = AIListenTask(
        name=payload.name,
        owner_admin_id=owner_admin_id,
        desired_state="paused",
        all_monitored_users=payload.all_monitored_users,
        listen_mode=payload.listen_mode,
        auto_publish_channels=payload.auto_publish_channels,
        qq_bot_id=payload.qq_bot_id,
        qq_group_openids=payload.qq_group_openids,
        config_version=1,
        initial_sync_days=payload.initial_sync_days,
        max_attempts_override=payload.max_attempts_override,
        language_override=payload.language_override,
        tone_override=payload.tone_override,
        max_output_tokens_override=payload.max_output_tokens_override,
        created_at=now,
        updated_at=now,
    )
    task.subscriptions = [
        AIListenTaskSubscription(monitored_user_id=user.id, effective_from=None, created_at=now)
        for user in users
    ]
    task.skills = [
        AIListenTaskSkill(skill=skill, priority=priority)
        for priority, skill in enumerate(skills, 1)
    ]
    db.add(task)
    await db.flush()
    db.add(_event(task, "created", "监听任务已创建", copied_from=copied_from))
    if payload.desired_state == "enabled":
        await _activate(db, task, switch_from_legacy=True)
    return task


@router.get("", response_model=AIListenTaskPage)
async def list_listen_tasks(
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    state: str | None = Query(default=None, pattern="^(enabled|paused|archived)$"),
    monitored_user_id: int | None = Query(default=None, ge=1),
    q: str | None = Query(default=None, max_length=100),
    include_archived: bool = False,
) -> AIListenTaskPage:
    conditions = []
    if state:
        conditions.append(AIListenTask.desired_state == state)
    elif not include_archived:
        conditions.append(AIListenTask.desired_state != "archived")
    if q:
        conditions.append(AIListenTask.name.ilike(f"%{q.strip()}%"))
    if monitored_user_id:
        conditions.append(
            AIListenTask.all_monitored_users.is_(True)
            | exists(
                select(AIListenTaskSubscription.id).where(
                    AIListenTaskSubscription.task_id == AIListenTask.id,
                    AIListenTaskSubscription.monitored_user_id == monitored_user_id,
                )
            )
        )
    total = int(await db.scalar(select(func.count(AIListenTask.id)).where(*conditions)) or 0)
    all_ids = list(await db.scalars(select(AIListenTask.id).where(*conditions)))
    task_rows = list(
        await db.scalars(
            select(AIListenTask)
            .where(*conditions)
            .options(*TASK_LOAD)
            .order_by(AIListenTask.updated_at.desc(), AIListenTask.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    all_stats = await task_stats(db, all_ids)
    setting = await get_ai_setting(db)
    source = await db.get(AIDataSource, 1)
    worker_online = await _worker_online(redis)
    users = list(await db.scalars(select(MonitoredUser).order_by(MonitoredUser.id)))
    return AIListenTaskPage(
        items=[
            await task_out(
                db,
                task,
                setting=setting,
                source=source,
                worker_online=worker_online,
                stats=all_stats.get(task.id),
                all_users=users,
            )
            for task in task_rows
        ],
        total=total,
        page=page,
        page_size=page_size,
        summary=summarize_stats(all_stats),
    )


@router.post("/preview", response_model=AIListenTaskPreviewOut)
async def preview_listen_task(
    payload: AIListenTaskPreview, db: DbSession, _: CurrentAdmin
) -> AIListenTaskPreviewOut:
    if payload.task_id is not None:
        await get_task(db, payload.task_id)
    return await preview_task(db, payload)


@router.post("", response_model=AIListenTaskOut, status_code=status.HTTP_201_CREATED)
async def create_listen_task(
    payload: AIListenTaskCreate, db: DbSession, redis: RedisClient, admin: CurrentAdmin
) -> AIListenTaskOut:
    task = await _create_task(db, payload, owner_admin_id=admin.id)
    await db.commit()
    return await _out(db, redis, await get_task(db, task.id))


@router.get("/{task_id}", response_model=AIListenTaskOut)
async def read_listen_task(
    task_id: int, db: DbSession, redis: RedisClient, _: CurrentAdmin
) -> AIListenTaskOut:
    return await _out(db, redis, await get_task(db, task_id))


@router.patch("/{task_id}", response_model=AIListenTaskOut)
async def patch_listen_task(
    task_id: int, payload: AIListenTaskPatch, db: DbSession, redis: RedisClient, admin: CurrentAdmin
) -> AIListenTaskOut:
    await get_ai_setting(db, for_update=True)
    task = await get_task(db, task_id, for_update=True)
    _require_task_owner(task, admin)
    if task.desired_state == "archived":
        raise APIError(409, "ai_listen_task_archived", "已归档任务不能编辑")
    if task.config_version != payload.config_version:
        raise APIError(
            409,
            "task_version_conflict",
            "监听任务已被其他操作修改",
            {"current_version": task.config_version},
        )
    changes = payload.model_dump(exclude_unset=True, exclude={"config_version"})
    selected_all = changes.get("all_monitored_users", task.all_monitored_users)
    selected_ids = changes.get(
        "monitored_user_ids", [link.monitored_user_id for link in task.subscriptions]
    )
    selected_skills = changes.get("skill_ids", [link.skill_id for link in task.skills])
    selected_channels = changes.get("auto_publish_channels", task.auto_publish_channels or [])
    selected_bot_id = changes.get("qq_bot_id", task.qq_bot_id)
    selected_groups = changes.get("qq_group_openids", task.qq_group_openids or [])
    users, skills = await validate_selection(
        db,
        all_monitored_users=selected_all,
        user_ids=selected_ids,
        skill_ids=selected_skills,
        lock_rows=True,
    )
    await validate_qq_publish_target(
        db,
        channels=selected_channels,
        bot_id=selected_bot_id,
        group_openids=selected_groups,
    )
    now = datetime.now(UTC)
    if "monitored_user_ids" in changes or "all_monitored_users" in changes:
        old = {link.monitored_user_id: link for link in task.subscriptions}
        task.subscriptions = [
            old[user.id]
            if user.id in old
            else AIListenTaskSubscription(
                monitored_user_id=user.id,
                effective_from=now if task.desired_state == "enabled" else None,
                created_at=now,
            )
            for user in users
        ]
    if "skill_ids" in changes:
        retained = {link.skill_id: link for link in task.skills}
        desired_ids = set(selected_skills)
        for link in list(task.skills):
            if link.skill_id not in desired_ids:
                task.skills.remove(link)
        # Delete removed rows before inserting replacements under the composite
        # uniqueness constraint. Reordering retained links needs no new rows.
        await db.flush()
        for priority, skill in enumerate(skills, 1):
            link = retained.get(skill.id)
            if link is None:
                task.skills.append(AIListenTaskSkill(skill=skill, priority=priority))
            else:
                link.priority = priority
    for key in (
        "name",
        "all_monitored_users",
        "listen_mode",
        "auto_publish_channels",
        "qq_bot_id",
        "qq_group_openids",
        "max_attempts_override",
        "language_override",
        "tone_override",
        "max_output_tokens_override",
    ):
        if key in changes:
            setattr(task, key, changes[key])
    if task.owner_admin_id is None and task.auto_publish_channels:
        task.owner_admin_id = admin.id
    task.config_version += 1
    task.updated_at = now
    db.add(_event(task, "updated", "监听任务配置已修改", config_version=task.config_version))
    await db.commit()
    return await _out(db, redis, await get_task(db, task.id))


@router.post("/{task_id}/pause", response_model=AIListenTaskOut)
async def pause_listen_task(
    task_id: int, db: DbSession, redis: RedisClient, admin: CurrentAdmin
) -> AIListenTaskOut:
    await get_ai_setting(db, for_update=True)
    task = await get_task(db, task_id, for_update=True)
    _require_task_owner(task, admin)
    if task.desired_state == "archived":
        raise APIError(409, "ai_listen_task_archived", "已归档任务不能暂停")
    if task.desired_state != "paused":
        task.desired_state = "paused"
        task.updated_at = datetime.now(UTC)
        db.add(_event(task, "paused", "监听任务已暂停"))
        await db.commit()
    return await _out(db, redis, task)


@router.post("/{task_id}/resume", response_model=AIListenTaskOut)
async def resume_listen_task(
    task_id: int,
    db: DbSession,
    redis: RedisClient,
    admin: CurrentAdmin,
    switch_from_legacy: bool = False,
) -> AIListenTaskOut:
    await get_ai_setting(db, for_update=True)
    task = await get_task(db, task_id, for_update=True)
    _require_task_owner(task, admin)
    if task.desired_state == "archived":
        raise APIError(409, "ai_listen_task_archived", "已归档任务不能恢复")
    if task.desired_state != "enabled":
        await _activate(db, task, switch_from_legacy=switch_from_legacy)
        task.updated_at = datetime.now(UTC)
        await db.commit()
    return await _out(db, redis, task)


@router.post("/{task_id}/archive", response_model=AIListenTaskOut)
async def archive_listen_task(
    task_id: int, db: DbSession, redis: RedisClient, admin: CurrentAdmin
) -> AIListenTaskOut:
    await get_ai_setting(db, for_update=True)
    task = await get_task(db, task_id, for_update=True)
    _require_task_owner(task, admin)
    if task.desired_state != "archived":
        now = datetime.now(UTC)
        task.desired_state = "archived"
        task.archived_at = now
        task.updated_at = now
        await db.execute(
            update(AIGenerationJob)
            .where(
                AIGenerationJob.listen_task_id == task.id,
                AIGenerationJob.status.in_(["queued", "retry_wait"]),
            )
            .values(status="cancelled", completed_at=now, updated_at=now)
        )
        await db.execute(
            update(AIListenTaskBackfill)
            .where(
                AIListenTaskBackfill.task_id == task.id,
                AIListenTaskBackfill.status.in_(["pending", "running"]),
            )
            .values(status="cancelled", updated_at=now)
        )
        db.add(_event(task, "archived", "监听任务已归档，未领取记录已取消"))
        await db.commit()
    return await _out(db, redis, task)


@router.post("/{task_id}/queue-decision", response_model=AIListenTaskOut)
async def decide_listen_queue(
    task_id: int,
    payload: AIListenTaskQueueDecision,
    db: DbSession,
    redis: RedisClient,
    admin: CurrentAdmin,
) -> AIListenTaskOut:
    await get_ai_setting(db, for_update=True)
    task = await get_task(db, task_id, for_update=True)
    _require_task_owner(task, admin)
    if task.desired_state == "archived":
        raise APIError(409, "ai_listen_task_archived", "已归档任务不能继续旧队列")
    if not task.queue_hold_reason:
        raise APIError(409, "queue_not_held", "当前没有待决策的旧队列")
    if payload.decision == "continue_old_snapshot":
        await validate_selection(
            db,
            all_monitored_users=task.all_monitored_users,
            user_ids=[link.monitored_user_id for link in task.subscriptions],
            skill_ids=[link.skill_id for link in task.skills],
        )
    else:
        now = datetime.now(UTC)
        await db.execute(
            update(AIGenerationJob)
            .where(
                AIGenerationJob.listen_task_id == task.id,
                AIGenerationJob.status.in_(["queued", "retry_wait"]),
            )
            .values(status="cancelled", completed_at=now, updated_at=now)
        )
    task.queue_hold_reason = None
    task.updated_at = datetime.now(UTC)
    db.add(_event(task, "queue_decision", "旧队列处理决定已保存", decision=payload.decision))
    await db.commit()
    return await _out(db, redis, task)


@router.post("/{task_id}/copy", response_model=AIListenTaskOut, status_code=status.HTTP_201_CREATED)
async def copy_listen_task(
    task_id: int, db: DbSession, redis: RedisClient, admin: CurrentAdmin
) -> AIListenTaskOut:
    original = await get_task(db, task_id)
    _require_task_owner(original, admin)
    payload = AIListenTaskCreate(
        name=f"{original.name}（副本）",
        desired_state="paused",
        all_monitored_users=original.all_monitored_users,
        monitored_user_ids=[link.monitored_user_id for link in original.subscriptions],
        listen_mode=original.listen_mode,
        auto_publish_channels=original.auto_publish_channels or [],
        qq_bot_id=original.qq_bot_id,
        qq_group_openids=original.qq_group_openids or [],
        skill_ids=[link.skill_id for link in original.skills],
        initial_sync_days=original.initial_sync_days,
        max_attempts_override=original.max_attempts_override,
        language_override=original.language_override,
        tone_override=original.tone_override,
        max_output_tokens_override=original.max_output_tokens_override,
    )
    task = await _create_task(db, payload, owner_admin_id=admin.id, copied_from=original.id)
    await db.commit()
    return await _out(db, redis, await get_task(db, task.id))


@router.post(
    "/{task_id}/backfills",
    response_model=AIListenTaskBackfillOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_listen_backfill(
    task_id: int, payload: AIListenTaskBackfillCreate, db: DbSession, admin: CurrentAdmin
) -> AIListenTaskBackfillOut:
    await get_ai_setting(db, for_update=True)
    task = await get_task(db, task_id, for_update=True)
    _require_task_owner(task, admin)
    if task.desired_state == "archived":
        raise APIError(409, "ai_listen_task_archived", "已归档任务不能提交历史补生成")
    try:
        backfill = await enqueue_backfill_request(
            db,
            task,
            from_at=payload.from_at,
            to_at=payload.to_at,
            request_id=payload.request_id,
            exclude_legacy_generated=payload.exclude_legacy_generated,
            all_user_ids=await active_all_user_ids(db) if task.all_monitored_users else None,
        )
    except ValueError as exc:
        raise APIError(409, str(exc), "回填请求 ID 已用于其他时间范围") from None
    await db.commit()
    return AIListenTaskBackfillOut.model_validate(backfill)


@router.get("/{task_id}/backfills", response_model=Page[AIListenTaskBackfillOut])
async def list_listen_backfills(
    task_id: int,
    db: DbSession,
    _: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[AIListenTaskBackfillOut]:
    await get_task(db, task_id)
    total = int(
        await db.scalar(
            select(func.count(AIListenTaskBackfill.id)).where(
                AIListenTaskBackfill.task_id == task_id
            )
        )
        or 0
    )
    rows = list(
        await db.scalars(
            select(AIListenTaskBackfill)
            .where(AIListenTaskBackfill.task_id == task_id)
            .order_by(AIListenTaskBackfill.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page(
        items=[AIListenTaskBackfillOut.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{task_id}/events", response_model=Page[AIListenTaskEventOut])
async def list_listen_events(
    task_id: int,
    db: DbSession,
    _: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[AIListenTaskEventOut]:
    await get_task(db, task_id)
    total = int(
        await db.scalar(
            select(func.count(AIListenTaskEvent.id)).where(AIListenTaskEvent.task_id == task_id)
        )
        or 0
    )
    rows = list(
        await db.scalars(
            select(AIListenTaskEvent)
            .where(AIListenTaskEvent.task_id == task_id)
            .order_by(AIListenTaskEvent.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page(
        items=[AIListenTaskEventOut.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
