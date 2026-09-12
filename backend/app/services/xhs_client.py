"""Nacos-discovered XHS calls. Mutating requests are never retried automatically."""

import asyncio
import hashlib
import json
import os
import uuid
from contextlib import ExitStack
from pathlib import Path

import httpx
from redis.asyncio import Redis

from app.control_plane.nacos import Instance, NacosClient
from app.control_plane.security import TokenClient
from app.core.config import Settings
from app.schemas.xhs_service import (
    XHSJobRequest,
    XHSJobState,
    XHSServiceStatus,
    XHSVerification,
)
from app.services.article_media import ALLOWED_IMAGE_SUFFIXES, MAX_ARTICLE_IMAGE_BYTES
from app.services.xhs_jobs import (
    XHSJobFailedError,
    XHSJobTimeoutError,
    XHSWorkerUnavailableError,
)


def validated_source(value: str, admin_id: int) -> Path:
    path = Path(value).resolve()
    upload_root = Path(os.getenv("XHS_UPLOAD_DIR", "/var/lib/xsentinel/xhs-uploads")).resolve()
    article_root = Path(
        os.getenv("ARTICLE_UPLOAD_DIR", "/var/lib/xsentinel/article-uploads")
    ).resolve() / str(admin_id)
    if not any(root in path.parents for root in (upload_root, article_root)):
        raise ValueError("图片路径无效")
    if path.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES or not path.is_file():
        raise ValueError("图片格式或文件无效")
    if path.stat().st_size > MAX_ARTICLE_IMAGE_BYTES:
        raise ValueError("单张图片不能超过 10MB")
    return path


class XHSServiceClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.http = httpx.AsyncClient(timeout=10, trust_env=False)
        self.nacos = NacosClient(
            self.http,
            settings.nacos_server_addr,
            settings.nacos_namespace,
            settings.nacos_group,
            settings.nacos_username,
            settings.nacos_password,
        )
        self.tokens = TokenClient(
            self.http,
            settings.service_auth_url,
            "backend",
            settings.service_client_secret_file,
            nacos=self.nacos,
        )

    async def endpoint(self, admin_id: int) -> Instance:
        instances = await self.nacos.discover_all(self.settings.xhs_service_name)
        # Deterministic account affinity across API replicas; each submitted job is pinned.
        return max(
            instances, key=lambda i: hashlib.sha256(f"{admin_id}:{i.ip}:{i.port}".encode()).digest()
        )

    async def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self.tokens.token('xhs-worker')}"}

    async def status(self) -> dict:
        try:
            target = await self.endpoint(0)
            response = await self.http.get(f"{target.url}/v1/status", headers=await self.headers())
            response.raise_for_status()
            return XHSServiceStatus.model_validate(response.json()).model_dump()
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError):
            return {"status": "offline", "installed": False}

    async def verification(self, redis: Redis, admin_id: int, version: str | None) -> dict:
        try:
            # Routing is shared in Redis, so polling through another API replica still
            # reaches the original browser even if Nacos membership has changed.
            raw = await redis.get(f"xsentinel:xhs:http:active:{admin_id}")
            if not raw:
                return {"required": False}
            route = json.loads(raw)
            target = Instance(route["ip"], int(route["port"]), self.settings.xhs_service_name)
            response = await self.http.get(
                f"{target.url}/v1/verification/{admin_id}",
                params={"version": version} if version else {},
                headers=await self.headers(),
            )
            response.raise_for_status()
            return XHSVerification.model_validate(response.json()).model_dump(exclude_none=True)
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
            raise XHSWorkerUnavailableError("小红书验证码服务暂不可用") from exc

    async def submit(
        self, *, operation: str, admin_id: int, payload: dict, timeout_seconds: float
    ) -> dict:
        try:
            async with asyncio.timeout(timeout_seconds):
                target = await self.endpoint(admin_id)
                paths = []
                body = {**payload, "operation": operation}
                if operation == "post":
                    paths = [
                        await asyncio.to_thread(validated_source, image, admin_id)
                        for image in body.pop("images", [])
                    ]
                    body["image_count"] = len(paths)
                job = XHSJobRequest(job_id=uuid.uuid4().hex, admin_id=admin_id, payload=body)
                with ExitStack() as files:
                    response = await self.http.post(
                        f"{target.url}/v1/jobs",
                        headers=await self.headers(),
                        data={"job": job.model_dump_json()},
                        files=[
                            (
                                "images",
                                (
                                    p.name,
                                    files.enter_context(p.open("rb")),
                                    "application/octet-stream",
                                ),
                            )
                            for p in paths
                        ],
                        timeout=min(timeout_seconds, 60),
                    )
                if response.status_code == 409:
                    raise XHSJobFailedError("该小红书账号已有任务执行中，请等待完成")
                response.raise_for_status()
                state = XHSJobState.model_validate(response.json())
                while state.state == "running":
                    await asyncio.sleep(1)
                    response = await self.http.get(
                        f"{target.url}/v1/jobs/{job.job_id}",
                        headers=await self.headers(),
                    )
                    response.raise_for_status()
                    state = XHSJobState.model_validate(response.json())
                    if state.job_id != job.job_id:
                        raise ValueError("Unexpected XHS job response")
                if state.state == "failed":
                    raise XHSJobFailedError(state.error or "小红书任务执行失败")
                return state.data
        except XHSJobFailedError:
            raise
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise XHSJobTimeoutError(
                "小红书服务请求超时，发布结果可能尚未返回，请核对笔记后再操作，勿重复发布"
            ) from exc
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
            raise XHSWorkerUnavailableError(
                "小红书微服务不可用或响应无效，请检查 Nacos 和服务日志"
            ) from exc

    async def aclose(self):
        await self.http.aclose()
