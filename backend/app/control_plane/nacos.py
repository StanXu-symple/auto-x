"""Small Nacos naming client used by X Sentinel services.

The client uses Nacos' HTTP naming API so the application image does not need a
second SDK or a fixed URL for any peer service.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

from app.control_plane.contracts import NacosInstanceList

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Instance:
    ip: str
    port: int
    service_name: str

    @property
    def url(self) -> str:
        host = f"[{self.ip}]" if ":" in self.ip else self.ip
        return f"http://{host}:{self.port}"


class NacosClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        server: str,
        namespace: str,
        group: str,
        username: str = "",
        password: str = "",
    ):
        self.http = http
        self.server = server.rstrip("/")
        if self.server.lower().endswith("/nacos"):
            self.server = self.server[:-6].rstrip("/")
        self.namespace = "" if namespace == "public" else namespace
        self.group = group
        self.username = username
        self.password = password
        self.access_token = ""
        self.token_expires = 0.0

    @property
    def config(self):
        """Return a config client sharing this client's HTTP session/token.

        Naming and config use different Nacos endpoints, but sharing the
        authenticated naming client avoids an extra connection and login when
        a service needs both capabilities.
        """

        from app.control_plane.nacos_config import AsyncNacosConfigClient

        config = getattr(self, "_config_client", None)
        if config is None:
            config = AsyncNacosConfigClient.from_nacos(self)
            self._config_client = config
        return config

    async def get_config(
        self, data_id: str, group: str | None = None, tenant: str | None = None
    ) -> str | None:
        """Read a Nacos Config item using this client's naming session."""

        return await self.config.get_config(data_id, group, tenant)

    async def get_json_config(
        self,
        data_id: str,
        group: str | None = None,
        tenant: str | None = None,
        *,
        default: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Read and validate a JSON object from Nacos Config."""

        return await self.config.get_json_config(data_id, group, tenant, default=default)

    async def publish_config(
        self,
        data_id: str,
        content: Any,
        group: str | None = None,
        tenant: str | None = None,
        *,
        config_type: str | None = None,
    ) -> bool:
        """Publish a Nacos Config item using this client's naming session."""

        return await self.config.publish_config(
            data_id, content, group, tenant, config_type=config_type
        )

    async def publish_json_config(
        self,
        data_id: str,
        content: Any,
        group: str | None = None,
        tenant: str | None = None,
    ) -> bool:
        return await self.config.publish_json_config(data_id, content, group, tenant)

    async def delete_config(
        self, data_id: str, group: str | None = None, tenant: str | None = None
    ) -> bool:
        return await self.config.delete_config(data_id, group, tenant)

    async def merge_remote_config(
        self,
        data_id: str,
        defaults: Mapping[str, Any] | None = None,
        group: str | None = None,
        tenant: str | None = None,
    ) -> dict[str, Any]:
        return await self.config.merge_remote_config(data_id, defaults, group, tenant)

    async def merge_json_config(
        self,
        data_id: str,
        patch: Mapping[str, Any],
        group: str | None = None,
        tenant: str | None = None,
        *,
        publish: bool = True,
    ) -> dict[str, Any]:
        return await self.config.merge_json_config(data_id, patch, group, tenant, publish=publish)

    async def _params(self) -> dict[str, str]:
        if self.username and (not self.access_token or time.monotonic() >= self.token_expires):
            response = await self.http.post(
                f"{self.server}/nacos/v1/auth/login",
                data={"username": self.username, "password": self.password},
            )
            response.raise_for_status()
            payload = response.json()
            self.access_token = payload["accessToken"]
            self.token_expires = time.monotonic() + max(1, int(payload["tokenTtl"]) - 30)
        params = {"namespaceId": self.namespace, "groupName": self.group}
        if self.access_token:
            params["accessToken"] = self.access_token
        return params

    async def register(
        self, service_name: str, ip: str, port: int, metadata: dict[str, str] | None = None
    ):
        params = await self._params()
        params.update(
            {"serviceName": service_name, "ip": ip, "port": str(port), "ephemeral": "true"}
        )
        if metadata:
            params["metadata"] = json.dumps(metadata)
        response = await self.http.post(f"{self.server}/nacos/v1/ns/instance", params=params)
        response.raise_for_status()

    async def beat(
        self,
        service_name: str,
        ip: str,
        port: int,
        metadata: dict[str, str] | None = None,
    ) -> None:
        params = await self._params()
        params.update(
            {"serviceName": service_name, "ip": ip, "port": str(port), "ephemeral": "true"}
        )
        params["beat"] = json.dumps(
            {
                "serviceName": f"{self.group}@@{service_name}",
                "ip": ip,
                "port": port,
                "cluster": "DEFAULT",
                "scheduled": True,
                "weight": 1,
            }
        )
        response = await self.http.put(f"{self.server}/nacos/v1/ns/instance/beat", params=params)
        response.raise_for_status()
        if response.json().get("code") == 20404:
            await self.register(service_name, ip, port, metadata)

    async def deregister(self, service_name: str, ip: str, port: int) -> None:
        params = await self._params()
        params.update(serviceName=service_name, ip=ip, port=str(port), ephemeral="true")
        response = await self.http.delete(f"{self.server}/nacos/v1/ns/instance", params=params)
        response.raise_for_status()

    async def discover(self, service_name: str) -> Instance:
        return (await self.discover_all(service_name))[0]

    async def discover_all(self, service_name: str) -> list[Instance]:
        params = await self._params()
        params["serviceName"] = service_name
        response = await self.http.get(f"{self.server}/nacos/v1/ns/instance/list", params=params)
        response.raise_for_status()
        payload = NacosInstanceList.model_validate(response.json())
        hosts = [host for host in payload.hosts if host.healthy and host.enabled]
        if not hosts:
            raise RuntimeError(f"No healthy Nacos instance for {service_name}")
        return [Instance(host.ip, host.port, service_name) for host in hosts]


async def heartbeat_loop(
    nacos: NacosClient,
    service_name: str,
    ip: str,
    port: int,
    interval: float = 5,
    metadata: dict[str, str] | None = None,
    readiness: Callable[[], Awaitable[bool]] | None = None,
):
    registered = True
    while True:
        ready = True
        if readiness is not None:
            try:
                ready = await readiness()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - an unhealthy dependency removes the instance
                ready = False
                logger.warning(
                    "Readiness probe failed for Nacos service %s at %s:%s",
                    service_name,
                    ip,
                    port,
                    exc_info=True,
                )
        try:
            if not ready:
                if registered:
                    try:
                        await nacos.deregister(service_name, ip, port)
                    except asyncio.CancelledError:
                        raise
                    except (httpx.HTTPError, RuntimeError, KeyError, ValueError):
                        logger.warning(
                            "Unable to deregister unready Nacos service %s at %s:%s; will retry",
                            service_name,
                            ip,
                            port,
                        )
                    else:
                        registered = False
                        logger.warning(
                            "Deregistered unready Nacos service %s at %s:%s",
                            service_name,
                            ip,
                            port,
                        )
            elif registered:
                await nacos.beat(service_name, ip, port, metadata)
            else:
                # A failed heartbeat may mean that Nacos restarted and lost
                # its ephemeral instance state.  Register explicitly after
                # connectivity returns instead of waiting for a 20404 beat.
                await nacos.register(service_name, ip, port, metadata)
                registered = True
                logger.info(
                    "Re-registered Nacos service %s at %s:%s",
                    service_name,
                    ip,
                    port,
                )
        except asyncio.CancelledError:
            raise
        except (httpx.HTTPError, RuntimeError, KeyError, ValueError):
            if registered:
                logger.warning(
                    "Nacos heartbeat failed for %s at %s:%s; registration will be retried",
                    service_name,
                    ip,
                    port,
                )
            registered = False
        await asyncio.sleep(interval)


def advertise_identity(
    advertise_ip: str | None = None, service_port: int | None = None
) -> tuple[str, int]:
    ip = (
        os.environ.get("NACOS_ADVERTISE_IP", "") if advertise_ip is None else advertise_ip
    ).strip()
    if not ip:
        try:
            addresses = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
            ip = next(
                (item[4][0] for item in addresses if not item[4][0].startswith("127.")),
                "",
            )
        except OSError:
            ip = ""
    ip = ip or "127.0.0.1"
    port = (
        int(os.environ.get("NACOS_SERVICE_PORT", "0"))
        if service_port is None
        else int(service_port)
    )
    if not urlsplit(f"http://{ip}").hostname or not 0 < port < 65536:
        raise ValueError("NACOS_ADVERTISE_IP and NACOS_SERVICE_PORT must be configured")
    return ip, port


class NacosServiceRegistration:
    """Own one ephemeral Nacos instance and its heartbeat task."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        nacos: NacosClient,
        service_name: str,
        ip: str,
        port: int,
        metadata: dict[str, str] | None = None,
        readiness: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self.http = http
        self.nacos = nacos
        self.service_name = service_name
        self.ip = ip
        self.port = port
        self.metadata = metadata
        self.readiness = readiness
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self.nacos.register(self.service_name, self.ip, self.port, self.metadata)
        self._heartbeat_task = asyncio.create_task(
            heartbeat_loop(
                self.nacos,
                self.service_name,
                self.ip,
                self.port,
                metadata=self.metadata,
                readiness=self.readiness,
            )
        )

    async def aclose(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        try:
            await self.nacos.deregister(self.service_name, self.ip, self.port)
        except (httpx.HTTPError, RuntimeError, KeyError, ValueError):
            # Ephemeral instances expire automatically.  A temporary Nacos
            # outage must not prevent a process from completing shutdown.
            logger.warning(
                "Unable to deregister Nacos service %s at %s:%s",
                self.service_name,
                self.ip,
                self.port,
                exc_info=True,
            )
        finally:
            await self.http.aclose()


async def start_service_registration(
    settings: Settings,
    default_service_name: str,
    default_service_port: int,
    *,
    metadata: dict[str, str] | None = None,
) -> NacosServiceRegistration | None:
    """Register one application process when a Nacos server is configured.

    Service identity is deployment-local.  Explicit ``NACOS_SERVICE_*``
    environment values take precedence, while callers provide a safe
    process-specific default for direct (non-Compose) execution.
    """

    if not settings.nacos_server_addr:
        return None
    service_name = os.environ.get("NACOS_SERVICE_NAME", default_service_name).strip()
    service_name = service_name or default_service_name
    raw_port = os.environ.get("NACOS_SERVICE_PORT", str(default_service_port))
    try:
        service_port = int(raw_port)
    except ValueError as exc:
        raise ValueError("NACOS_SERVICE_PORT must be an integer") from exc
    ip, port = advertise_identity(settings.nacos_advertise_ip, service_port)
    http = httpx.AsyncClient(
        timeout=settings.nacos_config_timeout_seconds,
        trust_env=False,
    )
    nacos = NacosClient(
        http,
        settings.nacos_server_addr,
        settings.nacos_namespace,
        settings.nacos_group,
        settings.nacos_username,
        settings.nacos_password,
    )
    registration = NacosServiceRegistration(
        http,
        nacos,
        service_name,
        ip,
        port,
        metadata,
    )
    try:
        await registration.start()
    except BaseException:
        await http.aclose()
        raise
    return registration


# Re-export the config clients from the established Nacos module so callers can
# import either naming and config APIs from ``app.control_plane.nacos``.
from app.control_plane.nacos_config import (  # noqa: E402  (intentional late import)
    AsyncNacosConfigClient,
    NacosConfigClient,
    NacosConfigError,
    NacosConfigKey,
    merge_config,
)

__all__ = [
    "AsyncNacosConfigClient",
    "Instance",
    "NacosClient",
    "NacosConfigClient",
    "NacosConfigError",
    "NacosConfigKey",
    "NacosServiceRegistration",
    "advertise_identity",
    "heartbeat_loop",
    "merge_config",
    "start_service_registration",
]
