"""Nacos Config HTTP clients.

The project intentionally uses Nacos' HTTP API instead of pulling in a second
Nacos SDK.  ``NacosConfigClient`` is synchronous so it can be used while
Pydantic settings are being constructed; ``AsyncNacosConfigClient`` is the
non-blocking counterpart for application code that already runs on an event
loop.

Only the small subset of the Nacos Config API needed by X Sentinel is exposed:
read, publish, delete, and JSON configuration merging.  Naming/discovery lives
in :mod:`app.control_plane.nacos`.
"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel

if TYPE_CHECKING:  # pragma: no cover - imports are only for static checkers
    from app.control_plane.nacos import NacosClient


CONFIG_ENDPOINT = "/nacos/v1/cs/configs"


class NacosConfigError(ValueError):
    """Raised when Nacos returns malformed or unusable configuration data."""


@dataclass(frozen=True)
class NacosConfigKey:
    """The identity of a Nacos configuration item."""

    data_id: str
    group: str = "X_SENTINEL"
    tenant: str = ""

    def __post_init__(self) -> None:
        if not self.data_id.strip():
            raise ValueError("Nacos data_id must not be empty")
        if not self.group.strip():
            raise ValueError("Nacos group must not be empty")


def merge_config(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge two mappings without mutating either input.

    Values in ``override`` win.  Nested mappings are merged recursively, while
    lists and scalar values are replaced as a whole.  This makes a Nacos
    document suitable for overriding only the deployment-specific portions of
    a checked-in defaults document.
    """

    result: dict[str, Any] = copy.deepcopy(dict(base))
    for key, value in override.items():
        current = result.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            result[key] = merge_config(current, value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _normalise_namespace(namespace: str) -> str:
    value = (namespace or "").strip()
    return "" if value.lower() == "public" else value


def _normalise_content(content: Any) -> tuple[str, str]:
    """Return ``(text, inferred_type)`` for a publish request."""

    if isinstance(content, BaseModel):
        content = content.model_dump(mode="json")
    if isinstance(content, (Mapping, list, tuple)):
        return json.dumps(content, ensure_ascii=False, indent=2), "json"
    if isinstance(content, bytes):
        return content.decode("utf-8"), "text"
    if isinstance(content, str):
        return content, "text"
    raise TypeError("Nacos config content must be text, bytes, a mapping, or a Pydantic model")


def _json_mapping(text: str, key: NacosConfigKey) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise NacosConfigError(f"Nacos config {key.data_id!r} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise NacosConfigError(f"Nacos config {key.data_id!r} must contain a JSON object")
    return payload


class _ConfigClientBase:
    """Shared validation and serialization for sync and async clients."""

    def __init__(
        self,
        server: str,
        namespace: str = "public",
        group: str = "X_SENTINEL",
        username: str = "",
        password: str = "",
    ) -> None:
        server = (server or "").strip().rstrip("/")
        # Accept both the root address (``http://nacos:8848``) and the
        # address copied from the Nacos console (``.../nacos``).  Endpoint
        # paths below include the context once, so strip a duplicate suffix.
        if server.lower().endswith("/nacos"):
            server = server[:-6].rstrip("/")
        if not server:
            raise ValueError("Nacos server address must not be empty")
        self.server = server
        self.namespace = _normalise_namespace(namespace)
        self.group = (group or "X_SENTINEL").strip()
        if not self.group:
            raise ValueError("Nacos group must not be empty")
        self.username = username.strip()
        self.password = password
        self.access_token = ""
        self.token_expires = 0.0

    def _key(self, data_id: str, group: str | None, tenant: str | None) -> NacosConfigKey:
        return NacosConfigKey(
            data_id=data_id,
            group=(group or self.group).strip(),
            tenant=_normalise_namespace(self.namespace if tenant is None else tenant),
        )

    def _config_params(self, key: NacosConfigKey) -> dict[str, str]:
        params = {"dataId": key.data_id, "group": key.group}
        if key.tenant:
            params["tenant"] = key.tenant
        if self.access_token:
            params["accessToken"] = self.access_token
        return params

    def _publish_data(
        self, key: NacosConfigKey, content: Any, config_type: str | None
    ) -> dict[str, str]:
        text, inferred_type = _normalise_content(content)
        selected_type = (config_type or inferred_type).strip().lower()
        if not selected_type:
            selected_type = inferred_type
        payload = self._config_params(key)
        payload.update({"content": text, "type": selected_type})
        return payload


class NacosConfigClient(_ConfigClientBase):
    """Synchronous Nacos Config client.

    ``http``/``client`` may be supplied for connection pooling or tests.  The
    client owns a default ``httpx.Client`` and can be used as a context manager
    when no external client is supplied.
    """

    def __init__(
        self,
        server: str,
        namespace: str = "public",
        group: str = "X_SENTINEL",
        username: str = "",
        password: str = "",
        *,
        http: httpx.Client | None = None,
        client: httpx.Client | None = None,
        timeout: float = 5.0,
    ) -> None:
        super().__init__(server, namespace, group, username, password)
        if http is not None and client is not None:
            raise ValueError("Pass either http or client, not both")
        self.http = http or client or httpx.Client(timeout=timeout, trust_env=False)
        self._owns_http = http is None and client is None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None, **kwargs: Any) -> NacosConfigClient:
        """Construct a client from the standard Nacos environment variables."""

        import os

        values = os.environ if environ is None else environ
        return cls(
            values.get("NACOS_SERVER_ADDR", ""),
            values.get("NACOS_NAMESPACE", "public"),
            values.get("NACOS_CONFIG_GROUP")
            or values.get("NACOS_GROUP", "X_SENTINEL"),
            values.get("NACOS_USERNAME", ""),
            values.get("NACOS_PASSWORD", ""),
            **kwargs,
        )

    def _ensure_token(self) -> None:
        if not self.username or (self.access_token and time.monotonic() < self.token_expires):
            return
        response = self.http.post(
            f"{self.server}/nacos/v1/auth/login",
            data={"username": self.username, "password": self.password},
        )
        response.raise_for_status()
        try:
            payload = response.json()
            token = str(payload["accessToken"])
            ttl = int(payload.get("tokenTtl", 1800))
        except (KeyError, TypeError, ValueError) as exc:
            raise NacosConfigError(
                "Nacos login response did not contain a valid access token"
            ) from exc
        self.access_token = token
        self.token_expires = time.monotonic() + max(1, ttl - 30)

    def get_config(
        self, data_id: str, group: str | None = None, tenant: str | None = None
    ) -> str | None:
        key = self._key(data_id, group, tenant)
        self._ensure_token()
        response = self.http.get(f"{self.server}{CONFIG_ENDPOINT}", params=self._config_params(key))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        # Nacos versions differ: some return 404 for a missing item, while
        # others return HTTP 200 with an empty body.  Treat both forms as
        # absent so callers can consistently apply defaults.
        return response.text if response.text.strip() else None

    def get_json_config(
        self,
        data_id: str,
        group: str | None = None,
        tenant: str | None = None,
        *,
        default: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        key = self._key(data_id, group, tenant)
        text = self.get_config(key.data_id, key.group, key.tenant)
        if text is None or not text.strip():
            return copy.deepcopy(dict(default)) if default is not None else None
        return _json_mapping(text, key)

    def publish_config(
        self,
        data_id: str,
        content: Any,
        group: str | None = None,
        tenant: str | None = None,
        *,
        config_type: str | None = None,
    ) -> bool:
        key = self._key(data_id, group, tenant)
        self._ensure_token()
        response = self.http.post(
            f"{self.server}{CONFIG_ENDPOINT}", data=self._publish_data(key, content, config_type)
        )
        response.raise_for_status()
        return response.text.strip().lower() == "true"

    def publish_json_config(
        self,
        data_id: str,
        content: Mapping[str, Any] | BaseModel,
        group: str | None = None,
        tenant: str | None = None,
    ) -> bool:
        return self.publish_config(data_id, content, group, tenant, config_type="json")

    def delete_config(
        self, data_id: str, group: str | None = None, tenant: str | None = None
    ) -> bool:
        key = self._key(data_id, group, tenant)
        self._ensure_token()
        response = self.http.delete(
            f"{self.server}{CONFIG_ENDPOINT}", params=self._config_params(key)
        )
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return response.text.strip().lower() == "true"

    def merge_remote_config(
        self,
        data_id: str,
        defaults: Mapping[str, Any] | None = None,
        group: str | None = None,
        tenant: str | None = None,
    ) -> dict[str, Any]:
        """Merge Nacos values over local defaults and return a new mapping."""

        remote = self.get_json_config(data_id, group, tenant)
        return merge_config(defaults or {}, remote or {})

    def merge_json_config(
        self,
        data_id: str,
        patch: Mapping[str, Any],
        group: str | None = None,
        tenant: str | None = None,
        *,
        publish: bool = True,
    ) -> dict[str, Any]:
        """Merge ``patch`` into the remote document, optionally publishing it."""

        key = self._key(data_id, group, tenant)
        current = self.get_json_config(key.data_id, key.group, key.tenant) or {}
        merged = merge_config(current, patch)
        if publish and not self.publish_json_config(key.data_id, merged, key.group, key.tenant):
            raise NacosConfigError(f"Nacos rejected config publish for {key.data_id!r}")
        return merged

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def __enter__(self) -> NacosConfigClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


class AsyncNacosConfigClient(_ConfigClientBase):
    """Asynchronous counterpart of :class:`NacosConfigClient`."""

    def __init__(
        self,
        server: str,
        namespace: str = "public",
        group: str = "X_SENTINEL",
        username: str = "",
        password: str = "",
        *,
        http: httpx.AsyncClient | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 5.0,
        nacos: NacosClient | None = None,
    ) -> None:
        # ``nacos`` lets an existing naming client share its HTTP connection and
        # authentication token.  It is intentionally duck-typed at runtime to
        # avoid a module import cycle.
        if nacos is not None:
            if http is not None or client is not None:
                raise ValueError("Pass nacos or http/client, not both")
            http = nacos.http
            server = nacos.server
            namespace = nacos.namespace or "public"
            group = nacos.group
            username = nacos.username
            password = nacos.password
        super().__init__(server, namespace, group, username, password)
        self._nacos = nacos
        if http is not None and client is not None:
            raise ValueError("Pass either http or client, not both")
        self.http = http or client or httpx.AsyncClient(timeout=timeout, trust_env=False)
        self._owns_http = http is None and client is None

    @classmethod
    def from_nacos(cls, nacos: NacosClient) -> AsyncNacosConfigClient:
        return cls("", nacos=nacos)

    async def _ensure_token(self) -> None:
        if self._nacos is not None:
            # The naming client owns the token cache and login path.
            await self._nacos._params()
            self.access_token = self._nacos.access_token
            self.token_expires = self._nacos.token_expires
            return
        if not self.username or (self.access_token and time.monotonic() < self.token_expires):
            return
        response = await self.http.post(
            f"{self.server}/nacos/v1/auth/login",
            data={"username": self.username, "password": self.password},
        )
        response.raise_for_status()
        try:
            payload = response.json()
            token = str(payload["accessToken"])
            ttl = int(payload.get("tokenTtl", 1800))
        except (KeyError, TypeError, ValueError) as exc:
            raise NacosConfigError(
                "Nacos login response did not contain a valid access token"
            ) from exc
        self.access_token = token
        self.token_expires = time.monotonic() + max(1, ttl - 30)

    async def get_config(
        self, data_id: str, group: str | None = None, tenant: str | None = None
    ) -> str | None:
        key = self._key(data_id, group, tenant)
        await self._ensure_token()
        response = await self.http.get(
            f"{self.server}{CONFIG_ENDPOINT}", params=self._config_params(key)
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.text if response.text.strip() else None

    async def get_json_config(
        self,
        data_id: str,
        group: str | None = None,
        tenant: str | None = None,
        *,
        default: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        key = self._key(data_id, group, tenant)
        text = await self.get_config(key.data_id, key.group, key.tenant)
        if text is None or not text.strip():
            return copy.deepcopy(dict(default)) if default is not None else None
        return _json_mapping(text, key)

    async def publish_config(
        self,
        data_id: str,
        content: Any,
        group: str | None = None,
        tenant: str | None = None,
        *,
        config_type: str | None = None,
    ) -> bool:
        key = self._key(data_id, group, tenant)
        await self._ensure_token()
        response = await self.http.post(
            f"{self.server}{CONFIG_ENDPOINT}", data=self._publish_data(key, content, config_type)
        )
        response.raise_for_status()
        return response.text.strip().lower() == "true"

    async def publish_json_config(
        self,
        data_id: str,
        content: Mapping[str, Any] | BaseModel,
        group: str | None = None,
        tenant: str | None = None,
    ) -> bool:
        return await self.publish_config(data_id, content, group, tenant, config_type="json")

    async def delete_config(
        self, data_id: str, group: str | None = None, tenant: str | None = None
    ) -> bool:
        key = self._key(data_id, group, tenant)
        await self._ensure_token()
        response = await self.http.delete(
            f"{self.server}{CONFIG_ENDPOINT}", params=self._config_params(key)
        )
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return response.text.strip().lower() == "true"

    async def merge_remote_config(
        self,
        data_id: str,
        defaults: Mapping[str, Any] | None = None,
        group: str | None = None,
        tenant: str | None = None,
    ) -> dict[str, Any]:
        remote = await self.get_json_config(data_id, group, tenant)
        return merge_config(defaults or {}, remote or {})

    async def merge_json_config(
        self,
        data_id: str,
        patch: Mapping[str, Any],
        group: str | None = None,
        tenant: str | None = None,
        *,
        publish: bool = True,
    ) -> dict[str, Any]:
        key = self._key(data_id, group, tenant)
        current = await self.get_json_config(key.data_id, key.group, key.tenant) or {}
        merged = merge_config(current, patch)
        if publish and not await self.publish_json_config(
            key.data_id, merged, key.group, key.tenant
        ):
            raise NacosConfigError(f"Nacos rejected config publish for {key.data_id!r}")
        return merged

    async def aclose(self) -> None:
        if self._owns_http:
            await self.http.aclose()

    async def __aenter__(self) -> AsyncNacosConfigClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()


__all__ = [
    "AsyncNacosConfigClient",
    "NacosConfigClient",
    "NacosConfigError",
    "NacosConfigKey",
    "merge_config",
]
