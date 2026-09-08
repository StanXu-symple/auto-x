from datetime import UTC, datetime, timedelta, timezone

from app.core.time import as_utc, to_database_utc


def test_postgres_datetime_filter_normalizes_offset_and_preserves_timezone() -> None:
    source = datetime(2026, 8, 31, 16, 0, tzinfo=timezone(timedelta(hours=8)))
    result = to_database_utc(source)
    assert result == datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
    assert result.tzinfo == UTC


def test_naive_database_datetime_is_interpreted_as_utc() -> None:
    source = datetime(2026, 8, 31, 8, 0)
    assert as_utc(source) == datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
