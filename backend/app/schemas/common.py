from datetime import UTC, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, field_serializer

T = TypeVar("T")


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_datetime(self, value: object) -> object:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return value


class Page(APIModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class MessageResponse(APIModel):
    message: str


class AcceptedResponse(APIModel):
    message: str
    user_id: int
    scheduled_at: datetime
