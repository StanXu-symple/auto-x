from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import APIModel
from app.schemas.x_credential import XCredentialStatus

XSourceProvider = Literal["official_api", "twscrape"]


class XSourceProviderUpdate(APIModel):
    provider: XSourceProvider


class TwscrapeCredentialSave(APIModel):
    account_label: str = Field(min_length=1, max_length=64)
    auth_token: str = Field(min_length=10, max_length=2048)
    ct0: str = Field(min_length=10, max_length=2048)
    acknowledged_risk: bool

    @field_validator("account_label", "auth_token", "ct0")
    @classmethod
    def strip_values(cls, value: str) -> str:
        return value.strip()

    @field_validator("auth_token", "ct0")
    @classmethod
    def reject_whitespace(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError("Cookie values cannot contain whitespace")
        return value

    @model_validator(mode="after")
    def require_risk_acknowledgement(self) -> "TwscrapeCredentialSave":
        if not self.acknowledged_risk:
            raise ValueError("You must acknowledge the twscrape account and policy risks")
        return self


class TwscrapeCredentialStatus(APIModel):
    configured: bool
    account_hint: str | None = None
    verification_status: Literal["unverified", "valid", "invalid", "error"] | None = None
    last_verified_at: datetime | None = None
    last_error: str | None = None
    updated_at: datetime | None = None
    version: int | None = None
    cache_active: bool = False
    cache_ttl_seconds: int | None = None


class XSourceStatus(APIModel):
    active_provider: XSourceProvider
    official_api: XCredentialStatus
    twscrape: TwscrapeCredentialStatus
    updated_at: datetime | None = None


class XSourceTestResult(APIModel):
    provider: XSourceProvider
    valid: bool
    verification_status: Literal["valid", "invalid", "error"]
    message: str
    checked_at: datetime
