"""Apply authenticated XHS completion notifications idempotently."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai import ArticlePublishAttempt
from app.models.ai_publish import AIPublishDispatch
from app.models.browser_job import BrowserJobResult


def _published_url(data: dict) -> str | None:
    detail = data.get("result")
    value = detail.get("url") if isinstance(detail, dict) else data.get("url")
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        note_link = (
            host in ("xiaohongshu.com", "www.xiaohongshu.com")
            and re.fullmatch(r"/(?:explore|discovery/item)/[a-fA-F0-9]{24}/?", parsed.path)
        ) or (
            (host == "xhslink.com" or host.endswith(".xhslink.com"))
            and bool(parsed.path.strip("/"))
        )
        valid = (
            parsed.scheme == "https" and parsed.port in (None, 443)
            and parsed.username is None and parsed.password is None
            and note_link
            and not any(character.isspace() or ord(character) < 32 for character in value)
        )
    except ValueError:
        return None
    return value if valid else None


async def apply_xhs_job_result(session: AsyncSession, job_id: str) -> bool:
    """Consume a persisted worker result, never a caller-supplied outcome.

    Caller owns the transaction. Repeated callbacks and dispatcher recovery use
    the same function; a terminal human decision or newer attempt is preserved.
    """
    result = await session.get(BrowserJobResult, ("xhs", job_id), populate_existing=True)
    if result is None or result.state not in ("succeeded", "failed", "uncertain"):
        return False
    dispatch = await session.scalar(
        select(AIPublishDispatch).where(
            AIPublishDispatch.channel == "xhs", AIPublishDispatch.xhs_job_id == job_id
        ).with_for_update().execution_options(populate_existing=True)
    )
    if (
        dispatch is None
        or dispatch.owner_admin_id != result.admin_id
        or dispatch.status not in ("dispatching", "reconciling", "uncertain")
        or dispatch.reconciled_at is not None
        or dispatch.reconciliation_result is not None
        or not dispatch.article_publish_attempt_id
    ):
        return False
    attempt = await session.get(ArticlePublishAttempt, dispatch.article_publish_attempt_id)
    if (
        attempt is None or attempt.article_id != dispatch.draft_id or attempt.channel != "xhs"
        or attempt.reconciled_at is not None or attempt.reconciliation_result is not None
    ):
        return False
    status = "published" if result.state == "succeeded" else result.state
    error = (result.error or None) if status != "published" else None
    completed_at = result.completed_at or datetime.now(UTC)
    published_url = _published_url(result.data or {}) if status == "published" else None
    changed = (
        dispatch.status != status or dispatch.last_error != error
        or dispatch.published_url != published_url
    )
    dispatch.status = status
    dispatch.last_error = error[:2000] if error else None
    dispatch.completed_at = completed_at
    dispatch.published_url = published_url
    # Article history has a legacy published/failed status vocabulary. The
    # dispatch preserves whether this is a verified failure or an unknown
    # outcome, so an uncertain attempt cannot be retried implicitly.
    attempt.status = "published" if status == "published" else "failed"
    attempt.error = dispatch.last_error
    attempt.completed_at = completed_at
    attempt.published_url = published_url
    return changed
