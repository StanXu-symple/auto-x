"""Typed browser jobs; arbitrary code execution is not exposed."""

from typing import Annotated

from pydantic import Field

from app.schemas.xhs_service import LoginJob, PostJob, XHSJobRequest


class BrowserPostJob(PostJob):
    encrypted_a1: str = Field(min_length=1, max_length=16384, repr=False)
    encrypted_web_session: str = Field(min_length=1, max_length=16384, repr=False)
    cookie_version: int = Field(ge=1)


class BrowserJobRequest(XHSJobRequest):
    payload: Annotated[LoginJob | BrowserPostJob, Field(discriminator="operation")]
