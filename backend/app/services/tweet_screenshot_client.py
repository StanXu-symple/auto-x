"""Nacos-discovered, authenticated X screenshot RPC with pinned binary retrieval."""

import asyncio
import base64
import hashlib
import uuid
from pathlib import Path

import httpx

from app.schemas.camoufox import BrowserJobRequest
from app.schemas.xhs_service import XHSJobState
from app.services.browser_screenshot_artifacts import MAX_SCREENSHOT_BYTES
from app.services.xhs_client import XHSServiceClient

# Public screenshot work has a separate HTTP lock from actual XHS admin sessions.
SCREENSHOT_ACCOUNT_ID = 2147483647


class ScreenshotBusyError(RuntimeError):
    """The browser did not accept work; retry later without consuming a capture attempt."""


class TweetScreenshotClient(XHSServiceClient):
    client_id = "screenshot-worker"
    audience = "camoufox-worker"
    namespace = "camoufox"
    request_model = BrowserJobRequest

    def __init__(self, settings):
        # Validate before allocating HTTP clients, so a node awaiting its secret
        # can retry initialization without accumulating transports.
        if not Path(settings.tweet_screenshot_client_secret_file).read_text().strip():
            raise ValueError("X 截图调用方 secret 为空")
        super().__init__(settings.model_copy(update={
            "service_client_secret_file": settings.tweet_screenshot_client_secret_file,
        }))
        self.service_name = settings.camoufox_service_name

    async def capture(
        self,
        *,
        tweet_id: str,
        username: str,
        expected_text: str,
        expected_media_count: int = 0,
        job_id: str | None = None,
    ) -> dict:
        job = BrowserJobRequest(
            job_id=job_id or uuid.uuid4().hex,
            admin_id=SCREENSHOT_ACCOUNT_ID,
            payload={
                "operation": "x_screenshot",
                "tweet_id": tweet_id,
                "username": username,
                "expected_text": expected_text,
                "expected_media_count": expected_media_count,
            },
        )
        try:
            async with asyncio.timeout(self.settings.camoufox_job_timeout_seconds + 90):
                target = await self.endpoint(SCREENSHOT_ACCOUNT_ID)
                response = await self.http.post(
                    f"{target.url}/v1/jobs",
                    headers=await self.headers(),
                    data={"job": job.model_dump_json()},
                    timeout=30,
                )
                response.raise_for_status()
                state = XHSJobState.model_validate(response.json())
                while True:
                    if state.job_id != job.job_id:
                        raise ValueError("X 截图任务响应 ID 不匹配")
                    if state.state != "running":
                        break
                    await asyncio.sleep(1)
                    response = await self.http.get(
                        f"{target.url}/v1/jobs/{job.job_id}", headers=await self.headers()
                    )
                    response.raise_for_status()
                    state = XHSJobState.model_validate(response.json())
                if state.state == "failed":
                    raise RuntimeError(state.error or "X 截图任务失败")
                if not state.data.get("screenshot_ready"):
                    raise ValueError("X 截图任务缺少图片")
                contents = bytearray()
                async with self.http.stream(
                    "GET",
                    f"{target.url}/v1/jobs/{job.job_id}/screenshot",
                    headers=await self.headers(),
                ) as download:
                    download.raise_for_status()
                    if download.headers.get("content-type", "").split(";")[0] != "image/png":
                        raise ValueError("X 截图响应类型无效")
                    async for chunk in download.aiter_bytes(65536):
                        contents.extend(chunk)
                        if len(contents) > MAX_SCREENSHOT_BYTES:
                            raise ValueError("X 截图超过 8MB")
                if (
                    not contents.startswith(b"\x89PNG\r\n\x1a\n")
                    or hashlib.sha256(contents).hexdigest() != state.data.get("sha256")
                    or len(contents) != state.data.get("size_bytes")
                ):
                    raise ValueError("X 截图下载校验失败")
                return {**state.data, "png_base64": base64.b64encode(contents).decode("ascii")}
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise RuntimeError("X 截图服务请求超时") from exc
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in {409, 429}:
                raise ScreenshotBusyError("浏览器正忙，请稍后重试") from exc
            raise RuntimeError(f"X 截图服务返回 HTTP {code}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("X 截图服务连接失败") from exc
