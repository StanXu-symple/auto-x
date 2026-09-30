from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.common import APIModel
from app.schemas.qq import validate_template_variables


class QQMessageTemplateWrite(APIModel):
    name: str = Field(min_length=1, max_length=100)
    message_template: str = Field(min_length=1, max_length=2000)
    template_variables: dict[str, str] = Field(default_factory=dict)

    @field_validator("name", "message_template", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("template_variables")
    @classmethod
    def validate_variables(cls, value: dict[str, str]) -> dict[str, str]:
        return validate_template_variables(value)


class QQMessageTemplateOut(QQMessageTemplateWrite):
    id: int
    created_at: datetime
    updated_at: datetime
