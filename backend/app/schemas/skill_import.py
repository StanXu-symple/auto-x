from pydantic import Field

from app.schemas.ai import AISkillOut
from app.schemas.common import APIModel


class GitHubSkillImport(APIModel):
    url: str = Field(min_length=1, max_length=2048)
    ref: str | None = Field(default=None, min_length=1, max_length=200)
    path: str | None = Field(default=None, max_length=1024)


class GitHubSkillImportResult(APIModel):
    repository: str
    commit: str
    created: int
    updated: int
    unchanged: int
    skills: list[AISkillOut]
    warnings: list[str] = Field(default_factory=list)
