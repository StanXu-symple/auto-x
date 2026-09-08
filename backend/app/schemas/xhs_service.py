from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.xhs_limits import XHS_NOTE_CONTENT_MAX_LENGTH, XHS_NOTE_TITLE_MAX_LENGTH


class LoginJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["login"]
    encrypted_a1: str = Field(min_length=1, max_length=16384, repr=False)
    encrypted_web_session: str = Field(min_length=1, max_length=16384, repr=False)


class PostJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["post"]
    title: str = Field(min_length=1, max_length=XHS_NOTE_TITLE_MAX_LENGTH)
    content: str = Field(min_length=1, max_length=XHS_NOTE_CONTENT_MAX_LENGTH)
    image_count: int = Field(ge=1, le=18)


class XHSJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    admin_id: int = Field(gt=0)
    payload: Annotated[LoginJob | PostJob, Field(discriminator="operation")]


class XHSJobState(BaseModel):
    job_id: str
    state: Literal["running", "succeeded", "failed"]
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class XHSServiceStatus(BaseModel):
    status: Literal["online", "offline"]
    installed: bool
    worker_id: str
    active_tasks: int = 0


class XHSVerification(BaseModel):
    required: bool
    image: str | None = None
    version: str | None = None
