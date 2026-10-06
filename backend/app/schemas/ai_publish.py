"""Read models for independent automatic article publishing outcomes."""

from datetime import datetime
from typing import Literal

from app.schemas.common import APIModel

AutoPublishStatus = Literal[
    "pending", "retry_wait", "dispatching", "accepted", "published", "failed", "uncertain"
]


class AIPublishDispatchOut(APIModel):
    id: int
    channel: Literal["xhs", "qq"]
    status: AutoPublishStatus
    attempts: int
    article_publish_attempt_id: str | None
    last_error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime
