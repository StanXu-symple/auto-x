"""Versioned HTTP contracts shared by Skill consumers and skill-worker."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.ai import AISkillOut


class SkillResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_ids: list[int] = Field(default_factory=list, max_length=1000)


class SkillContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monitored_user_id: int = Field(gt=0)
    feature_id: int = Field(gt=0)
    fallback_skill_ids: list[int] = Field(default_factory=list, max_length=1000)
    override_skill_ids: list[int] | None = Field(default=None, max_length=1000)


class SkillResolution(BaseModel):
    skills: list[AISkillOut]
    source: Literal["manual_override", "user_feature_binding", "global_default"]


class TaskSkillSelection(BaseModel):
    task_id: int
    selected_ids: list[int]
    skills: list[AISkillOut]

    @model_validator(mode="after")
    def ordered_active_subset(self):
        returned = [s.id for s in self.skills]
        if (
            len(set(self.selected_ids)) != len(self.selected_ids)
            or any(i <= 0 for i in self.selected_ids)
            or any(not s.is_active for s in self.skills)
            or returned != [i for i in self.selected_ids if i in set(returned)]
        ):
            raise ValueError("Invalid task Skill response")
        return self

    @property
    def valid(self) -> bool:
        return bool(self.selected_ids) and self.selected_ids == [s.id for s in self.skills]
