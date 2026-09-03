from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.time import as_utc


def next_qq_task_run(
    *,
    frequency: str,
    interval_value: int,
    run_time: str,
    weekdays: str | list[int],
    month_day: int | None,
    now: datetime,
    timezone_name: str,
) -> datetime:
    """Return the next run in UTC while interpreting wall-clock fields locally."""
    local_now = as_utc(now).astimezone(ZoneInfo(timezone_name))
    parts = [int(part) for part in run_time.split(":")]
    hour, minute, second = (*parts, 0)[:3]

    if frequency == "secondly":
        return (local_now + timedelta(seconds=interval_value)).astimezone(UTC)

    if frequency == "minutely":
        candidate = local_now.replace(second=second, microsecond=0)
        if candidate <= local_now:
            candidate += timedelta(minutes=interval_value)
        return candidate.astimezone(UTC)

    if frequency == "hourly":
        candidate = local_now.replace(minute=minute, second=second, microsecond=0)
        if candidate <= local_now:
            candidate += timedelta(hours=interval_value)
        return candidate.astimezone(UTC)

    candidate = local_now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if frequency == "daily":
        if candidate <= local_now:
            candidate += timedelta(days=1)
        return candidate.astimezone(UTC)

    if frequency == "weekly":
        selected_days = (
            {int(item) for item in weekdays.split(",") if item}
            if isinstance(weekdays, str)
            else set(weekdays)
        )
        selected_days = selected_days or {candidate.isoweekday()}
        for offset in range(8):
            value = candidate + timedelta(days=offset)
            if value > local_now and value.isoweekday() in selected_days:
                return value.astimezone(UTC)

    if frequency == "monthly":
        for offset in range(13):
            month_index = local_now.month - 1 + offset
            year = local_now.year + month_index // 12
            month = month_index % 12 + 1
            day = min(month_day or 1, calendar.monthrange(year, month)[1])
            value = candidate.replace(year=year, month=month, day=day)
            if value > local_now:
                return value.astimezone(UTC)

    raise ValueError(f"Unsupported QQ task frequency: {frequency}")
