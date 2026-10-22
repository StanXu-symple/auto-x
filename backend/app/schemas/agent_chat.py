from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import APIModel


class SendAgentMessage(APIModel):
    request_id: UUID
    content: str = Field(min_length=1, max_length=20000)
    skill_ids: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list, max_length=20)

    @field_validator("content")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("消息不能为空")
        return value.strip()

    @field_validator("skill_ids")
    @classmethod
    def unique_ids(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("不能重复关联同一提示词或 Skill")
        return value


class AgentArticle(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=300)
    excerpt: str = Field(max_length=2000)
    content: str = Field(min_length=1, max_length=50000)


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reply: str = Field(min_length=1, max_length=20000)
    article: AgentArticle | None


class AgentReference(APIModel):
    id: int
    name: str
    content_type: str
    version: int


class AgentConversationOut(APIModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class AgentTurnOut(APIModel):
    id: str
    conversation_id: str
    request_id: str
    sequence: int
    content: str
    skill_snapshot: list[AgentReference]
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    attempts: int
    reply: str | None
    article: AgentArticle | None
    error: str | None
    article_id: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator("article_id", mode="before")
    @classmethod
    def string_id(cls, value: object) -> str | None:
        return str(value) if value is not None else None


class AgentConversationDetail(AgentConversationOut):
    turns: list[AgentTurnOut]
