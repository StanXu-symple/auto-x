"""Authenticated Xiaohongshu orchestration API."""

from app.services.browser_job_api import XHSRuntime, create_app  # noqa: F401

app = create_app()
