"""Retryable XHS completion notifications to the authenticated backend.

The body contains only the job identity. The backend reads the canonical
PostgreSQL result itself, so neither callback transport failures nor a forged
result body can change a publication outcome.
"""

import logging

import httpx

from app.control_plane.nacos import NacosClient
from app.control_plane.security import TokenClient

logger = logging.getLogger(__name__)


class XHSCompletionDelivery:
    def __init__(self, settings, store):
        self.settings = settings
        self.store = store
        self.http = httpx.AsyncClient(timeout=10, trust_env=False)
        self.nacos = NacosClient(
            self.http, settings.nacos_server_addr, settings.nacos_namespace,
            settings.nacos_group, settings.nacos_username, settings.nacos_password,
        )
        self.tokens = TokenClient(
            self.http, settings.service_auth_url, "xhs-worker",
            settings.service_client_secret_file, nacos=self.nacos,
        )

    async def deliver_once(self):
        for job in await self.store.callback_jobs():
            delivered = False
            try:
                target = await self.nacos.discover(self.settings.xhs_result_service_name)
                token = await self.tokens.token("backend")
                response = await self.http.post(
                    f"{target.url}{self.settings.api_prefix}/internal/xhs/jobs/"
                    f"{job.job_id}/completion",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"job_id": job.job_id},
                )
                response.raise_for_status()
                delivered = True
            except (httpx.HTTPError, RuntimeError, ValueError):
                # Do not log auth headers, body or credential-bearing URLs.
                logger.warning(
                    "XHS completion callback unavailable; durable result will be retried",
                    extra={"job_id": job.job_id, "attempt": job.callback_attempts + 1},
                )
            await self.store.mark_callback(
                job.job_id, delivered=delivered, state=job.state
            )

    async def close(self):
        await self.http.aclose()
