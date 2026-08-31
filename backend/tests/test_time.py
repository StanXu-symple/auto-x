from datetime import UTC, datetime, timedelta, timezone

from app.core.time import as_utc, to_mysql_utc_naive


def test_mysql_datetime_filter_normalizes_offset_to_utc_naive() -> None:
    source = datetime(2026, 8, 31, 16, 0, tzinfo=timezone(timedelta(hours=8)))
    assert to_mysql_utc_naive(source) == datetime(2026, 8, 31, 8, 0)


def test_naive_database_datetime_is_interpreted_as_utc() -> None:
    source = datetime(2026, 8, 31, 8, 0)
    assert as_utc(source) == datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
