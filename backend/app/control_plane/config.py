import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


_NACOS_BOOTSTRAP_KEYS = {
    "nacos_server_addr",
    "nacos_namespace",
    "nacos_group",
    "nacos_username",
    "nacos_password",
    "nacos_config_enabled",
    "nacos_config_data_id",
    "nacos_config_group",
    "nacos_config_timeout_seconds",
    "nacos_config_required",
}

# This filter applies to the shared application document. Monitor topology and
# the node-to-address mapping have their own Nacos Data IDs below. Key/socket
# paths and Nacos bootstrap coordinates remain local to the process.
_NACOS_CONTROL_LOCAL_KEYS = {
    "environment",
    "debug",
    "nacos_service_name",
    "nacos_advertise_ip",
    "nacos_service_port",
    "monitor_node_id",
    "service_topology_file",
    "service_auth_private_key_file",
    "service_auth_clients_file",
    "service_auth_public_key_file",
    "service_client_secret_file",
    "docker_socket",
}


def _normalise_config_key(value: object) -> str:
    return str(value).strip().lower().replace("-", "_").replace(".", "_")


def _flatten_config(value: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = _normalise_config_key(raw_key)
        if not prefix and key == "postgresql":
            key = "postgres"
        if not key:
            continue
        full_key = f"{prefix}_{key}" if prefix else key
        if isinstance(raw_value, Mapping):
            result.update(_flatten_config(raw_value, full_key))
        else:
            result[full_key] = raw_value
    return result


def _as_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


async def load_runtime_config(nacos: Any | None) -> dict[str, Any]:
    """Load shared control-plane tuning from Nacos Config.

    Naming/bootstrap values are intentionally supplied by the local process
    environment.  A missing or unavailable document falls back to local
    defaults unless ``NACOS_CONFIG_REQUIRED=true``; this keeps the optional
    control plane compatible with a single-host deployment.
    """

    enabled = _as_bool(os.environ.get("NACOS_CONFIG_ENABLED"), default=True)
    if not enabled:
        return {}
    required = _as_bool(os.environ.get("NACOS_CONFIG_REQUIRED"), default=False)
    if nacos is None:
        if required:
            raise RuntimeError(
                "NACOS_SERVER_ADDR must be configured when NACOS_CONFIG_REQUIRED=true"
            )
        return {}
    data_id = os.environ.get("NACOS_CONFIG_DATA_ID", "x-sentinel-config.json").strip()
    data_id = data_id or "x-sentinel-config.json"
    group = (
        os.environ.get("NACOS_CONFIG_GROUP")
        or os.environ.get("NACOS_GROUP")
        or "X_SENTINEL"
    ).strip()
    group = group or "X_SENTINEL"
    try:
        payload = await nacos.get_json_config(data_id, group=group)
    except Exception as exc:  # noqa: BLE001 - optional remote source fallback
        message = f"Unable to load Nacos Config {data_id!r} for control plane: {exc}"
        if required:
            raise RuntimeError(message) from exc
        logger.warning("%s; using local control-plane defaults", message)
        return {}
    if payload is None:
        message = f"Nacos Config {data_id!r} is not present in group {group!r}"
        if required:
            raise RuntimeError(message)
        logger.warning("%s; using local control-plane defaults", message)
        return {}
    if not isinstance(payload, Mapping):
        message = f"Nacos Config {data_id!r} must contain a JSON object"
        if required:
            raise RuntimeError(message)
        logger.warning("%s; using local control-plane defaults", message)
        return {}
    return {
        key: value
        for key, value in _flatten_config(payload).items()
        if key not in _NACOS_BOOTSTRAP_KEYS and key not in _NACOS_CONTROL_LOCAL_KEYS
    }


def runtime_int(
    runtime: Mapping[str, Any],
    key: str,
    env_key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """Read a bounded integer, preferring Nacos over the local environment."""

    raw = runtime.get(key, os.environ.get(env_key, default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s value; using %s", key.upper(), default)
        return default
    if not minimum <= value <= maximum:
        logger.warning("%s is outside [%s, %s]; using %s", key.upper(), minimum, maximum, default)
        return default
    return value


def runtime_float(
    runtime: Mapping[str, Any],
    key: str,
    env_key: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    """Read a bounded floating-point value, preferring Nacos."""

    raw = runtime.get(key, os.environ.get(env_key, default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s value; using %s", key.upper(), default)
        return default
    if not minimum <= value <= maximum:
        logger.warning("%s is outside [%s, %s]; using %s", key.upper(), minimum, maximum, default)
        return default
    return value


class Node(BaseModel):
    agent_service_name: str = "xsentinel-monitor-agent"


class Service(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    name: str
    component: Literal[
        "api",
        "database",
        "redis",
        "worker",
        "ai_worker",
        "qq_worker",
        "xhs_worker",
        "camoufox_worker",
        "auth",
        "monitor",
        "frontend",
    ]
    node: str
    project: str = "x-sentinel"
    container_service: str
    port: int = Field(default=8200, ge=1, le=65535)


class Topology(BaseModel):
    nodes: dict[str, Node]
    services: list[Service]
    interval_seconds: float = Field(default=10, ge=3, le=60)
    stale_seconds: float = Field(default=45, ge=10, le=300)
    timeout_seconds: float = Field(default=3, ge=0.1, le=10)
    concurrency: int = Field(default=8, ge=1, le=32)

    @model_validator(mode="after")
    def validate_targets(self) -> "Topology":
        if len({service.id for service in self.services}) != len(self.services):
            raise ValueError("Service IDs must be unique")
        if self.stale_seconds <= self.interval_seconds:
            raise ValueError("stale_seconds must exceed interval_seconds")
        for node_id, _node in self.nodes.items():
            if not node_id or ":" in node_id:
                raise ValueError("Node IDs must be nonempty and contain no colon")
        selectors = set()
        for service in self.services:
            if service.node not in self.nodes:
                raise ValueError(f"Unknown node for {service.id}")
            selector = (service.node, service.project, service.container_service)
            if selector in selectors:
                raise ValueError(
                    "Duplicate container selector; replicas are discovered per service"
                )
            selectors.add(selector)
        return self


def validate_url(value: str, schemes: set[str]) -> None:
    url = urlsplit(value)
    if url.scheme not in schemes or not url.hostname or url.username or url.password:
        raise ValueError("Service URLs must have a supported scheme/host and no credentials")
    if url.scheme == "tcp" and not url.port:
        raise ValueError("TCP addresses require a port")


def load_topology() -> Topology:
    path = Path(os.environ.get("SERVICE_TOPOLOGY_FILE", "/etc/xsentinel/services.json"))
    return Topology.model_validate(json.loads(path.read_text()))


async def load_monitor_topology(nacos: Any | None) -> Topology:
    """Read the complete monitoring topology from its own Nacos Data ID."""

    enabled = _as_bool(os.environ.get("NACOS_CONFIG_ENABLED"), default=True)
    required = _as_bool(os.environ.get("NACOS_CONFIG_REQUIRED"), default=False)
    if not enabled:
        return load_topology()
    if nacos is None:
        if required:
            raise RuntimeError("NACOS_SERVER_ADDR is required for monitor topology")
        return load_topology()
    data_id = os.environ.get("NACOS_MONITOR_TOPOLOGY_DATA_ID", "x-sentinel-monitor-topology.json").strip()
    group = (os.environ.get("NACOS_CONFIG_GROUP") or os.environ.get("NACOS_GROUP") or "X_SENTINEL").strip()
    try:
        payload = await nacos.get_json_config(data_id, group=group)
        if payload is None:
            raise ValueError(f"Nacos Config {data_id!r} is absent")
        return Topology.model_validate(payload)
    except Exception as exc:
        if required:
            raise RuntimeError(f"Unable to load monitor topology {data_id!r}: {exc}") from exc
        logger.warning("Unable to load monitor topology %s: %s; using local file", data_id, exc)
        return load_topology()


async def load_monitor_node_id(nacos: Any | None, topology: Topology) -> str:
    """Resolve local identity from a separate Nacos node-address document."""

    enabled = _as_bool(os.environ.get("NACOS_CONFIG_ENABLED"), default=True)
    required = _as_bool(os.environ.get("NACOS_CONFIG_REQUIRED"), default=False)
    address = os.environ.get("NACOS_ADVERTISE_IP", "").strip()
    if enabled and nacos is not None and address:
        data_id = os.environ.get("NACOS_MONITOR_NODES_DATA_ID", "x-sentinel-monitor-nodes.json").strip()
        group = (os.environ.get("NACOS_CONFIG_GROUP") or os.environ.get("NACOS_GROUP") or "X_SENTINEL").strip()
        try:
            payload = await nacos.get_json_config(data_id, group=group)
            if not isinstance(payload, Mapping) or not isinstance(payload.get("nodes"), Mapping):
                raise ValueError("nodes mapping is absent")
            matches = [
                node_id for node_id, entry in payload["nodes"].items()
                if isinstance(entry, Mapping) and entry.get("advertise_ip") == address
            ]
            if len(matches) != 1 or matches[0] not in topology.nodes:
                raise ValueError(f"address {address!r} must map to exactly one topology node")
            return matches[0]
        except Exception as exc:
            if required:
                raise RuntimeError(f"Unable to resolve monitor node from {data_id!r}: {exc}") from exc
            logger.warning("Unable to resolve monitor node from %s: %s; using local identity", data_id, exc)
    elif enabled and required:
        raise RuntimeError("NACOS_ADVERTISE_IP is required for Nacos monitor node mapping")
    node_id = os.environ.get("MONITOR_NODE_ID", "local").strip()
    if node_id not in topology.nodes:
        raise ValueError("MONITOR_NODE_ID is not in monitor topology")
    return node_id




def read_secret(env: str) -> str:
    return Path(os.environ[env]).read_text().strip()
