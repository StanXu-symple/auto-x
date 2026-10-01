"""Typed browser jobs; arbitrary code execution is not exposed."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.xhs_service import LoginJob, PostJob, XHSJobRequest


class BrowserPostJob(PostJob):
    encrypted_a1: str = Field(min_length=1, max_length=16384, repr=False)
    encrypted_web_session: str = Field(min_length=1, max_length=16384, repr=False)
    cookie_version: int = Field(ge=1)


class TweetScreenshotJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["x_screenshot"]
    tweet_id: str = Field(pattern=r"^[0-9]{1,32}$")
    username: str = Field(pattern=r"^[A-Za-z0-9_]{1,15}$")
    expected_text: str = Field(max_length=50000)
    expected_media_count: int = Field(default=0, ge=0, le=16)


class BrowserJobRequest(XHSJobRequest):
    payload: Annotated[
        LoginJob | BrowserPostJob | TweetScreenshotJob, Field(discriminator="operation")
    ]
