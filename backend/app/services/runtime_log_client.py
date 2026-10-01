"""Relay authenticated SSE logs from a Nacos-discovered worker."""

import json
from collections.abc import AsyncIterator

import httpx

from app.control_plane.nacos import NacosClient
from app.control_plane.security import TokenClient
from app.core.config import Settings
from app.services.runtime_logs import log_path, sse_event, stream_log


class RuntimeLogUnavailableError(RuntimeError):
    pass


def remote_log_service(settings: Settings, system: str) -> str | None:
    log_path(system)  # Validate the allowlist even when selecting a remote source.
    if not settings.nacos_server_addr:
        return None
    if system == "xhs-worker" and settings.xhs_transport == "http":
        return settings.xhs_service_name
    if system == "camoufox-worker":
        return settings.camoufox_service_name
    return None


async def remote_logs(
    settings: Settings, system: str, service: str, tail: int
) -> AsyncIterator[str]:
    # HTTP headers, discovery and the first SSE frame must arrive promptly.
    # Workers send a keepalive every 15s, including when their log is still empty.
    async with httpx.AsyncClient(timeout=httpx.Timeout(35, connect=5), trust_env=False) as http:
        try:
            nacos = NacosClient(
                http,
                settings.nacos_server_addr,
                settings.nacos_namespace,
                settings.nacos_group,
                settings.nacos_username,
                settings.nacos_password,
            )
            tokens = TokenClient(
                http,
                settings.service_auth_url,
                "runtime-logs",
                settings.runtime_logs_client_secret_file,
                nacos=nacos,
            )
            target = await nacos.discover(service)
            headers = {"Authorization": f"Bearer {await tokens.token(system)}"}
            async with http.stream(
                "GET",
                f"{target.url}/v1/logs/stream",
                params={"tail": tail},
                headers=headers,
            ) as response:
                response.raise_for_status()
                if response.headers.get("content-type", "").split(";")[0] != "text/event-stream":
                    raise ValueError("Invalid log stream content type")
                event = ""
                data: list[str] = []
                frame_size = 0
                ready = False
                async for line in response.aiter_lines():
                    frame_size += len(line)
                    if frame_size > 8 * 1024 * 1024:
                        raise ValueError("Log frame too large")
                    if line.startswith(":"):
                        yield ": keepalive\n\n"
                    elif line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif not line:
                        if event in {"ready", "log"} and data:
                            payload = json.loads("\n".join(data))
                            if event == "ready":
                                if (
                                    ready
                                    or payload.get("system") != system
                                    or not isinstance(payload.get("lines"), list)
                                    or not all(isinstance(v, str) for v in payload["lines"])
                                ):
                                    raise ValueError("Invalid initial log frame")
                                ready = True
                            elif not ready or not isinstance(payload.get("line"), str):
                                raise ValueError("Invalid log frame")
                            yield sse_event(event, payload)
                        event, data, frame_size = "", [], 0
                if not ready:
                    raise ValueError("Missing initial log frame")
        except (httpx.HTTPError, OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            # Do not expose URLs with Nacos tokens, service credentials or remote response bodies.
            raise RuntimeLogUnavailableError(
                f"{system} 日志读取失败，请检查 Nacos 注册、服务认证及节点网络"
            ) from exc


async def system_logs(settings: Settings, system: str, *, tail: int = 200) -> AsyncIterator[str]:
    service = remote_log_service(settings, system)
    source = (
        remote_logs(settings, system, service, tail) if service else stream_log(system, tail=tail)
    )
    try:
        async for event in source:
            yield event
    finally:
        await source.aclose()
