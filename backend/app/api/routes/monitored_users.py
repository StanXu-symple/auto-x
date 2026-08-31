import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentAdmin, DbSession
from app.api.errors import APIError
from app.core.config import get_settings
from app.core.time import as_utc
from app.models.monitored_user import MonitoredUser
from app.models.tweet import Tweet
from app.schemas.common import AcceptedResponse, MessageResponse, Page
from app.schemas.monitored_user import (
    MonitoredUserCreate,
    MonitoredUserOut,
    MonitoredUserUpdate,
)
from app.services.settings_service import effective_interval, get_polling_settings

router = APIRouter(tags=["Monitored users"])


def _clear_pagination_checkpoint(user: MonitoredUser) -> None:
    user.pagination_token = None
    user.pagination_since_id = None
    user.pagination_newest_id = None


def _serialize_user(
    user: MonitoredUser,
    tweet_count: int,
    polling_settings: dict[str, int],
) -> MonitoredUserOut:
    return MonitoredUserOut(
        id=user.id,
        username=user.username,
        x_user_id=user.x_user_id,
        display_name=user.display_name,
        is_active=user.is_active,
        include_replies=user.include_replies,
        include_retweets=user.include_retweets,
        poll_interval_seconds=user.poll_interval_seconds,
        effective_poll_interval_seconds=effective_interval(
            user.poll_interval_seconds, polling_settings
        ),
        status=user.status,
        last_tweet_id=user.last_tweet_id,
        pagination_in_progress=user.pagination_token is not None,
        manual_poll_pending=user.manual_poll_token is not None,
        poll_generation=user.poll_generation,
        last_polled_at=user.last_polled_at,
        next_poll_at=user.next_poll_at,
        last_error=user.last_error,
        consecutive_failures=user.consecutive_failures,
        tweet_count=tweet_count,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


async def _get_user_or_404(db: DbSession, user_id: int) -> MonitoredUser:
    user = await db.get(MonitoredUser, user_id)
    if user is None:
        raise APIError(404, "monitored_user_not_found", "Monitored user was not found")
    return user


@router.get("", response_model=Page[MonitoredUserOut])
async def list_monitored_users(
    db: DbSession,
    _: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=64),
    is_active: bool | None = None,
) -> Page[MonitoredUserOut]:
    conditions = []
    if search:
        term = f"%{search.strip().lower()}%"
        conditions.append(
            or_(MonitoredUser.username.like(term), MonitoredUser.display_name.like(term))
        )
    if is_active is not None:
        conditions.append(MonitoredUser.is_active.is_(is_active))

    count_query = select(func.count(MonitoredUser.id)).where(*conditions)
    tweet_count = (
        select(func.count(Tweet.id))
        .where(Tweet.monitored_user_id == MonitoredUser.id)
        .correlate(MonitoredUser)
        .scalar_subquery()
    )
    query = (
        select(MonitoredUser, tweet_count.label("tweet_count"))
        .where(*conditions)
        .order_by(MonitoredUser.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    total = int(await db.scalar(count_query) or 0)
    rows = (await db.execute(query)).all()
    polling = await get_polling_settings(db, get_settings())
    return Page(
        items=[_serialize_user(user, int(count or 0), polling) for user, count in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=MonitoredUserOut, status_code=status.HTTP_201_CREATED)
async def create_monitored_user(
    payload: MonitoredUserCreate,
    db: DbSession,
    _: CurrentAdmin,
) -> MonitoredUserOut:
    now = datetime.now(UTC)
    user = MonitoredUser(
        username=payload.username,
        poll_interval_seconds=payload.poll_interval_seconds,
        include_replies=payload.include_replies,
        include_retweets=payload.include_retweets,
        status="queued",
        next_poll_at=now,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise APIError(409, "username_exists", "This X username is already monitored") from None
    await db.refresh(user)
    polling = await get_polling_settings(db, get_settings())
    return _serialize_user(user, 0, polling)


@router.get("/{user_id}", response_model=MonitoredUserOut)
async def get_monitored_user(
    user_id: int,
    db: DbSession,
    _: CurrentAdmin,
) -> MonitoredUserOut:
    user = await _get_user_or_404(db, user_id)
    count = int(
        await db.scalar(select(func.count(Tweet.id)).where(Tweet.monitored_user_id == user.id)) or 0
    )
    polling = await get_polling_settings(db, get_settings())
    return _serialize_user(user, count, polling)


@router.patch("/{user_id}", response_model=MonitoredUserOut)
async def update_monitored_user(
    user_id: int,
    payload: MonitoredUserUpdate,
    db: DbSession,
    _: CurrentAdmin,
) -> MonitoredUserOut:
    user = await _get_user_or_404(db, user_id)
    updates = payload.model_dump(exclude_unset=True)
    filters_changed = any(
        field in updates and updates[field] != getattr(user, field)
        for field in ("include_replies", "include_retweets")
    )
    for field, value in updates.items():
        setattr(user, field, value)
    if filters_changed:
        _clear_pagination_checkpoint(user)
        user.poll_generation += 1
        if user.is_active:
            user.status = "queued"
            user.next_poll_at = datetime.now(UTC)
    polling = await get_polling_settings(db, get_settings())
    if user.is_active and "poll_interval_seconds" in updates:
        interval = effective_interval(user.poll_interval_seconds, polling)
        proposed = datetime.now(UTC) + timedelta(seconds=interval)
        if user.next_poll_at is None or as_utc(user.next_poll_at) > proposed:
            user.next_poll_at = proposed
    await db.commit()
    await db.refresh(user)
    count = int(
        await db.scalar(select(func.count(Tweet.id)).where(Tweet.monitored_user_id == user.id)) or 0
    )
    return _serialize_user(user, count, polling)


@router.delete("/{user_id}", response_model=MessageResponse)
async def delete_monitored_user(
    user_id: int,
    db: DbSession,
    _: CurrentAdmin,
) -> MessageResponse:
    user = await _get_user_or_404(db, user_id)
    await db.delete(user)
    await db.commit()
    return MessageResponse(message=f"@{user.username} and its stored data were deleted")


@router.post("/{user_id}/pause", response_model=MonitoredUserOut)
async def pause_monitored_user(
    user_id: int,
    db: DbSession,
    _: CurrentAdmin,
) -> MonitoredUserOut:
    user = await _get_user_or_404(db, user_id)
    user.is_active = False
    user.status = "paused"
    user.next_poll_at = None
    user.manual_poll_token = None
    user.poll_generation += 1
    await db.commit()
    await db.refresh(user)
    count = int(
        await db.scalar(select(func.count(Tweet.id)).where(Tweet.monitored_user_id == user.id)) or 0
    )
    polling = await get_polling_settings(db, get_settings())
    return _serialize_user(user, count, polling)


@router.post("/{user_id}/resume", response_model=MonitoredUserOut)
async def resume_monitored_user(
    user_id: int,
    db: DbSession,
    _: CurrentAdmin,
) -> MonitoredUserOut:
    user = await _get_user_or_404(db, user_id)
    user.is_active = True
    user.status = "queued"
    user.last_error = None
    user.next_poll_at = datetime.now(UTC)
    user.poll_generation += 1
    await db.commit()
    await db.refresh(user)
    count = int(
        await db.scalar(select(func.count(Tweet.id)).where(Tweet.monitored_user_id == user.id)) or 0
    )
    polling = await get_polling_settings(db, get_settings())
    return _serialize_user(user, count, polling)


@router.post("/{user_id}/poll", response_model=AcceptedResponse, status_code=202)
async def poll_monitored_user_now(
    user_id: int,
    db: DbSession,
    _: CurrentAdmin,
) -> AcceptedResponse:
    user = await _get_user_or_404(db, user_id)
    if not user.is_active:
        raise APIError(409, "user_paused", "Resume this monitored user before polling")
    now = datetime.now(UTC)
    user.manual_poll_token = str(uuid.uuid4())
    user.next_poll_at = now
    user.status = "queued"
    await db.commit()
    return AcceptedResponse(message="Poll was scheduled", user_id=user.id, scheduled_at=now)
