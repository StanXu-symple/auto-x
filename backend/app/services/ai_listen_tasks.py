"""Validation, health and read models for AI listening task configuration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import case, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.errors import APIError
from app.models.ai import (
    AIGenerationJob,
    AIListenTask,
    AIListenTaskSkill,
    AISetting,
    AISkill,
)
from app.models.ai_data_source import AIDataSource
from app.models.monitored_user import MonitoredUser
from app.models.tweet import Tweet
from app.schemas.ai import (
    AIListenTaskAccountOut,
    AIListenTaskCondition,
    AIListenTaskOut,
    AIListenTaskPreview,
    AIListenTaskPreviewOut,
    AIListenTaskSkillOut,
    AIListenTaskStats,
)

TASK_LOAD = (
    selectinload(AIListenTask.subscriptions),
    selectinload(AIListenTask.skills).selectinload(AIListenTaskSkill.skill),
)
STATS_KEYS = (
    "matched",
    "queued",
    "running",
    "retry_wait",
    "succeeded",
    "failed",
    "cancelled",
    "lifetime_attempts",
)


async def validate_selection(
    db: AsyncSession,
    *,
    all_monitored_users: bool,
    user_ids: list[int],
    skill_ids: list[int],
    lock_rows: bool = False,
) -> tuple[list[MonitoredUser], list[AISkill]]:
    if not all_monitored_users and not user_ids:
        raise APIError(422, "monitored_users_required", "请至少选择一个监听账号")
    user_query = (
        select(MonitoredUser).where(MonitoredUser.id.in_(user_ids)).order_by(MonitoredUser.id)
    )
    if lock_rows:
        user_query = user_query.with_for_update(read=True)
    selected_users = list(await db.scalars(user_query)) if user_ids else []
    if len(selected_users) != len(user_ids) or any(
        user.archived_at is not None for user in selected_users
    ):
        raise APIError(422, "invalid_monitored_user_ids", "所选监听账号不存在或已归档")
    skill_query = (
        select(AISkill)
        .where(AISkill.id.in_(skill_ids), AISkill.is_active.is_(True))
        .order_by(AISkill.id)
    )
    if lock_rows:
        skill_query = skill_query.with_for_update(read=True)
    selected_skills = list(await db.scalars(skill_query))
    if len(selected_skills) != len(skill_ids):
        raise APIError(422, "invalid_skill_ids", "所有选中 Skill 必须存在且已启用")
    by_id = {skill.id: skill for skill in selected_skills}
    return selected_users, [by_id[item] for item in skill_ids]


async def get_task(db: AsyncSession, task_id: int, *, for_update: bool = False) -> AIListenTask:
    stmt = select(AIListenTask).where(AIListenTask.id == task_id).options(*TASK_LOAD)
    if for_update:
        stmt = stmt.with_for_update(of=AIListenTask)
    task = await db.scalar(stmt)
    if task is None:
        raise APIError(404, "ai_listen_task_not_found", "监听任务不存在")
    return task


def initial_window(task: AIListenTask, activated_at: datetime) -> tuple[datetime, datetime] | None:
    if task.initial_sync_days <= 0:
        return None
    return activated_at - timedelta(days=task.initial_sync_days), activated_at


async def active_all_user_ids(db: AsyncSession) -> list[int]:
    return list(
        await db.scalars(
            select(MonitoredUser.id).where(
                MonitoredUser.is_active.is_(True), MonitoredUser.archived_at.is_(None)
            )
        )
    )


async def task_stats(
    db: AsyncSession, task_ids: list[int]
) -> dict[int, tuple[AIListenTaskStats, dict]]:
    if not task_ids:
        return {}
    rows = (
        (
            await db.execute(
                select(
                    AIGenerationJob.listen_task_id,
                    func.count(AIGenerationJob.id).label("matched"),
                    *[
                        func.sum(case((AIGenerationJob.status == status, 1), else_=0)).label(status)
                        for status in (
                            "queued",
                            "running",
                            "retry_wait",
                            "succeeded",
                            "failed",
                            "cancelled",
                        )
                    ],
                    func.coalesce(func.sum(AIGenerationJob.lifetime_attempts), 0).label(
                        "lifetime_attempts"
                    ),
                    func.max(AIGenerationJob.created_at).label("last_matched_at"),
                    func.max(
                        case((AIGenerationJob.status == "succeeded", AIGenerationJob.completed_at))
                    ).label("last_success_at"),
                    func.max(
                        case((AIGenerationJob.status == "failed", AIGenerationJob.completed_at))
                    ).label("last_failure_at"),
                    func.max(AIGenerationJob.started_at).label("last_ai_started_at"),
                )
                .where(AIGenerationJob.listen_task_id.in_(task_ids))
                .group_by(AIGenerationJob.listen_task_id)
            )
        )
        .mappings()
        .all()
    )
    result = {}
    for row in rows:
        stats = AIListenTaskStats(**{key: int(row[key] or 0) for key in STATS_KEYS})
        result[row["listen_task_id"]] = (
            stats,
            {
                key: row[key]
                for key in (
                    "last_matched_at",
                    "last_success_at",
                    "last_failure_at",
                    "last_ai_started_at",
                )
            },
        )
    return result


def summarize_stats(rows: dict[int, tuple[AIListenTaskStats, dict]]) -> AIListenTaskStats:
    return AIListenTaskStats(
        **{key: sum(getattr(stats, key) for stats, _ in rows.values()) for key in STATS_KEYS}
    )


def _account_available(user: MonitoredUser, mode: str) -> bool:
    return (
        user.archived_at is None
        and user.is_active
        and (
            mode not in {"reply", "retweet"}
            or (mode == "reply" and user.include_replies)
            or (mode == "retweet" and user.include_retweets)
        )
    )


async def task_out(
    db: AsyncSession,
    task: AIListenTask,
    *,
    setting: AISetting,
    source: AIDataSource | None,
    worker_online: bool,
    stats: tuple[AIListenTaskStats, dict] | None = None,
    all_users: list[MonitoredUser] | None = None,
) -> AIListenTaskOut:
    if all_users is None:
        all_users = list(await db.scalars(select(MonitoredUser).order_by(MonitoredUser.id)))
    users_by_id = {user.id: user for user in all_users}
    if not task.all_monitored_users:
        missing_ids = [
            link.monitored_user_id
            for link in task.subscriptions
            if link.monitored_user_id not in users_by_id
        ]
        if missing_ids:
            archived = list(
                await db.scalars(select(MonitoredUser).where(MonitoredUser.id.in_(missing_ids)))
            )
            users_by_id.update({user.id: user for user in archived})
    account_ids = (
        [user.id for user in all_users if user.archived_at is None]
        if task.all_monitored_users
        else [link.monitored_user_id for link in task.subscriptions]
    )
    accounts = [users_by_id[item] for item in account_ids if item in users_by_id]
    usable = [user for user in accounts if _account_available(user, task.listen_mode)]
    health_reasons: list[str] = []
    if not accounts:
        health_reasons.append("没有可监听的账号")
    elif not usable:
        health_reasons.append("所选账号均未启用采集或未采集此类型内容")
    elif len(usable) < len(accounts):
        health_reasons.append(f"{len(usable)}/{len(accounts)} 个账号可采集此类型内容")
    ordered_skills = sorted(task.skills, key=lambda link: link.priority)
    missing_skills = [link.skill_id for link in ordered_skills if not link.skill.is_active]
    if missing_skills:
        health_reasons.append("所选 Skill 已停用")
    if task.queue_hold_reason:
        health_reasons.append("旧队列等待处理决定")
    health_status = (
        "blocked"
        if not usable or missing_skills or task.queue_hold_reason
        else "limited"
        if health_reasons
        else "healthy"
    )
    dependency_reasons: list[str] = []
    if not setting.enabled:
        dependency_reasons.append("AI 总开关已关闭")
    if not setting.auto_generate:
        dependency_reasons.append("自动监听开关已关闭")
    if setting.auto_trigger_mode != "listening_tasks":
        dependency_reasons.append("当前仍在旧自动生成兼容模式")
    if source is None:
        dependency_reasons.append("AI 数据源未配置")
    if not worker_online:
        dependency_reasons.append("AI Worker 离线")
    counts, times = stats or (AIListenTaskStats(), {})
    return AIListenTaskOut(
        id=task.id,
        name=task.name,
        desired_state=task.desired_state,
        queue_hold_reason=task.queue_hold_reason,
        all_monitored_users=task.all_monitored_users,
        monitored_user_ids=[link.monitored_user_id for link in task.subscriptions],
        accounts=[AIListenTaskAccountOut.model_validate(user) for user in accounts],
        listen_mode=task.listen_mode,
        skill_ids=[link.skill_id for link in ordered_skills],
        skills=[
            AIListenTaskSkillOut(
                id=link.skill_id,
                name=link.skill.name,
                is_active=link.skill.is_active,
                version=link.skill.version,
                priority=link.priority,
            )
            for link in ordered_skills
        ],
        feature_code=task.feature_code,
        config_version=task.config_version,
        activated_at=task.activated_at,
        effective_from=task.effective_from,
        initial_sync_days=task.initial_sync_days,
        initial_backfill_from=task.initial_backfill_from,
        initial_backfill_to=task.initial_backfill_to,
        archived_at=task.archived_at,
        max_attempts_override=task.max_attempts_override,
        language_override=task.language_override,
        tone_override=task.tone_override,
        max_output_tokens_override=task.max_output_tokens_override,
        health=AIListenTaskCondition(status=health_status, reasons=health_reasons),
        dependency=AIListenTaskCondition(
            status="blocked" if dependency_reasons else "ready", reasons=dependency_reasons
        ),
        stats=counts,
        last_matched_at=times.get("last_matched_at"),
        last_success_at=times.get("last_success_at"),
        last_failure_at=times.get("last_failure_at"),
        last_ai_started_at=times.get("last_ai_started_at"),
        data_source_name=source.name if source else None,
        data_source_model=source.model_name if source else None,
        data_source_verified_at=source.last_verified_at if source else None,
        data_source_verification_status=source.verification_status if source else None,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


async def preview_task(db: AsyncSession, payload: AIListenTaskPreview) -> AIListenTaskPreviewOut:
    users, _ = await validate_selection(
        db,
        all_monitored_users=payload.all_monitored_users,
        user_ids=payload.monitored_user_ids,
        skill_ids=payload.skill_ids,
    )
    if payload.all_monitored_users:
        users = list(
            await db.scalars(
                select(MonitoredUser)
                .where(MonitoredUser.archived_at.is_(None))
                .order_by(MonitoredUser.id)
            )
        )
    historical = payload.from_at is not None or payload.initial_sync_days > 0

    def available(user: MonitoredUser) -> bool:
        return (
            user.archived_at is None and user.is_active
            if historical
            else _account_available(user, payload.listen_mode)
        )

    unavailable = [
        {
            "id": user.id,
            "username": user.username,
            "reason": (
                "采集已关闭"
                if not user.is_active
                else "未采集回复"
                if payload.listen_mode == "reply" and not user.include_replies
                else "未采集转推"
                if payload.listen_mode == "retweet" and not user.include_retweets
                else ""
            ),
        }
        for user in users
        if not available(user)
    ]
    usable_ids = [user.id for user in users if available(user)]
    now = datetime.now(UTC)
    from_at = payload.from_at or (
        now - timedelta(days=payload.initial_sync_days) if payload.initial_sync_days else None
    )
    to_at = payload.to_at or (now if payload.initial_sync_days else None)
    if not usable_ids:
        return AIListenTaskPreviewOut(
            matched=0,
            duplicates=0,
            legacy_generated=0,
            pending=0,
            unavailable_accounts=unavailable,
            from_at=from_at,
            to_at=to_at,
        )
    filters = [Tweet.monitored_user_id.in_(usable_ids)]
    if payload.listen_mode != "all":
        filters.append(Tweet.tweet_type == payload.listen_mode)
    if from_at is not None and to_at is not None:
        filters.extend([Tweet.posted_at >= from_at, Tweet.posted_at < to_at])
    else:
        # With no historical range, existing posts are outside the future-only window.
        filters.append(Tweet.posted_at >= now)
    duplicate_exists = (
        exists(
            select(AIGenerationJob.id).where(
                AIGenerationJob.source_tweet_id == Tweet.id,
                AIGenerationJob.listen_task_id == payload.task_id,
            )
        )
        if payload.task_id
        else None
    )
    legacy_exists = exists(
        select(AIGenerationJob.id).where(
            AIGenerationJob.source_tweet_id == Tweet.id,
            AIGenerationJob.trigger_type == "legacy_auto",
        )
    )
    rows = (
        await db.execute(
            select(
                func.count(Tweet.id),
                func.sum(case((duplicate_exists, 1), else_=0))
                if duplicate_exists is not None
                else func.count(Tweet.id) * 0,
                func.sum(case((legacy_exists, 1), else_=0)),
                func.sum(
                    case(
                        (
                            (duplicate_exists if duplicate_exists is not None else Tweet.id == -1)
                            | (
                                legacy_exists
                                if payload.exclude_legacy_generated
                                else Tweet.id == -1
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
            ).where(*filters)
        )
    ).one()
    matched = int(rows[0] or 0)
    duplicates = int(rows[1] or 0)
    legacy = int(rows[2] or 0)
    excluded = int(rows[3] or 0)
    return AIListenTaskPreviewOut(
        matched=matched,
        duplicates=duplicates,
        legacy_generated=legacy,
        pending=max(0, matched - excluded),
        unavailable_accounts=unavailable,
        from_at=from_at,
        to_at=to_at,
    )
