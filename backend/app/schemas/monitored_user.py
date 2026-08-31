import re
from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.common import APIModel


def normalize_x_username(value: str) -> str:
    normalized = value.strip().lstrip("@").lower()
    if re.fullmatch(r"[A-Za-z0-9_]{1,15}", normalized, flags=re.ASCII) is None:
        raise ValueError("username may contain only letters, digits, and underscores")
    return normalized


class MonitoredUserCreate(APIModel):
    username: str
    poll_interval_seconds: int | None = Field(default=None, ge=15, le=86400)
    include_replies: bool = True
    include_retweets: bool = True

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return normalize_x_username(value)


class MonitoredUserUpdate(APIModel):
    poll_interval_seconds: int | None = Field(default=None, ge=15, le=86400)
    include_replies: bool | None = None
    include_retweets: bool | None = None

    @field_validator("include_replies", "include_retweets", mode="before")
    @classmethod
    def reject_null_filters(cls, value: object) -> object:
        if value is None:
            raise ValueError("filter flags cannot be null")
        return value


class MonitoredUserOut(APIModel):
    id: int
    username: str
    x_user_id: str | None
    display_name: str | None
    is_active: bool
    include_replies: bool
    include_retweets: bool
    poll_interval_seconds: int | None
    effective_poll_interval_seconds: int
    status: str
    last_tweet_id: str | None
    pagination_in_progress: bool = False
    manual_poll_pending: bool = False
    poll_generation: int = 0
    last_polled_at: datetime | None
    next_poll_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    tweet_count: int = 0
    created_at: datetime
    updated_at: datetime
