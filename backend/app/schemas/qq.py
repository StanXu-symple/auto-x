from __future__ import annotations

from datetime import datetime
from string import Formatter
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import APIModel

DEFAULT_QQ_MESSAGE_TEMPLATE = "{title}\n@{username} · {posted_at}\n{text}\n{url}"
ALLOWED_TEMPLATE_FIELDS = {"title", "author", "username", "text", "url", "posted_at"}


def validate_message_template(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Message template cannot be empty")
    fields = {field for _, field, _, _ in Formatter().parse(value) if field}
    unknown = fields - ALLOWED_TEMPLATE_FIELDS
    if unknown:
        raise ValueError(f"Unsupported template fields: {', '.join(sorted(unknown))}")
    return value


class QQBotCreate(APIModel):
    name: str = Field(min_length=1, max_length=100)
    app_id: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    app_secret: str = Field(min_length=8, max_length=500)
    is_enabled: bool = True


class QQBotUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    app_id: str | None = Field(
        default=None, min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"
    )
    app_secret: str | None = Field(default=None, min_length=8, max_length=500)
    is_enabled: bool | None = None


class QQBotOut(APIModel):
    id: int
    name: str
    app_id: str
    secret_hint: str
    is_enabled: bool
    verification_status: str
    last_verified_at: datetime | None
    last_error: str | None
    version: int
    target_count: int = 0
    online_status: Literal["online", "connecting", "offline", "disabled"] = "offline"
    created_at: datetime
    updated_at: datetime


class QQBotTestResult(APIModel):
    valid: bool
    verification_status: Literal["valid", "invalid", "error"]
    message: str
    checked_at: datetime


class QQJoinedGroupOut(APIModel):
    group_openid: str
    name: str | None = None
    target_id: int | None = None
    last_event_at: datetime


class QQTargetCreate(APIModel):
    bot_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=100)
    group_openid: str = Field(min_length=3, max_length=128)
    is_enabled: bool = True
    all_monitored_users: bool = False
    monitored_user_ids: list[int] = Field(default_factory=list, max_length=1000)
    message_template: str = Field(default=DEFAULT_QQ_MESSAGE_TEMPLATE, max_length=2000)

    @field_validator("message_template")
    @classmethod
    def validate_template(cls, value: str) -> str:
        return validate_message_template(value)

    @field_validator("monitored_user_ids")
    @classmethod
    def unique_users(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("Monitored user ids must be positive")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_scope(self) -> QQTargetCreate:
        if not self.all_monitored_users and not self.monitored_user_ids:
            raise ValueError("Select at least one monitored account")
        return self


class QQTargetUpdate(APIModel):
    bot_id: int | None = Field(default=None, gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    group_openid: str | None = Field(default=None, min_length=3, max_length=128)
    is_enabled: bool | None = None
    all_monitored_users: bool | None = None
    monitored_user_ids: list[int] | None = Field(default=None, max_length=1000)
    message_template: str | None = Field(default=None, max_length=2000)

    @field_validator("message_template")
    @classmethod
    def validate_template(cls, value: str | None) -> str | None:
        return validate_message_template(value) if value is not None else None

    @field_validator("monitored_user_ids")
    @classmethod
    def unique_users(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if any(item <= 0 for item in value):
            raise ValueError("Monitored user ids must be positive")
        return list(dict.fromkeys(value))


class QQTargetOut(APIModel):
    id: int
    bot_id: int
    bot_name: str
    name: str
    group_openid: str
    is_enabled: bool
    all_monitored_users: bool
    monitored_user_ids: list[int]
    message_template: str
    created_at: datetime
    updated_at: datetime


class QQDeliveryOut(APIModel):
    id: int
    task_id: int | None = None
    target_id: int | None
    source_tweet_id: int | None
    kind: str
    bot_name: str
    bot_app_id: str
    target_name: str
    group_openid: str
    message_body: str
    status: str
    attempts: int
    max_attempts: int
    next_attempt_at: datetime
    provider_message_id: str | None
    last_error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class QQOverview(APIModel):
    total_bots: int
    enabled_bots: int
    enabled_targets: int
    queued_deliveries: int
    failed_deliveries: int
    worker_status: str
    worker_last_heartbeat: datetime | None = None


class QQDeliveryAccepted(APIModel):
    message: str
    delivery_id: int


class QQBatchPushCreate(APIModel):
    bot_id: int = Field(gt=0)
    group_openids: list[str] = Field(min_length=1, max_length=100)
    tweet_ids: list[int] = Field(min_length=1, max_length=100)

    @field_validator("group_openids")
    @classmethod
    def unique_groups(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("请选择至少一个群")
        return list(dict.fromkeys(cleaned))

    @field_validator("tweet_ids")
    @classmethod
    def unique_tweets(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class QQBatchPushAccepted(APIModel):
    message: str
    delivery_ids: list[int]
    batch_count: int

class QQScheduledTaskCreate(APIModel):
    name: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2000)
    frequency: Literal["secondly", "minutely", "hourly", "daily", "weekly", "monthly"] = "daily"
    interval_value: int = Field(default=1, ge=1, le=365)
    run_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$")
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    month_day: int | None = Field(default=None, ge=1, le=31)
    is_enabled: bool = True
    send_immediately: bool = False
    bot_ids: list[int] = Field(min_length=1, max_length=50)
    groups: list[dict[str, int | str]] = Field(min_length=1, max_length=100)

class QQScheduledTaskOut(QQScheduledTaskCreate):
    id: int
    last_run_at: datetime | None
    next_run_at: datetime
    created_at: datetime
    updated_at: datetime
