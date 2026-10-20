"""Durable, bounded scanning of explicitly requested AI listening history."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai import (
    AIListenTask,
    AIListenTaskBackfill,
    AIListenTaskEvent,
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
