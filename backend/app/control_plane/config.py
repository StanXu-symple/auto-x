import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator


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
        "auth",
        "monitor",
        "frontend",
    ]
    node: str
    project: str = "x-sentinel"
    container_service: str
    port: int = Field(default=8000, ge=1, le=65535)


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


def read_secret(env: str) -> str:
    return Path(os.environ[env]).read_text().strip()
