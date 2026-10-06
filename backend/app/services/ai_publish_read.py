"""Batch read automatic publish results for article and AI job pages."""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_publish import AIPublishDispatch
from app.schemas.ai_publish import AIPublishDispatchOut


async def _dispatches_by_id(
    db: AsyncSession, ids: Iterable[int], *, key: str
) -> dict[int, list[AIPublishDispatchOut]]:
    selected = sorted(set(ids))
    if not selected:
        return {}
    column = AIPublishDispatch.job_id if key == "job" else AIPublishDispatch.draft_id
    rows = await db.scalars(
        select(AIPublishDispatch).where(column.in_(selected)).order_by(column, AIPublishDispatch.id)
    )
    grouped: dict[int, list[AIPublishDispatchOut]] = {}
    for row in rows:
        grouped.setdefault(getattr(row, f"{key}_id"), []).append(
            AIPublishDispatchOut.model_validate(row)
        )
    return grouped


async def dispatches_by_job(
    db: AsyncSession, job_ids: Iterable[int]
) -> dict[int, list[AIPublishDispatchOut]]:
    return await _dispatches_by_id(db, job_ids, key="job")


async def dispatches_by_draft(
    db: AsyncSession, draft_ids: Iterable[int]
) -> dict[int, list[AIPublishDispatchOut]]:
    return await _dispatches_by_id(db, draft_ids, key="draft")
