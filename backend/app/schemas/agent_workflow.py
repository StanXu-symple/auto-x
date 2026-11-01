from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import APIModel

NodeType = Literal["custom", "article", "review"]
PositiveId = Annotated[int, Field(gt=0)]


class NodeInput(APIModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    node_type: NodeType = "custom"
    skill_ids: list[PositiveId] = Field(min_length=1, max_length=20)
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def name_required(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()

    @field_validator("skill_ids")
    @classmethod
    def distinct_skills(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("同一节点不能重复关联相同的提示词或 Skill")
        return value


class NodeUpdate(NodeInput):
    version: int = Field(ge=1)


class NodeOut(NodeInput):
    id: int
    version: int
    created_at: datetime
    updated_at: datetime


class WorkflowInput(APIModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    node_ids: list[PositiveId] = Field(min_length=1, max_length=10)
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def name_required(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()


class WorkflowUpdate(WorkflowInput):
    version: int = Field(ge=1)


class WorkflowOut(WorkflowInput):
    id: int
    version: int
    nodes: list[NodeOut]
    created_at: datetime
    updated_at: datetime


class RunInput(APIModel):
    model_config = ConfigDict(extra="forbid")
    workflow_id: PositiveId
    workflow_version: int = Field(ge=1)
    request_id: UUID
    input_text: str = Field(min_length=1, max_length=30000)
    article_id: PositiveId | None = None
    article_revision: int | None = Field(default=None, ge=1)
    tweet_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_source(self):
        self.input_text = self.input_text.strip()
        if not self.input_text:
            raise ValueError("请填写本次流程的任务要求")
        if self.article_id and self.tweet_id:
            raise ValueError("每次只能选择一种来源")
        if bool(self.article_id) != bool(self.article_revision):
            raise ValueError("文章来源需要同时提供文章编号和版本")
        return self


class StepOut(APIModel):
    position: int
    node_name: str
    node_type: NodeType
    status: str
    attempts: int
    output: dict[str, Any] | None
    usage: dict[str, Any] | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None


class RunOut(APIModel):
    id: str
    workflow_id: int | None
    workflow_name: str
    workflow_version: int
    status: str
    current_position: int
    error: str | None
    article_id: int | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class RunDetail(RunOut):
    input_text: str
    source_snapshot: dict[str, Any] | None
    workflow_snapshot: dict[str, Any]
    config_snapshot: dict[str, Any]
    steps: list[StepOut]
    source_changed: bool = False


class TextResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=30000)


class ArticleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=30000)
    excerpt: str | None = Field(max_length=1000)

    @field_validator("title", "content")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("文章不能为空")
        return value.strip()


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: float = Field(ge=0, le=100)
    decision: Literal["pass", "revise", "reject"]
    summary: str = Field(min_length=1, max_length=4000)
    issues: list[Annotated[str, Field(max_length=1000)]] = Field(max_length=30)
    suggestions: list[Annotated[str, Field(max_length=1000)]] = Field(max_length=30)


RESULT_TYPES = {"custom": TextResult, "article": ArticleResult, "review": ReviewResult}
