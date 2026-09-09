"""Small Nacos naming client used by the control plane.

The client uses Nacos' HTTP naming API so the application image does not need a
second SDK or a fixed URL for any peer service.
"""

import asyncio
import json
import os
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.control_plane.contracts import NacosInstanceList


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

    async def beat(self, service_name: str, ip: str, port: int) -> None:
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
            await self.register(service_name, ip, port)

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
    nacos: NacosClient, service_name: str, ip: str, port: int, interval: float = 5
):
    while True:
        try:
            await nacos.beat(service_name, ip, port)
        except (httpx.HTTPError, RuntimeError, KeyError, ValueError):
            pass
        await asyncio.sleep(interval)


def advertise_identity() -> tuple[str, int]:
    ip = os.environ.get("NACOS_ADVERTISE_IP", "").strip()
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
    port = int(os.environ.get("NACOS_SERVICE_PORT", "0"))
    if not urlsplit(f"http://{ip}").hostname or not 0 < port < 65536:
        raise ValueError("NACOS_ADVERTISE_IP and NACOS_SERVICE_PORT must be configured")
    return ip, port


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
    "advertise_identity",
    "heartbeat_loop",
    "merge_config",
]
