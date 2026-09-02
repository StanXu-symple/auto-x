from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import APIModel

XAcquisitionMethod = Literal["developer_console", "api_exchange"]


class XCredentialSave(APIModel):
    bearer_token: str = Field(min_length=20, max_length=2048)
    acquisition_method: XAcquisitionMethod

    @field_validator("bearer_token")
    @classmethod
    def clean_token(cls, value: str) -> str:
        value = value.strip()
        if value.lower().startswith("bearer "):
            value = value[7:].strip()
        if not value or any(character.isspace() for character in value):
            raise ValueError("Bearer Token cannot contain whitespace")
        return value


class XCredentialStatus(APIModel):
    configured: bool
    token_hint: str | None = None
    acquisition_method: XAcquisitionMethod | None = None
    verification_status: Literal["unverified", "valid", "invalid", "error"] | None = None
    last_verified_at: datetime | None = None
    last_error: str | None = None
    updated_at: datetime | None = None
    version: int | None = None
    cache_active: bool = False
    cache_ttl_seconds: int | None = None


class XCredentialTestResult(APIModel):
    valid: bool
    verification_status: Literal["valid", "invalid", "error"]
    message: str
    checked_at: datetime
