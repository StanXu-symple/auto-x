from datetime import UTC, datetime


def as_utc(value: datetime) -> datetime:
    """Interpret naive database datetimes as UTC and normalize aware values to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_mysql_utc_naive(value: datetime) -> datetime:
    """MySQL 5.7 DATETIME has no offset; bind a UTC-normalized naive value."""
    return as_utc(value).replace(tzinfo=None)
