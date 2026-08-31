from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import case, func, select

from app.api.deps import CurrentAdmin, DbSession, RedisClient
from app.api.routes.polling_logs import _polling_log_out
from app.api.routes.tweets import _tweet_out
from app.models.monitored_user import MonitoredUser
from app.models.polling_log import PollingLog
from app.models.tweet import Tweet
from app.schemas.dashboard import CountSummary, DashboardSummary, PollingSummary
from app.services.system_health import collect_system_metrics

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def dashboard_summary(
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
) -> DashboardSummary:
    now = datetime.now(UTC)
    since = now - timedelta(hours=24)
    user_counts = (
        await db.execute(
            select(
                func.count(MonitoredUser.id),
                func.sum(case((MonitoredUser.is_active.is_(True), 1), else_=0)),
            )
        )
    ).one()
    tweet_total = int(await db.scalar(select(func.count(Tweet.id))) or 0)
    tweet_day = int(
        await db.scalar(select(func.count(Tweet.id)).where(Tweet.posted_at >= since)) or 0
    )
    run_counts = (
        await db.execute(
            select(
                func.count(PollingLog.id),
                func.sum(case((PollingLog.status == "success", 1), else_=0)),
                func.sum(
                    case(
                        (PollingLog.status.in_(["error", "rate_limited"]), 1),
                        else_=0,
                    )
                ),
            ).where(PollingLog.started_at >= since)
        )
    ).one()
    due_users = int(
        await db.scalar(
            select(func.count(MonitoredUser.id)).where(
                MonitoredUser.is_active.is_(True),
                MonitoredUser.next_poll_at <= now,
            )
        )
        or 0
    )
    recent_tweet_rows = (
        await db.execute(
            select(Tweet, MonitoredUser.username)
            .join(MonitoredUser, Tweet.monitored_user_id == MonitoredUser.id)
            .order_by(Tweet.posted_at.desc())
            .limit(10)
        )
    ).all()
    recent_run_rows = (
        await db.execute(
            select(PollingLog, MonitoredUser.username)
            .join(MonitoredUser, PollingLog.monitored_user_id == MonitoredUser.id)
            .order_by(PollingLog.started_at.desc())
            .limit(10)
        )
    ).all()
    server = await collect_system_metrics(db, redis)
    total_users = int(user_counts[0] or 0)
    active_users = int(user_counts[1] or 0)
    total_runs = int(run_counts[0] or 0)
    successful_runs = int(run_counts[1] or 0)
    failed_runs = int(run_counts[2] or 0)
    return DashboardSummary(
        generated_at=now,
        counts=CountSummary(
            monitored_users=total_users,
            active_users=active_users,
            paused_users=total_users - active_users,
            tweets=tweet_total,
            tweets_last_24h=tweet_day,
        ),
        polling=PollingSummary(
            runs_last_24h=total_runs,
            successful_runs_last_24h=successful_runs,
            failed_runs_last_24h=failed_runs,
            success_rate=round(successful_runs / total_runs * 100, 2) if total_runs else 0.0,
            due_users=due_users,
        ),
        server=server,
        recent_tweets=[
            _tweet_out(tweet, username, include_raw=False) for tweet, username in recent_tweet_rows
        ],
        recent_runs=[_polling_log_out(log, username) for log, username in recent_run_rows],
    )
