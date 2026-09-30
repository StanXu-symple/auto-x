"""Shared authenticated job transport for XHS orchestration and browser execution."""

import asyncio
import base64
import hashlib
import json
import logging
import socket
import tempfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.responses import Response

from app import __version__
from app.control_plane.nacos import NacosClient, heartbeat_loop
from app.control_plane.security import ServiceVerifier
from app.core.config import Settings, get_settings
from app.schemas.xhs_service import (
    LoginJob,
    PostJob,
    XHSJobRequest,
    XHSJobState,
    XHSServiceStatus,
    XHSVerification,
)
from app.services.article_media import ALLOWED_IMAGE_SUFFIXES, MAX_ARTICLE_IMAGE_BYTES
from app.services.xhs_verification import clear_verification_image, read_verification_image

logger = logging.getLogger(__name__)
RENEW_LOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    redis.call('expire', KEYS[1], ARGV[2])
    redis.call('expire', KEYS[2], ARGV[3])
    return 1
end
return 0
"""
RELEASE_LOCK = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end"


class XHSRuntime:
    def __init__(self, worker, settings: Settings, ip: str, namespace="xhs"):
        self.namespace = namespace
        self.worker = worker
        self.settings = settings
        self.ip = ip
        self.tasks: set[asyncio.Task] = set()
        self.submit_lock = asyncio.Lock()
        self.semaphore = asyncio.Semaphore(settings.xhs_browser_max_concurrency)

    async def submit(self, job: XHSJobRequest, directory, paths: list[str]) -> XHSJobState:
        async with self.submit_lock:
            return await self._submit(job, directory, paths)

    async def _submit(self, job: XHSJobRequest, directory, paths: list[str]) -> XHSJobState:
        key = f"xsentinel:{self.namespace}:http:job:{job.job_id}"
        active_key = f"xsentinel:{self.namespace}:http:active:{job.admin_id}"
        digest = hashlib.sha256(job.model_dump_json().encode())
        for path in paths:
            digest.update(await asyncio.to_thread(Path(path).read_bytes))
        fingerprint = digest.hexdigest()
        old = await self.worker.redis.get(key)
        if old:
            record = json.loads(old)
            if record["fingerprint"] != fingerprint:
                raise HTTPException(409, "Job ID already used with different data")
            return XHSJobState.model_validate(record["result"])
        # Bound pending browser work. Clients may resubmit with the same ID after a 429.
        if len(self.tasks) >= self.settings.xhs_browser_max_concurrency:
            raise HTTPException(429, "XHS worker at capacity", headers={"Retry-After": "5"})
        route = json.dumps(
            {"job_id": job.job_id, "ip": self.ip, "port": self.settings.xhs_service_advertise_port}
        )
        ttl = int(self.settings.xhs_job_timeout_seconds) + 60
        if not await self.worker.redis.set(active_key, route, nx=True, ex=ttl):
            raise HTTPException(409, "Account already has an active job")
        state = XHSJobState(job_id=job.job_id, state="running")
        claimed = False
        try:
            claimed = bool(
                await self.worker.redis.set(
                    key,
                    json.dumps({"fingerprint": fingerprint, "result": state.model_dump()}),
                    nx=True,
                    ex=ttl + self.settings.xhs_job_result_ttl_seconds,
                )
            )
            if not claimed:
                raise HTTPException(409, "Job already accepted")
            task = asyncio.create_task(
                self.execute(job, directory, paths, key, fingerprint, active_key, route)
            )
            directory._xsentinel_handoff = True
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        finally:
            if not claimed:
                await self.worker.redis.eval(RELEASE_LOCK, 1, active_key, route)
        return state

    async def renew(self, key, active_key, route):
        ttl = int(self.settings.xhs_job_timeout_seconds) + 60
        while True:
            await asyncio.sleep(10)
            await self.worker.redis.eval(
                RENEW_LOCK,
                2,
                active_key,
                key,
                route,
                ttl,
                ttl + self.settings.xhs_job_result_ttl_seconds,
            )

    async def execute(self, job, directory, paths, key, fingerprint, active_key, route):
        self.worker.active_tasks += 1
        renewal = asyncio.create_task(self.renew(key, active_key, route))
        try:
            payload = job.payload.model_dump(exclude={"operation", "image_count"})
            if isinstance(job.payload, PostJob):
                payload["images"] = paths
            await asyncio.to_thread(clear_verification_image, job.admin_id)
            async with self.semaphore, asyncio.timeout(self.settings.xhs_job_timeout_seconds):
                result = await self.worker._execute_job(
                    {
                        "job_id": job.job_id,
                        "operation": job.payload.operation,
                        "admin_id": job.admin_id,
                        "payload": payload,
                    }
                )
            state = XHSJobState(job_id=job.job_id, state="succeeded", data=result)
        except (Exception, asyncio.CancelledError):
            logger.exception("XHS HTTP job failed", extra={"job_id": job.job_id})
            state = XHSJobState(
                job_id=job.job_id,
                state="failed",
                error="小红书执行失败或服务重启，请核对发布结果并查看 Worker 日志",
            )
        finally:
            renewal.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await renewal
            self.worker.active_tasks -= 1
            await asyncio.to_thread(directory.cleanup)
        try:
            await self.worker.redis.set(
                key,
                json.dumps({"fingerprint": fingerprint, "result": state.model_dump()}),
                ex=self.settings.xhs_job_result_ttl_seconds,
            )
        finally:
            await self.worker.redis.eval(RELEASE_LOCK, 1, active_key, route)


def create_app(
    settings: Settings | None = None, worker_factory=None, *, browser_service=False
) -> FastAPI:
    namespace = "camoufox" if browser_service else "xhs"
    from app.schemas.camoufox import BrowserJobRequest

    request_model = BrowserJobRequest if browser_service else XHSJobRequest

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from app.db.session import engine
        from app.xhs_worker import UPLOAD_DIR, XiaohongshuWorker

        config = settings or get_settings()
        from app.core.logging import configure_logging

        configure_logging(config.log_level)
        if browser_service:
            from app.camoufox_worker import CamoufoxWorker

            config = config.model_copy(
                update={
                    "xhs_service_name": config.camoufox_service_name,
                    "xhs_service_advertise_ip": config.camoufox_service_advertise_ip,
                    "xhs_service_advertise_port": config.camoufox_service_advertise_port,
                    "xhs_browser_max_concurrency": config.camoufox_max_concurrency,
                    "xhs_job_timeout_seconds": config.camoufox_job_timeout_seconds,
                    "xhs_job_result_ttl_seconds": config.camoufox_job_result_ttl_seconds,
                }
            )
            factory = worker_factory or CamoufoxWorker
        else:
            factory = worker_factory or XiaohongshuWorker
        ip = config.xhs_service_advertise_ip or socket.gethostbyname(socket.gethostname())
        worker = factory(config)
        await worker._wait_for_dependencies()
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        app.state.upload_root = UPLOAD_DIR
        app.state.runtime = XHSRuntime(worker, config, ip, namespace)
        async with httpx.AsyncClient(timeout=5, trust_env=False) as http:
            nacos = NacosClient(
                http,
                config.nacos_server_addr,
                config.nacos_namespace,
                config.nacos_group,
                config.nacos_username,
                config.nacos_password,
            )
            app.state.verifier = ServiceVerifier(
                None,
                f"{namespace}-worker",
                "browser:execute" if browser_service else "xhs:execute",
                http=http,
                auth_center_url=config.service_auth_url,
                nacos=nacos,
            )
            beat = None
            worker_beat = None
            metadata = {"component": f"{namespace}-worker", "version": __version__}
            try:
                await nacos.register(
                    config.xhs_service_name,
                    ip,
                    config.xhs_service_advertise_port,
                    metadata,
                )
                beat = asyncio.create_task(
                    heartbeat_loop(
                        nacos,
                        config.xhs_service_name,
                        ip,
                        config.xhs_service_advertise_port,
                        metadata=metadata,
                    )
                )
                worker_beat = asyncio.create_task(worker._heartbeat_loop())
                yield
            finally:
                if beat:
                    beat.cancel()
                    with suppress(asyncio.CancelledError):
                        await beat
                if worker_beat:
                    worker.stop_event.set()
                    worker_beat.cancel()
                    with suppress(asyncio.CancelledError):
                        await worker_beat
                with suppress(httpx.HTTPError):
                    await nacos.deregister(
                        config.xhs_service_name, ip, config.xhs_service_advertise_port
                    )
                for task in app.state.runtime.tasks:
                    task.cancel()
                await asyncio.gather(*app.state.runtime.tasks, return_exceptions=True)
                await worker.close()
                await worker.redis.aclose()
                await engine.dispose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    async def authorize(request: Request):
        return await app.state.verifier(request)

    @app.get("/health/live")
    async def live():
        return {"status": "healthy"}

    @app.get("/metrics")
    async def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/status", dependencies=[Depends(authorize)])
    async def status() -> XHSServiceStatus:
        worker = app.state.runtime.worker
        browser_status = await worker.browser_status()
        installed = bool(browser_status.get("installed"))
        try:
            await worker.redis.ping()
        except Exception:
            return XHSServiceStatus(
                status="offline", installed=installed, worker_id=worker.worker_id
            )
        return XHSServiceStatus(
            status="online",
            installed=installed,
            worker_id=worker.worker_id,
            active_tasks=worker.active_tasks,
            browser_pool_size=browser_status.get("browser_pool_size", 0),
            browser_pool_busy=browser_status.get("browser_pool_busy", 0),
            browser_pool_limit=browser_status.get("browser_pool_limit", 0),
        )

    @app.post("/v1/jobs", dependencies=[Depends(authorize)], status_code=202)
    async def submit(request: Request) -> XHSJobState:
        # Multipart is parsed only after service authentication. Starlette spools files to disk.
        async with request.form(max_files=18, max_fields=1, max_part_size=65536) as form:
            try:
                job = request_model.model_validate_json(str(form.get("job", "")))
            except ValidationError:
                raise HTTPException(422, "Invalid XHS job") from None
            uploads = form.getlist("images")
            expected = job.payload.image_count if isinstance(job.payload, PostJob) else 0
            if len(uploads) != expected or (isinstance(job.payload, LoginJob) and uploads):
                raise HTTPException(422, "Image count mismatch")
            directory = tempfile.TemporaryDirectory(prefix=".rpc-", dir=app.state.upload_root)
            paths = []
            handed_off = False
            try:
                for i, upload in enumerate(uploads):
                    if not isinstance(upload, UploadFile):
                        raise HTTPException(422, "Invalid image upload")
                    suffix = Path(upload.filename or "").suffix.lower()
                    if suffix not in ALLOWED_IMAGE_SUFFIXES:
                        raise HTTPException(422, "Unsupported image format")
                    path = Path(directory.name) / f"{i}{suffix}"
                    size = 0
                    with path.open("wb") as stream:
                        while chunk := await upload.read(65536):
                            size += len(chunk)
                            if size > MAX_ARTICLE_IMAGE_BYTES:
                                raise HTTPException(413, "Image exceeds 10MB")
                            stream.write(chunk)
                    if not size:
                        raise HTTPException(422, "Empty image")
                    paths.append(str(path))
                result = await app.state.runtime.submit(job, directory, paths)
                # A deduplicated request does not own the original task's temporary files.
                handed_off = bool(getattr(directory, "_xsentinel_handoff", False))
                return result
            finally:
                if not handed_off:
                    directory.cleanup()

    @app.get("/v1/jobs/{job_id}", dependencies=[Depends(authorize)])
    async def result(job_id: str) -> XHSJobState:
        if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            raise HTTPException(422, "Invalid job ID")
        raw = await app.state.runtime.worker.redis.get(f"xsentinel:{namespace}:http:job:{job_id}")
        if not raw:
            raise HTTPException(404, "Job result not found or expired")
        return XHSJobState.model_validate(json.loads(raw)["result"])

    @app.get("/v1/verification/{admin_id}", dependencies=[Depends(authorize)])
    async def verification(admin_id: int, version: str | None = None) -> XHSVerification:
        if admin_id <= 0:
            raise HTTPException(422, "Invalid admin ID")
        item = await asyncio.to_thread(read_verification_image, admin_id)
        if item is None:
            return XHSVerification(required=False)
        image, revision = item
        return XHSVerification(
            required=True,
            version=str(revision),
            image=None
            if (version == str(revision))
            else "data:image/png;base64," + base64.b64encode(image).decode("ascii"),
        )

    if browser_service:

        @app.delete("/v1/browsers/xhs/{admin_id}", dependencies=[Depends(authorize)])
        async def retire_browser(admin_id: int):
            if admin_id <= 0:
                raise HTTPException(422, "Invalid admin ID")
            runtime = app.state.runtime
            if await runtime.worker.redis.get(f"xsentinel:camoufox:http:active:{admin_id}"):
                raise HTTPException(409, "Account has an active browser job")
            await runtime.worker.executor.browser_pool.invalidate(admin_id)
            return {"retired": True, "profile_preserved": True}

    return app
