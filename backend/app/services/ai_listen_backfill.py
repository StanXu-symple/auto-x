"""Durable, bounded scanning of explicitly requested AI listening history."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai import (
    AIFeature,
    AIListenTask,
    AIListenTaskBackfill,
    AIListenTaskEvent,
)
from app.models.monitored_user import MonitoredUser
from app.models.tweet import Tweet
from app.services.ai_jobs import get_ai_setting


def _locked_backfill_users(user_ids: list[int]):
    # Account pause/archive updates need an exclusive row lock. Hold this shared
    # lock through enqueue and cursor commit so a state change cannot hide rows
    # after availability was checked.
    return (
        select(MonitoredUser)
        .where(MonitoredUser.id.in_(user_ids))
        .order_by(MonitoredUser.id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )


def task_snapshot(
    task: AIListenTask,
    *,
    exclude_legacy_generated: bool = True,
    all_user_ids: list[int] | None = None,
) -> dict:
    """Freeze the explicit selection so edits cannot silently widen a pending scan."""
    return {
        "config_version": task.config_version,
        "owner_admin_id": task.owner_admin_id,
        "all_monitored_users": task.all_monitored_users,
        "monitored_user_ids": (
            all_user_ids
            if task.all_monitored_users and all_user_ids is not None
            else [link.monitored_user_id for link in task.subscriptions]
        ),
        "subscriptions": [
            {
                "monitored_user_id": link.monitored_user_id,
                "effective_from": link.effective_from.isoformat() if link.effective_from else None,
            }
            for link in task.subscriptions
        ],
        "listen_mode": task.listen_mode,
        "auto_publish_channels": list(task.auto_publish_channels or []),
        "qq_bot_id": task.qq_bot_id,
        "qq_group_openids": list(task.qq_group_openids or []),
        "skill_ids": [
            link.skill_id for link in sorted(task.skills, key=lambda item: item.priority)
        ],
        "skills": [
            {
                "id": link.skill.id,
                "name": link.skill.name,
                "description": link.skill.description,
                "instructions": link.skill.instructions,
                "output_schema": link.skill.output_schema,
                "version": link.skill.version,
                "remote_skill_id": link.skill.remote_skill_id,
                "remote_skill_version": link.skill.remote_skill_version,
            }
            for link in sorted(task.skills, key=lambda item: item.priority)
        ],
        "feature_code": task.feature_code,
        "max_attempts_override": task.max_attempts_override,
        "language_override": task.language_override,
        "tone_override": task.tone_override,
        "max_output_tokens_override": task.max_output_tokens_override,
        "exclude_legacy_generated": exclude_legacy_generated,
    }


async def enqueue_backfill_request(
    db: AsyncSession,
    task: AIListenTask,
    *,
    from_at: datetime,
    to_at: datetime,
    request_id: str | None = None,
    exclude_legacy_generated: bool = True,
    all_user_ids: list[int] | None = None,
) -> AIListenTaskBackfill:
    if from_at.tzinfo is None or to_at.tzinfo is None or from_at >= to_at:
        raise ValueError("invalid_backfill_window")
    request_id = request_id or str(uuid.uuid4())
    existing = await db.scalar(
        select(AIListenTaskBackfill).where(
            AIListenTaskBackfill.task_id == task.id,
            AIListenTaskBackfill.request_id == request_id,
        )
    )
    if existing is not None:
        if existing.from_at != from_at or existing.to_at != to_at:
            raise ValueError("backfill_request_conflict")
        return existing
    now = datetime.now(UTC)
    backfill = AIListenTaskBackfill(
        task_id=task.id,
        request_id=request_id,
        from_at=from_at,
        to_at=to_at,
        config_snapshot=task_snapshot(
            task, exclude_legacy_generated=exclude_legacy_generated, all_user_ids=all_user_ids
        ),
        status="pending",
        scanned_count=0,
        enqueued_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(backfill)
    await db.flush()
    db.add(
        AIListenTaskEvent(
            task_id=task.id,
            backfill_id=backfill.id,
            event_type="backfill_requested",
            summary="已提交历史补生成请求",
            details={"from_at": from_at.isoformat(), "to_at": to_at.isoformat()},
            created_at=now,
        )
    )
    return backfill


async def process_pending_backfills(db: AsyncSession, batch_size: int = 100) -> int:
    """Scan one bounded batch. Caller commits; rollback leaves the cursor unchanged."""
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be in [1, 1000]")
    # Keep the claim lock order consistent with the AI worker and task APIs.
    setting = await get_ai_setting(db, for_update=True)
    if (
        not setting.enabled
        or not setting.auto_generate
        or setting.auto_trigger_mode != "listening_tasks"
    ):
        return 0
    candidates = (
        await db.execute(
            select(AIListenTaskBackfill.id, AIListenTaskBackfill.task_id)
            .join(AIListenTask, AIListenTask.id == AIListenTaskBackfill.task_id)
            .where(
                AIListenTaskBackfill.status.in_(["pending", "running"]),
                AIListenTask.desired_state == "enabled",
                AIListenTask.queue_hold_reason.is_(None),
            )
            .order_by(AIListenTaskBackfill.updated_at.asc(), AIListenTaskBackfill.id.asc())
            .limit(32)
        )
    ).all()
    for candidate in candidates:
        scanned = await _process_backfill_candidate(db, candidate.id, candidate.task_id, batch_size)
        if scanned is not None:
            return scanned
    return 0


async def _process_backfill_candidate(
    db: AsyncSession, backfill_id: int, task_id: int, batch_size: int
) -> int | None:
    """Return None when a waiting request should give the next candidate a turn."""
    task = await db.scalar(
        select(AIListenTask)
        .where(AIListenTask.id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None or task.desired_state != "enabled" or task.queue_hold_reason:
        return None
    backfill = await db.scalar(
        select(AIListenTaskBackfill)
        .where(AIListenTaskBackfill.id == backfill_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if backfill is None or backfill.status not in {"pending", "running"}:
        return None
    snapshot = backfill.config_snapshot or {}
    user_ids = snapshot.get("monitored_user_ids") or []
    if not user_ids:
        backfill.status = "completed"
        backfill.updated_at = datetime.now(UTC)
        return 0
    # The enqueue path intentionally skips temporarily unavailable accounts and
    # features. Do not advance a single shared cursor past their historical posts.
    users = list(await db.scalars(_locked_backfill_users(user_ids)))
    by_id = {user.id: user for user in users}
    unavailable = [
        user_id
        for user_id in user_ids
        if user_id not in by_id
        or not by_id[user_id].is_active
        or by_id[user_id].archived_at is not None
    ]
    feature_available = await db.scalar(
        select(AIFeature.id).where(
            AIFeature.code == snapshot.get("feature_code", "article_generation"),
            AIFeature.is_active.is_(True),
        )
    )
    if unavailable or feature_available is None:
        backfill.status = "pending"
        backfill.last_error = (
            f"账号 {unavailable} 的采集配置暂不可用" if unavailable else "AI 功能点暂不可用"
        )
        backfill.updated_at = datetime.now(UTC)
        return None
    backfill.last_error = None
    mode = snapshot.get("listen_mode", "original")
    conditions = [
        Tweet.monitored_user_id.in_(user_ids),
        Tweet.posted_at >= backfill.from_at,
        Tweet.posted_at < backfill.to_at,
    ]
    if mode != "all":
        conditions.append(Tweet.tweet_type == mode)
    if backfill.cursor_posted_at is not None and backfill.cursor_tweet_id is not None:
        conditions.append(
            or_(
                Tweet.posted_at > backfill.cursor_posted_at,
                and_(
                    Tweet.posted_at == backfill.cursor_posted_at,
                    Tweet.id > backfill.cursor_tweet_id,
                ),
            )
        )
    rows = (
        await db.execute(
            select(Tweet.id, Tweet.posted_at)
            .where(*conditions)
            .order_by(Tweet.posted_at.asc(), Tweet.id.asc())
            .limit(batch_size)
        )
    ).all()
    if not rows:
        backfill.status = "completed"
        backfill.updated_at = datetime.now(UTC)
        db.add(
            AIListenTaskEvent(
                task_id=task.id,
                backfill_id=backfill.id,
                event_type="backfill_completed",
                summary=f"历史扫描完成，入队 {backfill.enqueued_count} 条",
                details={"scanned": backfill.scanned_count, "enqueued": backfill.enqueued_count},
            )
        )
        return None
    from app.services.ai_jobs import enqueue_listening_jobs

    inserted = await enqueue_listening_jobs(
        db,
        [row.id for row in rows],
        task_id=task.id,
        backfill_window=(backfill.from_at, backfill.to_at),
        backfill_snapshot=snapshot,
    )
    if task.queue_hold_reason:
        # Skill invalidation may be discovered during enqueue. Retain the cursor so
        # this batch can be retried after the administrator resolves the old queue.
        backfill.status = "pending"
        backfill.updated_at = datetime.now(UTC)
        return None
    backfill.cursor_tweet_id = rows[-1].id
    backfill.cursor_posted_at = rows[-1].posted_at
    backfill.scanned_count += len(rows)
    backfill.enqueued_count += inserted
    backfill.status = "running" if len(rows) == batch_size else "completed"
    backfill.updated_at = datetime.now(UTC)
    db.add(
        AIListenTaskEvent(
            task_id=task.id,
            backfill_id=backfill.id,
            event_type="backfill_batch" if backfill.status == "running" else "backfill_completed",
            summary=f"扫描 {len(rows)} 条，入队 {inserted} 条",
            details={"scanned": backfill.scanned_count, "enqueued": backfill.enqueued_count},
        )
    )
    return len(rows)
