from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentAdmin, DbSession
from app.core.config import get_settings
from app.core.time import as_utc
from app.models.monitored_user import MonitoredUser
from app.models.setting import AppSetting
from app.schemas.polling import (
    PollingSettingsOut,
    PollingSettingsPatch,
    PollingSettingsUpdate,
)
from app.services.settings_service import POLLING_KEY, get_polling_settings

router = APIRouter(prefix="/settings", tags=["Settings"])


@router.get("", response_model=PollingSettingsOut)
async def read_settings(db: DbSession, _: CurrentAdmin) -> PollingSettingsOut:
    values = await get_polling_settings(db, get_settings())
    row = await db.get(AppSetting, POLLING_KEY)
    return PollingSettingsOut(**values, updated_at=row.updated_at if row else None)


async def _persist_settings(
    payload: PollingSettingsUpdate, db: DbSession, *, reschedule_global: bool
) -> PollingSettingsOut:
    row = await db.get(AppSetting, POLLING_KEY)
    if row is None:
        row = AppSetting(key=POLLING_KEY, value=payload.model_dump())
        db.add(row)
    else:
        row.value = payload.model_dump()

    if reschedule_global:
        # Let a shorter global interval take effect without waiting for the old deadline.
        proposed = datetime.now(UTC) + timedelta(seconds=payload.global_poll_interval_seconds)
        users = (
            await db.scalars(
                select(MonitoredUser).where(
                    MonitoredUser.is_active.is_(True),
                    MonitoredUser.poll_interval_seconds.is_(None),
                )
            )
        ).all()
        for user in users:
            if user.next_poll_at is None or as_utc(user.next_poll_at) > proposed:
                user.next_poll_at = proposed
    await db.commit()
    await db.refresh(row)
    return PollingSettingsOut(**payload.model_dump(), updated_at=row.updated_at)


@router.put("", response_model=PollingSettingsOut)
async def replace_settings(
    payload: PollingSettingsUpdate,
    db: DbSession,
    _: CurrentAdmin,
) -> PollingSettingsOut:
    return await _persist_settings(payload, db, reschedule_global=True)


@router.patch("", response_model=PollingSettingsOut)
async def patch_settings(
    payload: PollingSettingsPatch,
    db: DbSession,
    _: CurrentAdmin,
) -> PollingSettingsOut:
    current = await get_polling_settings(db, get_settings())
    changes = payload.model_dump(exclude_unset=True)
    merged = PollingSettingsUpdate(**{**current, **changes})
    return await _persist_settings(
        merged,
        db,
        reschedule_global="global_poll_interval_seconds" in changes,
    )
