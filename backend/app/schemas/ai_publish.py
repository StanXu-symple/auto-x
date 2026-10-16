"""Read models for independent automatic article publishing outcomes."""

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from app.schemas.common import APIModel

AutoPublishStatus = Literal[
    "pending", "retry_wait", "dispatching", "accepted", "published", "failed", "uncertain"
]
PublishReconciliationResult = Literal["published", "not_published", "uncertain"]


class AIPublishReconcileRequest(APIModel):
    decision: PublishReconciliationResult
    published_url: str | None = Field(default=None, max_length=2048)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("note", "published_url")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def validate_published_url(self) -> "AIPublishReconcileRequest":
        if self.decision != "published":
            if self.published_url is not None:
                raise ValueError("只有确认已发布时才可填写笔记链接")
            return self
        if not self.published_url:
            raise ValueError("确认已发布时必须填写小红书笔记链接")
        try:
            parsed = urlsplit(self.published_url)
            host = (parsed.hostname or "").lower()
            port = parsed.port
        except ValueError:
            raise ValueError("请填写有效的小红书 HTTPS 笔记链接") from None
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
            or not any(
                host == domain or host.endswith(f".{domain}")
                for domain in ("xiaohongshu.com", "xhslink.com")
            )
            or not parsed.path.strip("/")
            or any(character.isspace() or ord(character) < 32 for character in self.published_url)
        ):
            raise ValueError("请填写有效的小红书 HTTPS 笔记链接")
        return self


class AIPublishDispatchOut(APIModel):
    id: int
    channel: Literal["xhs", "qq"]
    status: AutoPublishStatus
    attempts: int
    article_publish_attempt_id: str | None
    last_error: str | None
    reconciliation_result: PublishReconciliationResult | None = None
    reconciliation_note: str | None = None
    published_url: str | None = None
    reconciled_at: datetime | None = None
    reconciled_by_admin_id: int | None = None
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime
