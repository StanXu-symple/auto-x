"""Authenticated Nacos-discovered calls from XHS to the browser service."""

import asyncio
import base64
import os

import httpx

from app.schemas.camoufox import BrowserJobRequest
from app.schemas.xhs_service import XHSVerification
from app.services.xhs_client import XHSServiceClient
from app.services.xhs_verification import clear_verification_image, verification_image_path


class CamoufoxServiceClient(XHSServiceClient):
    client_id = "xhs-worker"
    audience = "camoufox-worker"
    namespace = "camoufox"
    request_model = BrowserJobRequest

    def __init__(self, settings):
        super().__init__(settings)
        self.service_name = settings.camoufox_service_name
        self.revisions = {}

    async def submit(self, *, admin_id, **kwargs):
        await asyncio.to_thread(clear_verification_image, admin_id)
        try:
            return await super().submit(admin_id=admin_id, **kwargs)
        finally:
            self.revisions.pop(admin_id, None)
            await asyncio.to_thread(clear_verification_image, admin_id)

    async def poll_verification(self, target, admin_id):
        try:
            response = await self.http.get(
                f"{target.url}/v1/verification/{admin_id}",
                headers=await self.headers(),
                params={"version": self.revisions[admin_id]} if admin_id in self.revisions else {},
            )
            response.raise_for_status()
            item = XHSVerification.model_validate(response.json())
            if not item.required:
                self.revisions.pop(admin_id, None)
                await asyncio.to_thread(clear_verification_image, admin_id)
            elif item.image:
                content = base64.b64decode(
                    item.image.removeprefix("data:image/png;base64,"), validate=True
                )
                if len(content) > 10 * 1024 * 1024 or not content.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("Invalid verification image")
                await asyncio.to_thread(self._save_image, admin_id, content)
                self.revisions[admin_id] = item.version
        except (httpx.HTTPError, ValueError, OSError):
            # A transient QR polling error must not turn a submitted publish into a retry.
            return

    @staticmethod
    def _save_image(admin_id, content):
        path = verification_image_path(admin_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(content)
        temporary.chmod(0o600)
        os.replace(temporary, path)
