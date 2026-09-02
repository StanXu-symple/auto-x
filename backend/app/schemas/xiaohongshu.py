from __future__ import annotations

from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from app.schemas.common import APIModel

XhsPublishStrategy = Literal["manual", "automatic", "delayed"]
XhsPublishStatus = Literal[
    "draft", "queued", "publishing", "retry_wait", "published", "failed", "cancelled"
]
XhsVisibility = Literal["公开可见", "仅自己可见", "仅互关好友可见"]


def _http_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("URL credentials are not allowed")
    hostname = parsed.hostname or ""
    local_http = hostname in {"localhost", "host.docker.internal"} or "." not in hostname
    try:
        address = ip_address(hostname)
        local_http = local_http or address.is_private or address.is_loopback
    except ValueError:
        pass
    if parsed.scheme == "http" and not local_http:
        raise ValueError("public MCP services must use HTTPS")
    return normalized


class XiaohongshuConnectionSave(APIModel):
    name: str = Field(default="小红书 MCP", min_length=1, max_length=100)
    connector: Literal["xiaohongshu_mcp"] = "xiaohongshu_mcp"
    mcp_url: str = Field(max_length=500)
    auth_token: str | None = Field(default=None, max_length=4096)
    risk_acknowledged: bool

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("mcp_url")
    @classmethod
    def validate_mcp_url(cls, value: str) -> str:
        normalized = _http_url(value)
        return normalized if normalized.endswith("/mcp") else normalized + "/mcp"

    @field_validator("auth_token")
    @classmethod
    def normalize_token(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    @model_validator(mode="after")
    def require_risk_acknowledgement(self) -> XiaohongshuConnectionSave:
        if not self.risk_acknowledged:
            raise ValueError("must acknowledge the unofficial integration risk")
        return self


class XiaohongshuConnectionStatus(APIModel):
    configured: bool
    name: str | None = None
    connector: str | None = None
    mcp_url: str | None = None
    token_configured: bool = False
    token_hint: str | None = None
    verification_status: str | None = None
    login_status: str = "unknown"
    risk_acknowledged: bool = False
    last_verified_at: datetime | None = None
    last_error: str | None = None
    version: int | None = None
    cache_active: bool = False
    cache_ttl_seconds: int | None = None
    updated_at: datetime | None = None


class XiaohongshuConnectionTestResult(APIModel):
    valid: bool
    logged_in: bool
    verification_status: str
    login_status: str
    message: str
    checked_at: datetime


class XiaohongshuLoginQr(APIModel):
    image_data: str | None = None
    mime_type: str = "image/png"
    message: str


class XiaohongshuPublishSettingsPatch(APIModel):
    enabled: bool
    default_strategy: XhsPublishStrategy
    default_delay_minutes: int = Field(ge=1, le=20160)
    max_attempts: int = Field(ge=1, le=10)
    daily_publish_limit: int = Field(ge=1, le=50)
    default_visibility: XhsVisibility
    declare_original: bool


class XiaohongshuPublishSettingsOut(XiaohongshuPublishSettingsPatch):
    worker_status: str = "unknown"
    worker_last_heartbeat: datetime | None = None
    updated_at: datetime


class XiaohongshuPublishJobCreate(APIModel):
    source_ai_draft_id: int | None = Field(default=None, ge=1)
    title: str = Field(min_length=1, max_length=20)
    content: str = Field(min_length=1, max_length=1000)
    images: list[str] = Field(min_length=1, max_length=18)
    tags: list[str] = Field(default_factory=list, max_length=20)
    products: list[str] = Field(default_factory=list, max_length=20)
    visibility: XhsVisibility | None = None
    is_original: bool | None = None
    strategy: XhsPublishStrategy | None = None
    scheduled_at: datetime | None = None

    @field_validator("title", "content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("images")
    @classmethod
    def validate_images(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw in values:
            value = raw.strip()
            if not value:
                continue
            parsed = urlsplit(value)
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                if parsed.username or parsed.password:
                    raise ValueError("image URL credentials are not allowed")
            elif not Path(value).is_absolute():
                raise ValueError("images must be HTTP(S) URLs or server absolute paths")
            normalized.append(value)
        if not normalized:
            raise ValueError("at least one image is required")
        return list(dict.fromkeys(normalized))

    @field_validator("tags", "products")
    @classmethod
    def normalize_terms(cls, values: list[str]) -> list[str]:
        normalized = [item.strip().lstrip("#") for item in values if item.strip()]
        if any(len(item) > 50 for item in normalized):
            raise ValueError("tag or product is too long")
        return list(dict.fromkeys(normalized))

    @field_validator("scheduled_at")
    @classmethod
    def normalize_schedule(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("scheduled_at must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_delayed_schedule(self) -> XiaohongshuPublishJobCreate:
        if self.strategy == "delayed" and self.scheduled_at is not None:
            if self.scheduled_at <= datetime.now(UTC):
                raise ValueError("scheduled_at must be in the future")
        return self


class XiaohongshuPublishJobOut(APIModel):
    id: int
    source_ai_draft_id: int | None
    title: str
    content: str
    images: list[str]
    tags: list[str]
    products: list[str]
    visibility: XhsVisibility
    is_original: bool
    strategy: XhsPublishStrategy
    status: XhsPublishStatus
    scheduled_at: datetime | None
    attempts: int
    max_attempts: int
    last_error: str | None
    platform_note_id: str | None
    platform_url: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class XiaohongshuScheduleRequest(APIModel):
    scheduled_at: datetime

    @field_validator("scheduled_at")
    @classmethod
    def validate_schedule(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("scheduled_at must include a timezone")
        normalized = value.astimezone(UTC)
        if normalized <= datetime.now(UTC):
            raise ValueError("scheduled_at must be in the future")
        return normalized
