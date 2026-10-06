from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from app.schemas.common import APIModel, Page

AIProvider = Literal["openai_responses", "codex_bridge"]
AIJobStatus = Literal["queued", "running", "retry_wait", "succeeded", "failed", "cancelled"]
AITriggerType = Literal["manual", "legacy_auto", "listen_task"]
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
    auto_trigger_mode: Literal["legacy_all", "listening_tasks"] | None = None
    provider: AIProvider | None = None
    model: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, max_length=500)
    bridge_url: str | None = Field(default=None, max_length=500)
    prompt_template: str | None = Field(default=None, max_length=20000)
    language: str | None = Field(default=None, min_length=1, max_length=32)
    tone: str | None = Field(default=None, min_length=1, max_length=64)
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
        "auto_trigger_mode",
        "provider",
        "model",
        "base_url",
        "language",
        "tone",
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
    auto_trigger_mode: Literal["legacy_all", "listening_tasks"]
    provider: AIProvider
    model: str
    base_url: str
    bridge_url: str | None
    prompt_template: str | None
    language: str
    tone: str
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


class AIFeatureOut(APIModel):
    id: int
    code: str
    name: str
    description: str | None
    base_prompt: str
    is_active: bool


class AIUserSkillBindingReplace(APIModel):
    skill_ids: list[int] = Field(max_length=20)

    @field_validator("skill_ids")
    @classmethod
    def validate_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value) or len(value) != len(set(value)):
            raise ValueError("skill ids must be unique positive integers")
        return value


class AIUserSkillBindingOut(APIModel):
    monitored_user_id: int
    username: str
    feature: AIFeatureOut
    skill_ids: list[int]
    skills: list[AISkillOut]
    resolution_source: str


class AIUserProfileOut(APIModel):
    monitored_user_id: int
    username: str
    identity_summary: str
    focus_summary: str
    relationship_summary: str
    recurring_topics: list[str]
    evidence: list[dict[str, Any]]
    confidence: float
    version: int
    last_source_tweet_id: int | None
    updated_at: datetime | None


class AIDraftOut(APIModel):
    id: int
    job_id: int | None
    source_tweet_id: int | None
    article_source: Literal["ai", "user"]
    title: str
    content: str
    excerpt: str | None
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
    feature_code: str
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
    listen_task_id: int | None = None
    trigger_type: AITriggerType = "legacy_auto"
    task_config_version: int | None = None
    lifetime_attempts: int = 0
    is_archived: bool = False
    last_error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    draft: AIDraftOut | None = None


class AIJobDetail(AIJobOut):
    task_snapshot: dict[str, Any] | None = None
    request_snapshot: dict[str, Any] | None
    response_snapshot: dict[str, Any] | None
    prompt_hash: str | None
    source_text_hash: str | None


class AIListenTaskCreate(APIModel):
    name: str = Field(min_length=1, max_length=100)
    desired_state: Literal["enabled", "paused"] = "enabled"
    all_monitored_users: bool = False
    monitored_user_ids: list[int] = Field(default_factory=list, max_length=500)
    listen_mode: Literal["all", "original", "reply", "retweet"] = "original"
    skill_ids: list[int] = Field(min_length=1, max_length=20)
    initial_sync_days: int = Field(default=0, ge=0, le=365)
    max_attempts_override: int | None = Field(default=None, ge=1, le=10)
    language_override: str | None = Field(default=None, min_length=1, max_length=32)
    tone_override: str | None = Field(default=None, min_length=1, max_length=64)
    max_output_tokens_override: int | None = Field(default=None, ge=128, le=100000)
    switch_from_legacy: bool = False

    @model_validator(mode="after")
    def validate_selection(self) -> AIListenTaskCreate:
        if not self.all_monitored_users and not self.monitored_user_ids:
            raise ValueError("choose at least one monitored user")
        if len(self.monitored_user_ids) != len(set(self.monitored_user_ids)):
            raise ValueError("monitored user ids must be unique")
        if any(item <= 0 for item in self.monitored_user_ids):
            raise ValueError("monitored user ids must be positive")
        if len(self.skill_ids) != len(set(self.skill_ids)) or any(
            item <= 0 for item in self.skill_ids
        ):
            raise ValueError("skill ids must be unique positive integers")
        return self

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class AIListenTaskPatch(APIModel):
    config_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    all_monitored_users: bool | None = None
    monitored_user_ids: list[int] | None = Field(default=None, max_length=500)
    listen_mode: Literal["all", "original", "reply", "retweet"] | None = None
    skill_ids: list[int] | None = Field(default=None, min_length=1, max_length=20)
    max_attempts_override: int | None = Field(default=None, ge=1, le=10)
    language_override: str | None = Field(default=None, min_length=1, max_length=32)
    tone_override: str | None = Field(default=None, min_length=1, max_length=64)
    max_output_tokens_override: int | None = Field(default=None, ge=128, le=100000)

    @model_validator(mode="after")
    def validate_patch(self) -> AIListenTaskPatch:
        if len(self.model_fields_set) <= 1:
            raise ValueError("at least one task field must be provided")
        if self.monitored_user_ids is not None:
            if len(self.monitored_user_ids) != len(set(self.monitored_user_ids)) or any(
                item <= 0 for item in self.monitored_user_ids
            ):
                raise ValueError("monitored user ids must be unique positive integers")
        if self.skill_ids is not None:
            if len(self.skill_ids) != len(set(self.skill_ids)) or any(
                item <= 0 for item in self.skill_ids
            ):
                raise ValueError("skill ids must be unique positive integers")
        for required in (
            "name",
            "all_monitored_users",
            "monitored_user_ids",
            "listen_mode",
            "skill_ids",
        ):
            if required in self.model_fields_set and getattr(self, required) is None:
                raise ValueError(f"{required} cannot be null")
        if self.name is not None:
            self.name = self.name.strip()
            if not self.name:
                raise ValueError("name must not be blank")
        return self


class AIListenTaskPreview(AIListenTaskCreate):
    task_id: int | None = Field(default=None, ge=1)
    from_at: datetime | None = None
    to_at: datetime | None = None
    exclude_legacy_generated: bool = True

    @model_validator(mode="after")
    def validate_window(self) -> AIListenTaskPreview:
        if (self.from_at is None) != (self.to_at is None):
            raise ValueError("from_at and to_at must be supplied together")
        if self.from_at and (self.from_at.tzinfo is None or self.to_at.tzinfo is None):
            raise ValueError("history timestamps must include a timezone")
        if self.from_at and self.to_at and self.from_at >= self.to_at:
            raise ValueError("from_at must precede to_at")
        return self


class AIListenTaskStats(APIModel):
    matched: int = 0
    queued: int = 0
    running: int = 0
    retry_wait: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0
    lifetime_attempts: int = 0


class AIListenTaskCondition(APIModel):
    status: str
    reasons: list[str] = Field(default_factory=list)


class AIListenTaskAccountOut(APIModel):
    id: int
    username: str
    is_active: bool
    include_replies: bool
    include_retweets: bool
    last_polled_at: datetime | None
    archived_at: datetime | None = None


class AIListenTaskSkillOut(APIModel):
    id: int
    name: str
    is_active: bool
    version: int
    priority: int


class AIListenTaskOut(APIModel):
    id: int
    name: str
    desired_state: Literal["enabled", "paused", "archived"]
    queue_hold_reason: str | None
    all_monitored_users: bool
    monitored_user_ids: list[int]
    accounts: list[AIListenTaskAccountOut]
    listen_mode: Literal["all", "original", "reply", "retweet"]
    skill_ids: list[int]
    skills: list[AIListenTaskSkillOut]
    feature_code: str
    config_version: int
    activated_at: datetime | None
    effective_from: datetime | None
    initial_sync_days: int
    initial_backfill_from: datetime | None
    initial_backfill_to: datetime | None
    archived_at: datetime | None
    max_attempts_override: int | None
    language_override: str | None
    tone_override: str | None
    max_output_tokens_override: int | None
    health: AIListenTaskCondition
    dependency: AIListenTaskCondition
    stats: AIListenTaskStats
    last_matched_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_ai_started_at: datetime | None
    data_source_name: str | None
    data_source_model: str | None
    data_source_verified_at: datetime | None
    data_source_verification_status: str | None
    created_at: datetime
    updated_at: datetime


class AIListenTaskPage(Page[AIListenTaskOut]):
    summary: AIListenTaskStats


class AIListenTaskPreviewOut(APIModel):
    matched: int
    duplicates: int
    legacy_generated: int
    pending: int
    unavailable_accounts: list[dict[str, Any]]
    from_at: datetime | None
    to_at: datetime | None


class AIListenTaskBackfillCreate(APIModel):
    from_at: datetime
    to_at: datetime
    request_id: str | None = Field(
        default=None, min_length=8, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    exclude_legacy_generated: bool = True

    @model_validator(mode="after")
    def validate_window(self) -> AIListenTaskBackfillCreate:
        if self.from_at.tzinfo is None or self.to_at.tzinfo is None:
            raise ValueError("history timestamps must include a timezone")
        if self.from_at >= self.to_at:
            raise ValueError("from_at must precede to_at")
        return self


class AIListenTaskBackfillOut(APIModel):
    id: int
    task_id: int
    request_id: str
    from_at: datetime
    to_at: datetime
    status: str
    scanned_count: int
    enqueued_count: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class AIListenTaskEventOut(APIModel):
    id: int
    task_id: int
    event_type: str
    job_id: int | None
    backfill_id: int | None
    summary: str
    details: dict[str, Any] | None
    created_at: datetime


class AIListenTaskQueueDecision(APIModel):
    decision: Literal["continue_old_snapshot", "cancel_old_queue"]


class AIGenerationAttemptOut(APIModel):
    id: int
    job_id: int
    lifetime_number: int
    round_number: int
    attempt_number: int
    started_at: datetime
    ended_at: datetime | None
    status: str
    error_type: str | None
    error_summary: str | None
    duration_ms: int | None
    provider: str | None
    model_name: str | None
    data_source_name: str | None
    data_source_version: int | None


class ManualGenerateRequest(APIModel):
    feature_code: str = Field(
        default="article_generation", min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"
    )
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


class GeneratedAuthorProfile(APIModel):
    identity_summary: str = Field(max_length=4000)
    focus_summary: str = Field(max_length=4000)
    relationship_summary: str = Field(max_length=4000)
    recurring_topics: list[str] = Field(max_length=30)
    evidence: list[dict[str, Any]] = Field(max_length=50)
    confidence: float = Field(ge=0, le=1)


class GeneratedDraft(APIModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=50000)
    excerpt: str | None = Field(default=None, max_length=1000)
    metadata: dict[str, Any] | None = None
    author_profile: GeneratedAuthorProfile

    @field_validator("title", "content")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized
