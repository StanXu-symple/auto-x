import asyncio
import time

import httpx

from app.control_plane.monitor import is_fresh
from app.control_plane.nacos import NacosClient
from app.control_plane.resources import unavailable
from app.control_plane.security import TokenClient
from app.core.config import Settings


class MonitoringClient:
    """Short cache and single-flight requests independent of dashboard viewer count."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.http = httpx.AsyncClient(
            timeout=settings.monitor_request_timeout_seconds, trust_env=False
        )
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
        self.lock = asyncio.Lock()
        self.cached: dict | None = None
        self.expires = 0.0

    async def snapshot(self) -> dict:
        async with self.lock:
            if self.cached is not None and time.monotonic() < self.expires:
                return self.cached
            try:
                token = await self.tokens.token("monitor")
                monitor_url = self.settings.monitor_center_url
                if self.settings.nacos_server_addr:
                    monitor_url = (await self.nacos.discover("xsentinel-monitor-center")).url
                response = await self.http.get(
                    f"{monitor_url.rstrip('/')}/v1/resources",
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                data = response.json()
                if not is_fresh(data.get("sampled_at"), self.settings.monitor_stale_seconds):
                    raise ValueError("Expired monitoring snapshot")
                if not isinstance(data.get("instances"), list):
                    raise ValueError("Invalid monitoring snapshot")
                self.cached = data
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                self.cached = {
                    "mode": "microservices",
                    "instances": [],
                    "error": "监控中心暂不可用或采样已过期",
                }
            self.expires = time.monotonic() + 5
            return self.cached

    async def aclose(self):
        await self.http.aclose()


def apply_snapshot(metrics: dict, snapshot: dict) -> dict:
    metrics["monitoring"] = snapshot
    # Preserve application health/heartbeats, but never mix process RSS or Redis
    # allocator limits into container metrics when distributed monitoring is enabled.
    for component in ("api", "database", "redis", "worker", "ai_worker", "qq_worker", "xhs_worker"):
        metric = metrics[component]
        metric.update(unavailable(snapshot.get("error", "未配置此服务的资源采集")))
        items = [i for i in snapshot["instances"] if i["component"] == component]
        if not items:
            continue
        valid = [i for i in items if isinstance(i.get("memory_used_bytes"), (int, float))]
        # Only aggregate complete observations; unavailable replicas must not look like zero.
        if len(valid) != len(items):
            metric["resource_note"] = "部分实例资源不可用，请查看服务实例"
            continue
        used = sum(i["memory_used_bytes"] for i in items)
        total = sum(i.get("memory_total_bytes") or 0 for i in items)
        cpu = [i.get("cpu_percent") for i in items]
        metric.update(
            memory_used_bytes=used,
            memory_total_bytes=total or None,
            memory_percent=round(used / total * 100, 2) if total else None,
            cpu_percent=sum(cpu) if all(isinstance(v, (int, float)) for v in cpu) else None,
            resource_note=None,
        )
    return metrics
