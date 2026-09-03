from datetime import UTC, datetime

from app.services.qq_schedule import next_qq_task_run


def next_run(now: datetime, **overrides) -> datetime:
    values = {
        "frequency": "hourly",
        "interval_value": 1,
        "run_time": "20:40:00",
        "weekdays": [],
        "month_day": None,
        "now": now,
        "timezone_name": "Asia/Shanghai",
    }
    values.update(overrides)
    return next_qq_task_run(**values)


def test_hourly_task_uses_minute_and_second_in_current_hour() -> None:
    # 12:20 UTC is 20:20 in Asia/Shanghai, so the next run is 20:40 local.
    assert next_run(datetime(2026, 9, 3, 12, 20, tzinfo=UTC)) == datetime(
        2026, 9, 3, 12, 40, tzinfo=UTC
    )


def test_hourly_task_rolls_to_next_hour_after_target_minute() -> None:
    assert next_run(datetime(2026, 9, 3, 12, 45, tzinfo=UTC)) == datetime(
        2026, 9, 3, 13, 40, tzinfo=UTC
    )


def test_daily_task_interprets_run_time_in_application_timezone() -> None:
    assert next_run(
        datetime(2026, 9, 3, 12, 45, tzinfo=UTC),
        frequency="daily",
    ) == datetime(2026, 9, 4, 12, 40, tzinfo=UTC)


def test_monthly_task_can_run_later_in_current_month() -> None:
    assert next_run(
        datetime(2026, 9, 3, 12, 45, tzinfo=UTC),
        frequency="monthly",
        month_day=20,
    ) == datetime(2026, 9, 20, 12, 40, tzinfo=UTC)
