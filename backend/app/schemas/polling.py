from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.common import APIModel


class PollingLogOut(APIModel):
    id: int
    monitored_user_id: int
    username: str
    trigger: str
    status: str
    worker_id: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    tweets_fetched: int
    tweets_inserted: int
    http_status: int | None
    error_message: str | None
    rate_limit_reset_at: datetime | None


class PollingSettingsUpdate(APIModel):
    global_poll_interval_seconds: int = Field(ge=15, le=86400)
    max_concurrency: int = Field(ge=1, le=100)


class PollingSettingsPatch(APIModel):
    global_poll_interval_seconds: int | None = Field(default=None, ge=15, le=86400)
    max_concurrency: int | None = Field(default=None, ge=1, le=100)

    @field_validator("global_poll_interval_seconds", "max_concurrency", mode="before")
    @classmethod
    def reject_null_values(cls, value: object) -> object:
        if value is None:
            raise ValueError("settings values cannot be null")
        return value


class PollingSettingsOut(PollingSettingsUpdate):
    updated_at: datetime | None = None
