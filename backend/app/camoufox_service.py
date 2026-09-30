"""Camoufox process owner: Nacos registration and authenticated browser jobs."""

from app.services.browser_job_api import create_app as create_job_app


def create_app(settings=None, worker_factory=None):
    return create_job_app(settings, worker_factory, browser_service=True)


app = create_app()
