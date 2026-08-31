from datetime import datetime

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import CurrentAdmin, DbSession
from app.core.time import to_mysql_utc_naive
from app.models.monitored_user import MonitoredUser
from app.models.polling_log import PollingLog
from app.schemas.common import Page
from app.schemas.polling import PollingLogOut

router = APIRouter(tags=["Polling logs"])


def _polling_log_out(log: PollingLog, username: str) -> PollingLogOut:
    return PollingLogOut(
        id=log.id,
        monitored_user_id=log.monitored_user_id,
        username=username,
        trigger=log.trigger,
        status=log.status,
        worker_id=log.worker_id,
        started_at=log.started_at,
        finished_at=log.finished_at,
        duration_ms=log.duration_ms,
        tweets_fetched=log.tweets_fetched,
        tweets_inserted=log.tweets_inserted,
        http_status=log.http_status,
        error_message=log.error_message,
        rate_limit_reset_at=log.rate_limit_reset_at,
    )


@router.get("", response_model=Page[PollingLogOut])
async def list_polling_logs(
    db: DbSession,
    _: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    monitored_user_id: int | None = None,
    status_filter: str | None = Query(default=None, alias="status", max_length=24),
    trigger: str | None = Query(default=None, max_length=16),
    started_after: datetime | None = None,
    started_before: datetime | None = None,
) -> Page[PollingLogOut]:
    conditions = []
    if monitored_user_id is not None:
        conditions.append(PollingLog.monitored_user_id == monitored_user_id)
    if status_filter:
        conditions.append(PollingLog.status == status_filter)
    if trigger:
        conditions.append(PollingLog.trigger == trigger)
    if started_after:
        conditions.append(PollingLog.started_at >= to_mysql_utc_naive(started_after))
    if started_before:
        conditions.append(PollingLog.started_at <= to_mysql_utc_naive(started_before))

    joined = PollingLog.__table__.join(
        MonitoredUser.__table__, PollingLog.monitored_user_id == MonitoredUser.id
    )
    total = int(
        await db.scalar(select(func.count(PollingLog.id)).select_from(joined).where(*conditions))
        or 0
    )
    rows = (
        await db.execute(
            select(PollingLog, MonitoredUser.username)
            .select_from(joined)
            .where(*conditions)
            .order_by(PollingLog.started_at.desc(), PollingLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return Page(
        items=[_polling_log_out(log, username) for log, username in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
