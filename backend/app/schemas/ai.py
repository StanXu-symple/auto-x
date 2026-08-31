from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from app.schemas.common import APIModel

AIProvider = Literal["openai_responses", "codex_bridge"]
AIJobStatus = Literal["queued", "running", "retry_wait", "succeeded", "failed", "cancelled"]
AIDraftStatus = Literal["draft", "approved", "rejected"]
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]


def _validate_http_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("URL credentials are not allowed")
    return normalized


class AISettingsPatch(APIModel):
    enabled: bool | None = None
    auto_generate: bool | None = None
    provider: AIProvider | None = None
    model: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, max_length=500)
    bridge_url: str | None = Field(default=None, max_length=500)
    prompt_template: str | None = Field(default=None, max_length=20000)
    language: str | None = Field(default=None, min_length=1, max_length=32)
    tone: str | None = Field(default=None, min_length=1, max_length=64)
    require_review: bool | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    max_output_tokens: int | None = Field(default=None, ge=128, le=100000)
    request_timeout_seconds: int | None = Field(default=None, ge=5, le=600)
    reasoning_effort: ReasoningEffort | None = None
    default_skill_ids: list[int] | None = Field(default=None, max_length=20)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_http_url(value)

    @field_validator("bridge_url")
    @classmethod
    def validate_bridge_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return _validate_http_url(value)

    @field_validator("model", "language", "tone")
    @classmethod
    def normalize_short_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator(
        "enabled",
        "auto_generate",
        "provider",
        "model",
        "base_url",
        "language",
        "tone",
        "require_review",
        "max_attempts",
        "max_output_tokens",
        "request_timeout_seconds",
        "reasoning_effort",
        "default_skill_ids",
        mode="before",
    )
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("field cannot be null")
        return value

    @field_validator("default_skill_ids")
    @classmethod
    def unique_skill_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return value
        if any(skill_id <= 0 for skill_id in value):
            raise ValueError("skill ids must be positive")
        if len(value) != len(set(value)):
            raise ValueError("skill ids must be unique")
        return value


class AISettingsOut(APIModel):
    enabled: bool
    auto_generate: bool
    provider: AIProvider
    model: str
    base_url: str
    bridge_url: str | None
    prompt_template: str | None
    language: str
    tone: str
    require_review: bool
    max_attempts: int
    max_output_tokens: int
    request_timeout_seconds: int
    reasoning_effort: ReasoningEffort
    default_skill_ids: list[int]
    provider_ready: bool | None
    key_configured: bool | None
    key_status: Literal["configured", "missing", "not_required", "worker_managed", "unknown"]
    worker_status: str
    worker_last_heartbeat: datetime | None
    updated_at: datetime


class AISkillCreate(APIModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=4000)
    instructions: str = Field(min_length=1, max_length=20000)
    output_schema: dict[str, Any] | None = None
    is_active: bool = True
    remote_skill_id: str | None = Field(default=None, max_length=128)
    remote_skill_version: str | None = Field(default=None, max_length=64)

    @field_validator("name", "instructions")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("description", "remote_skill_id", "remote_skill_version")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("output_schema")
    @classmethod
    def validate_output_schema(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and len(json.dumps(value, ensure_ascii=False)) > 20000:
            raise ValueError("output_schema is too large")
        return value


class AISkillPatch(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=4000)
    instructions: str | None = Field(default=None, min_length=1, max_length=20000)
    output_schema: dict[str, Any] | None = None
    is_active: bool | None = None
    remote_skill_id: str | None = Field(default=None, max_length=128)
    remote_skill_version: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def require_change(self) -> AISkillPatch:
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        return self

    @field_validator("name", "instructions")
    @classmethod
    def strip_required_text(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("field cannot be null")
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("is_active", mode="before")
    @classmethod
    def reject_null_boolean(cls, value: object) -> object:
        if value is None:
            raise ValueError("field cannot be null")
        return value

    @field_validator("output_schema")
    @classmethod
    def validate_output_schema(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and len(json.dumps(value, ensure_ascii=False)) > 20000:
            raise ValueError("output_schema is too large")
        return value


class AISkillOut(APIModel):
    id: int
    name: str
    description: str | None
    instructions: str
    output_schema: dict[str, Any] | None
    is_active: bool
    version: int
    remote_skill_id: str | None
    remote_skill_version: str | None
    created_at: datetime
    updated_at: datetime


class AIDraftOut(APIModel):
    id: int
    job_id: int
    source_tweet_id: int
    title: str
    content: str
    excerpt: str | None
    status: AIDraftStatus
    metadata: dict[str, Any] | None
    revision: int
    created_at: datetime
    updated_at: datetime


class AIJobOut(APIModel):
    id: int
    source_tweet_id: int
    source_x_tweet_id: str | None = None
    source_text: str | None = None
    source_username: str | None = None
    skill_id: int | None
    skill_ids: list[int]
    skill_snapshot: list[dict[str, Any]]
    idempotency_key: str
    status: AIJobStatus
    provider: AIProvider
    model: str
    attempts: int
    max_attempts: int
    next_attempt_at: datetime
    manual: bool
    last_error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    draft: AIDraftOut | None = None


class AIJobDetail(AIJobOut):
    request_snapshot: dict[str, Any] | None
    response_snapshot: dict[str, Any] | None
    prompt_hash: str | None
    source_text_hash: str | None


class ManualGenerateRequest(APIModel):
    skill_ids: list[int] | None = Field(default=None, max_length=20)
    idempotency_key: str | None = Field(
        default=None, min_length=8, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$"
    )

    @field_validator("skill_ids")
    @classmethod
    def validate_skill_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return value
        if any(skill_id <= 0 for skill_id in value):
            raise ValueError("skill ids must be positive")
        if len(value) != len(set(value)):
            raise ValueError("skill ids must be unique")
        return value


class AIDraftPatch(APIModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = Field(default=None, min_length=1, max_length=50000)
    excerpt: str | None = Field(default=None, max_length=1000)
    status: AIDraftStatus | None = None
    metadata: dict[str, Any] | None = None
    revision: int = Field(ge=1)

    @model_validator(mode="after")
    def require_content_change(self) -> AIDraftPatch:
        if not (self.model_fields_set - {"revision"}):
            raise ValueError("at least one draft field must be provided")
        return self

    @field_validator("title", "content")
    @classmethod
    def reject_null_required_text(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("field cannot be null")
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class GeneratedDraft(APIModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=50000)
    excerpt: str | None = Field(default=None, max_length=1000)
    metadata: dict[str, Any] | None = None

    @field_validator("title", "content")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized
