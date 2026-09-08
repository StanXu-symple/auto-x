from datetime import UTC, datetime


def as_utc(value: datetime) -> datetime:
    """Interpret naive database datetimes as UTC and normalize aware values to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_database_utc(value: datetime) -> datetime:
    """Normalize database filter values to UTC while preserving naive compatibility."""
    return as_utc(value)
