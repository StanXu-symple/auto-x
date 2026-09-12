import asyncio
import json
import os
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request

from app import __version__
from app.control_plane.config import (
    Service,
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
from app.control_plane.resources import docker_resources, unavailable
from app.control_plane.security import ServiceVerifier


class DockerCollector:
    def __init__(self, topology: Topology, node_id: str, http: httpx.AsyncClient):
        self.topology = topology
        self.node_id = node_id
        self.http = http
        self.services = [s for s in topology.services if s.node == node_id]
        self.previous: dict[str, dict] = {}
        self.semaphore = asyncio.Semaphore(topology.concurrency)
        self.api = ""

    async def collect_service(self, service: Service) -> list[dict]:
        async with self.semaphore:
            base = {
                "service_id": service.id,
                "name": service.name,
                "component": service.component,
                "node": self.node_id,
                "port": service.port,
            }
            try:
                filters = {
                    "label": [
                        f"com.docker.compose.project={service.project}",
                        f"com.docker.compose.service={service.container_service}",
                    ]
                }
                response = await self.http.get(
                    f"{self.api}/containers/json",
                    params={
                        "all": "true",
                        "filters": json.dumps(filters),
                    },
                )
                response.raise_for_status()
                containers = response.json()
                containers = [
                    c
                    for c in containers
                    if c.get("Labels", {}).get("com.docker.compose.oneoff", "false").lower()
                    != "true"
                ]
                if not containers:
                    return [
                        {
                            **base,
                            "instance_id": service.id,
                            "status": "offline",
                            **unavailable("未找到配置对应的容器"),
                        }
                    ]
                reachable = True

                async def sample(container: dict) -> dict:
                    container_id = container["Id"]
                    item = {
                        **base,
                        "instance_id": container_id,
                        "endpoint_reachable": reachable,
                        "container_name": next(iter(container.get("Names", [])), "").lstrip("/"),
                    }
                    if container.get("State") != "running":
                        return {**item, "status": "offline", **unavailable("容器未运行")}
                    try:
                        response = await self.http.get(
                            f"{self.api}/containers/{container_id}/stats",
                            params={"stream": "false", "one-shot": "true"},
                        )
                        response.raise_for_status()
                        stats = response.json()
                        metrics = docker_resources(stats, self.previous.get(container_id))
                        self.previous[container_id] = stats.get("cpu_stats", {})
                        return {
                            **item,
                            **metrics,
                            "status": "unhealthy"
                            if ("(unhealthy)" in container.get("Status", "") or not reachable)
                            else "healthy",
                            "sampled_at": datetime.now(UTC).isoformat(),
                        }
                    except (httpx.HTTPError, ValueError, KeyError):
                        return {**item, "status": "unknown", **unavailable("容器资源采集失败")}

                # Bound work even if a service has many replicas.
                return [await sample(container) for container in containers]
            except (httpx.HTTPError, ValueError, KeyError):
                return [
                    {
                        **base,
                        "instance_id": service.id,
                        "status": "unknown",
                        **unavailable("Docker 采集不可用"),
                    }
                ]

    async def collect(self) -> dict:
        # Negotiate Docker API version instead of depending on the daemon's default API.
        if not self.api:
            response = await self.http.get("/version")
            response.raise_for_status()
            self.api = f"/v{response.json()['ApiVersion']}"
        groups = await asyncio.gather(*(self.collect_service(s) for s in self.services))
        instances = [item for group in groups for item in group]
        active_ids = {item["instance_id"] for item in instances}
        self.previous = {key: value for key, value in self.previous.items() if key in active_ids}
        return ResourceSnapshot(
            node=self.node_id, sampled_at=datetime.now(UTC).isoformat(), instances=instances
        ).model_dump(mode="json")


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
        node = os.environ.get("MONITOR_NODE_ID", "local")
        if node not in topology.nodes:
            raise ValueError("MONITOR_NODE_ID is not in the static topology")
        async with httpx.AsyncClient(
            timeout=topology.timeout_seconds, trust_env=False
        ) as http_nacos:
            nacos = NacosClient(
                http_nacos,
                os.environ["NACOS_SERVER_ADDR"],
                os.environ.get("NACOS_NAMESPACE", "public"),
                os.environ.get("NACOS_GROUP", "X_SENTINEL"),
                os.environ.get("NACOS_USERNAME", ""),
                os.environ.get("NACOS_PASSWORD", ""),
            )
            app.state.verifier = ServiceVerifier(
                None,
                f"agent:{node}",
                "resources:read",
                http=http_nacos,
                auth_center_url=os.environ.get("SERVICE_AUTH_URL", ""),
                nacos=nacos,
            )
            ip, port = advertise_identity()
            service_name = os.environ.get(
                "NACOS_SERVICE_NAME", f"xsentinel-monitor-agent-{node}"
            )
            registration = NacosServiceRegistration(
                http_nacos,
                nacos,
                service_name,
                ip,
                port,
                metadata={"component": "monitor-agent", "version": __version__},
            )
            await registration.start()
            app.state.snapshot = None
            transport = httpx.AsyncHTTPTransport(
                uds=os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://docker",
                timeout=topology.timeout_seconds,
                trust_env=False,
            ) as http:
                collector = DockerCollector(topology, node, http)

                async def loop():
                    while True:
                        try:
                            app.state.snapshot = await collector.collect()
                        except (httpx.HTTPError, ValueError, KeyError):
                            app.state.snapshot = None
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
        title="X Sentinel node agent",
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
        if app.state.snapshot is None:
            raise HTTPException(503, "Docker resource snapshot unavailable")
        return ResourceSnapshot.model_validate(app.state.snapshot)

    return app


app = create_app()
