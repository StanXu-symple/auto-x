from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from app.schemas.common import APIModel


class AIDataSourceSave(APIModel):
    name: str = Field(min_length=1, max_length=100)
    protocol: Literal["openai_responses"] = "openai_responses"
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=128)
    api_key: str | None = Field(default=None, min_length=8, max_length=4096)

    @field_validator("name", "model")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("api_key")
    @classmethod
    def normalize_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if any(character.isspace() for character in normalized):
            raise ValueError("API Key cannot contain whitespace")
        return normalized

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("URL credentials, query, and fragment are not allowed")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("API Key endpoints must use HTTPS except on localhost")
        return normalized


class AIDataSourceStatus(APIModel):
    configured: bool
    name: str | None = None
    protocol: Literal["openai_responses"] = "openai_responses"
    base_url: str | None = None
    model: str | None = None
    key_hint: str | None = None
    verification_status: Literal["unverified", "valid", "invalid", "error"] | None = None
    last_verified_at: datetime | None = None
    last_error: str | None = None
    version: int | None = None
    cache_active: bool = False
    cache_ttl_seconds: int | None = None
    updated_at: datetime | None = None


class AIDataSourceTestResult(APIModel):
    valid: bool
    verification_status: Literal["valid", "invalid", "error"]
    message: str
    models: list[str]
    checked_at: datetime


class AIModelList(APIModel):
    models: list[str]
