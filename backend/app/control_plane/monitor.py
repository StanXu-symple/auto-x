import asyncio
import os
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

import httpx
from fastapi import Depends, FastAPI, Request

from app import __version__
from app.control_plane.config import (
    Topology,
    apply_runtime_topology,
    load_runtime_config,
    load_topology,
    runtime_float,
)
from app.control_plane.contracts import ResourceSnapshot
from app.control_plane.nacos import (
    NacosClient,
    NacosServiceRegistration,
    advertise_identity,
)
from app.control_plane.resources import unavailable
from app.control_plane.security import ServiceVerifier, TokenClient


def is_fresh(timestamp: str | None, max_age: float) -> bool:
    try:
        age = (datetime.now(UTC) - datetime.fromisoformat(timestamp or "")).total_seconds()
        return -5 <= age <= max_age
    except (TypeError, ValueError):
        return False


class MonitorCollector:
    def __init__(self, topology: Topology, http: httpx.AsyncClient, tokens: TokenClient):
        self.topology = topology
        self.http = http
        self.tokens = tokens
        self.semaphore = asyncio.Semaphore(topology.concurrency)

    async def collect_node(self, node_id: str) -> list[dict]:
        services = [s for s in self.topology.services if s.node == node_id]
        expected = {s.id: s for s in services}
        async with self.semaphore:
            try:
                token = await self.tokens.token(f"agent:{node_id}")
                agent = await self.tokens.nacos.discover(
                    self.topology.nodes[node_id].agent_service_name
                )
                response = await self.http.get(
                    f"{agent.url}/v1/resources",
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                payload = ResourceSnapshot.model_validate(response.json()).model_dump(mode="python")
                if payload.get("node") != node_id or not is_fresh(
                    payload.get("sampled_at"),
                    self.topology.stale_seconds,
                ):
                    raise ValueError("Agent snapshot expired or wrong node")
                result = []
                seen = set()
                for item in payload["instances"]:
                    service = expected.get(item.get("service_id"))
                    if service is None:
                        continue
                    seen.add(service.id)
                    # The static center configuration is authoritative for identity.
                    item.update(
                        node=node_id,
                        name=service.name,
                        component=service.component,
                        port=service.port,
                    )
                    if item.get("sampled_at") and not is_fresh(
                        item["sampled_at"],
                        self.topology.stale_seconds,
                    ):
                        item.update(unavailable("资源采样已过期"), status="unknown")
                    result.append(item)
                result.extend(
                    self.missing(s, "节点未上报此服务") for s in services if s.id not in seen
                )
                return result
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                return [self.missing(s, "节点不可达或采样已过期") for s in services]

    @staticmethod
    def missing(service, note: str) -> dict:
        return {
            "service_id": service.id,
            "instance_id": service.id,
            "name": service.name,
            "component": service.component,
            "node": service.node,
            "port": service.port,
            "status": "unknown",
            **unavailable(note),
        }

    async def collect(self) -> dict:
        groups = await asyncio.gather(*(self.collect_node(node) for node in self.topology.nodes))
        return {
            "sampled_at": datetime.now(UTC).isoformat(),
            "mode": "microservices",
            "stale_seconds": self.topology.stale_seconds,
            "instances": [item for group in groups for item in group],
        }


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        local_topology = load_topology()
        bootstrap_timeout = runtime_float(
            {},
            "nacos_config_timeout_seconds",
            "NACOS_CONFIG_TIMEOUT_SECONDS",
            3,
            minimum=0.1,
            maximum=30,
        )
        async with httpx.AsyncClient(timeout=bootstrap_timeout, trust_env=False) as bootstrap_http:
            bootstrap_nacos = NacosClient(
                bootstrap_http,
                os.environ["NACOS_SERVER_ADDR"],
                os.environ.get("NACOS_NAMESPACE", "public"),
                os.environ.get("NACOS_GROUP", "X_SENTINEL"),
                os.environ.get("NACOS_USERNAME", ""),
                os.environ.get("NACOS_PASSWORD", ""),
            )
            runtime = await load_runtime_config(bootstrap_nacos)
        topology = apply_runtime_topology(local_topology, runtime)
        app.state.snapshot = {
            "sampled_at": datetime.now(UTC).isoformat(),
            "mode": "microservices",
            "instances": [MonitorCollector.missing(s, "等待首次采样") for s in topology.services],
        }
        async with httpx.AsyncClient(timeout=topology.timeout_seconds, trust_env=False) as http:
            nacos = NacosClient(
                http,
                os.environ["NACOS_SERVER_ADDR"],
                os.environ.get("NACOS_NAMESPACE", "public"),
                os.environ.get("NACOS_GROUP", "X_SENTINEL"),
                os.environ.get("NACOS_USERNAME", ""),
                os.environ.get("NACOS_PASSWORD", ""),
            )
            app.state.verifier = ServiceVerifier(
                None,
                "monitor",
                "monitor:read",
                http=http,
                auth_center_url=os.environ.get("SERVICE_AUTH_URL", ""),
                nacos=nacos,
            )
            ip, port = advertise_identity()
            service_name = os.environ.get("NACOS_SERVICE_NAME", "xsentinel-monitor-center")
            registration = NacosServiceRegistration(
                http,
                nacos,
                service_name,
                ip,
                port,
                metadata={"component": "monitor-center", "version": __version__},
            )
            await registration.start()
            tokens = TokenClient(
                http,
                os.environ.get("SERVICE_AUTH_URL", ""),
                "monitor",
                os.environ["SERVICE_CLIENT_SECRET_FILE"],
                nacos=nacos,
            )
            collector = MonitorCollector(topology, http, tokens)

            async def loop():
                while True:
                    app.state.snapshot = await collector.collect()
                    await asyncio.sleep(topology.interval_seconds)

            task = asyncio.create_task(loop())
            try:
                yield
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                await registration.aclose()

    app = FastAPI(
        title="X Sentinel monitoring center",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    async def authorize(request: Request):
        return await app.state.verifier(request)

    @app.get("/health/live")
    async def live():
        return {"status": "healthy"}

    @app.get("/v1/resources", dependencies=[Depends(authorize)])
    async def resources() -> ResourceSnapshot:
        return ResourceSnapshot.model_validate(app.state.snapshot)

    return app


app = create_app()
