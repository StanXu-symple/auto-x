from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import APIModel

ArticleSource = Literal["ai", "user"]
ArticleStatus = Literal["draft", "approved", "rejected"]
ArticlePublishStatus = Literal["unpublished", "queued", "published", "failed"]
ArticlePublishChannel = Literal["qq", "xhs"]


class ArticleCreate(APIModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=50000)
    excerpt: str | None = Field(default=None, max_length=1000)
    images: list[str] = Field(default_factory=list, max_length=18)
    status: ArticleStatus = "draft"

    @field_validator("title", "content")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("excerpt")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class ArticlePatch(APIModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = Field(default=None, min_length=1, max_length=50000)
    excerpt: str | None = Field(default=None, max_length=1000)
    images: list[str] | None = Field(default=None, max_length=18)
    status: ArticleStatus | None = None
    revision: int = Field(ge=1)

    @model_validator(mode="after")
    def require_change(self) -> "ArticlePatch":
        if not (self.model_fields_set - {"revision"}):
            raise ValueError("at least one article field must be provided")
        return self

    @field_validator("title", "content")
    @classmethod
    def normalize_required_text(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("field cannot be null")
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("excerpt")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("status", mode="before")
    @classmethod
    def reject_null_status(cls, value: object) -> object:
        if value is None:
            raise ValueError("field cannot be null")
        return value

    @field_validator("images", mode="before")
    @classmethod
    def reject_null_images(cls, value: object) -> object:
        if value is None:
            raise ValueError("field cannot be null")
        return value


class ArticleOut(APIModel):
    id: int
    job_id: int | None
    source_tweet_id: int | None
    article_source: ArticleSource
    title: str
    content: str
    excerpt: str | None
    images: list[str]
    status: ArticleStatus
    publish_status: ArticlePublishStatus
    publish_channel: ArticlePublishChannel | None
    publish_error: str | None
    published_at: datetime | None
    revision: int
    created_at: datetime
    updated_at: datetime


class ArticlePublishCreate(APIModel):
    channel: ArticlePublishChannel
    bot_id: int | None = Field(default=None, gt=0)
    group_openids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("group_openids")
    @classmethod
    def unique_groups(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @model_validator(mode="after")
    def require_qq_target(self) -> "ArticlePublishCreate":
        if self.channel == "qq" and (self.bot_id is None or not self.group_openids):
            raise ValueError("QQ 推送必须选择机器人和群")
        return self


class ArticlePublishAccepted(APIModel):
    message: str
    channel: ArticlePublishChannel
    publish_status: ArticlePublishStatus
    delivery_ids: list[int] = Field(default_factory=list)


class ArticlePublishHistoryOut(APIModel):
    attempt_id: str
    channel: ArticlePublishChannel
    status: ArticlePublishStatus
    target_summary: str
    delivery_count: int
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
