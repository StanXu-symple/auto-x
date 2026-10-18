"""Shared authenticated job transport for XHS orchestration and browser execution."""

import asyncio
import base64
import hashlib
import json
import logging
import os
import socket
import tempfile
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
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
from app.services.browser_screenshot_artifacts import (
    artifact_path,
    expire_artifacts,
    store_artifact,
)
from app.services.runtime_logs import log_path, stream_log
from app.services.xhs_jobs import (
    XHSJobFailedError,
    XHSWorkerUnavailableError,
    safe_job_error,
)
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
    def __init__(
        self, worker, settings: Settings, ip: str, namespace="xhs", artifact_root=None,
        *, store=None,
    ):
        self.namespace = namespace
        self.worker = worker
        self.settings = settings
        self.ip = ip
        self.store = store
        self.tasks: set[asyncio.Task] = set()
        self.inflight_job_ids: set[str] = set()
        self.submit_lock = asyncio.Lock()
        self.semaphore = asyncio.Semaphore(settings.xhs_browser_max_concurrency)
        self.artifact_root = artifact_root or (
            Path(os.getenv("XHS_UPLOAD_DIR", "/var/lib/xsentinel/camoufox-uploads"))
            / ".screenshots"
        )

    async def expire_screenshots(self):
        while True:
            try:
                await asyncio.to_thread(
                    expire_artifacts,
                    self.artifact_root,
                    self.settings.xhs_job_result_ttl_seconds + 120,
                )
            except OSError:
                logger.exception("Unable to expire browser screenshot artifacts")
            await asyncio.sleep(60)

    async def submit(self, job: XHSJobRequest, directory, paths: list[str]) -> XHSJobState:
        async with self.submit_lock:
            return await self._submit(job, directory, paths)

    async def _submit(self, job: XHSJobRequest, directory, paths: list[str]) -> XHSJobState:
        key = f"xsentinel:{self.namespace}:http:job:{job.job_id}"
        account_key = "x-screenshot" if job.payload.operation == "x_screenshot" else job.admin_id
        active_key = f"xsentinel:{self.namespace}:http:active:{account_key}"
        digest = hashlib.sha256(job.model_dump_json().encode())
        for path in paths:
            digest.update(await asyncio.to_thread(Path(path).read_bytes))
        fingerprint = digest.hexdigest()
        durable = self.store is not None and job.payload.operation == "post"
        if durable:
            old_row = await self.store.get(self.namespace, job.job_id)
            if old_row is not None:
                if old_row.fingerprint != fingerprint:
                    raise HTTPException(409, "Job ID already used with different data")
                return self.row_state(old_row)
        old = await self.worker.redis.get(key)
        if old:
            record = json.loads(old)
            if record["fingerprint"] != fingerprint:
                raise HTTPException(409, "Job ID already used with different data")
            return XHSJobState.model_validate(record["result"])
        # Bound pending browser work. Clients may resubmit with the same ID after a 429.
        if len(self.tasks) >= self.settings.xhs_browser_max_concurrency:
            raise HTTPException(429, "Browser worker at capacity", headers={"Retry-After": "5"})
        route = json.dumps(
            {"job_id": job.job_id, "ip": self.ip, "port": self.settings.xhs_service_advertise_port}
        )
        ttl = int(self.settings.xhs_job_timeout_seconds) + 60
        if not await self.worker.redis.set(active_key, route, nx=True, ex=ttl):
            raise HTTPException(409, "Account already has an active job")
        state = XHSJobState(job_id=job.job_id, state="running")
        claimed = False
        handed_off = False
        created = False
        try:
            if durable:
                try:
                    row, created = await self.store.accept(
                        self.namespace, job.job_id, job.admin_id, fingerprint,
                        worker_id=self.worker.worker_id, lease_seconds=ttl,
                    )
                except ValueError:
                    raise HTTPException(409, "Job ID already used with different data") from None
                if not created:
                    if row.fingerprint != fingerprint:
                        raise HTTPException(409, "Job ID already used with different data")
                    return self.row_state(row)
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
            handed_off = True
            self.tasks.add(task)
            self.inflight_job_ids.add(job.job_id)
            task.add_done_callback(self.tasks.discard)
            task.add_done_callback(lambda _: self.inflight_job_ids.discard(job.job_id))
        finally:
            if not handed_off:
                if created:
                    # We have not handed the payload to an executor, so this
                    # failure is definite even if the Redis write failed.
                    await self.store.finish(
                        self.namespace, job.job_id, "failed",
                        error="任务未能启动，尚未执行小红书发布",
                    )
                await self.worker.redis.eval(RELEASE_LOCK, 1, active_key, route)
        return state

    async def renew(self, key, active_key, route, job=None):
        ttl = int(self.settings.xhs_job_timeout_seconds) + 60
        while True:
            await asyncio.sleep(10)
            try:
                await self.worker.redis.eval(
                    RENEW_LOCK,
                    2,
                    active_key,
                    key,
                    route,
                    ttl,
                    ttl + self.settings.xhs_job_result_ttl_seconds,
                )
            except Exception:
                logger.warning("Unable to renew browser active-job cache")
            if self.store is not None and job is not None and job.payload.operation == "post":
                try:
                    await self.store.renew(
                        self.namespace, job.job_id, self.worker.worker_id, ttl
                    )
                except Exception:
                    logger.warning("Unable to renew browser result lease", extra={
                        "job_id": job.job_id
                    })

    async def execute(self, job, directory, paths, key, fingerprint, active_key, route):
        self.worker.active_tasks += 1
        renewal = asyncio.create_task(self.renew(key, active_key, route, job))
        try:
            payload = job.payload.model_dump(exclude={"operation", "image_count"})
            if isinstance(job.payload, PostJob):
                payload["images"] = paths
            if job.payload.operation != "x_screenshot":
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
                if job.payload.operation == "x_screenshot":
                    result = await asyncio.to_thread(
                        store_artifact, self.artifact_root, job.job_id, result
                    )
            state = XHSJobState(job_id=job.job_id, state="succeeded", data=result)
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning(
                "Browser HTTP job did not complete normally",
                extra={"job_id": job.job_id, "error_type": type(exc).__name__,
                       "error": safe_job_error(str(exc))},
            )
            state = self.failure_state(job, exc)
        finally:
            self.worker.active_tasks -= 1
            with suppress(OSError):
                await asyncio.to_thread(directory.cleanup)
        try:
            if self.store is not None and job.payload.operation == "post":
                # Retain the observed outcome in this task while PostgreSQL is
                # transiently unavailable. Never replace a known success with
                # an execution failure because its persistence failed.
                while True:
                    try:
                        row = await self.store.finish(
                            self.namespace, job.job_id, state.state,
                            data=state.data, error=state.error,
                        )
                        break
                    except Exception:
                        logger.exception(
                            "Unable to persist browser completion; retrying",
                            extra={"job_id": job.job_id},
                        )
                        await asyncio.sleep(5)
                state = self.row_state(row)
            try:
                await self.worker.redis.set(
                    key,
                    json.dumps({"fingerprint": fingerprint, "result": state.model_dump()}),
                    ex=self.settings.xhs_job_result_ttl_seconds,
                )
            except Exception:
                logger.exception("Unable to cache browser completion", extra={"job_id": job.job_id})
        finally:
            renewal.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await renewal
            with suppress(Exception):
                await self.worker.redis.eval(RELEASE_LOCK, 1, active_key, route)

    @staticmethod
    def row_state(row) -> XHSJobState:
        return XHSJobState(
            job_id=row.job_id, state=row.state, data=row.data or {}, error=row.error,
            failure_confirmed=row.state == "failed",
        )

    def failure_state(self, job, exc) -> XHSJobState:
        if job.payload.operation != "post":
            return XHSJobState(
                job_id=job.job_id, state="failed",
                error=safe_job_error(str(exc)) or "任务超时或服务重启",
            )
        if isinstance(exc, XHSJobFailedError):
            return XHSJobState(
                job_id=job.job_id, state="failed",
                error=safe_job_error(str(exc)) or "小红书已确认发布失败",
                failure_confirmed=True,
            )
        # A transport exception does not mean that the browser did not publish.
        # The XHS worker will query the SAME browser job until its final result
        # is available; it never recreates a post as part of reconciliation.
        if self.namespace == "xhs":
            return XHSJobState(
                job_id=job.job_id, state="reconciling",
                error="正在自动获取浏览器的最终发布结果，请勿重复发布",
            )
        return XHSJobState(
            job_id=job.job_id, state="uncertain",
            error="浏览器执行中断，未取得平台的明确发布结果，请勿重复发布",
        )

    async def job_state(self, job_id: str) -> XHSJobState | None:
        if self.store is not None:
            row = await self.store.get(self.namespace, job_id)
            if row is not None:
                return self.row_state(row)
        raw = await self.worker.redis.get(f"xsentinel:{self.namespace}:http:job:{job_id}")
        return XHSJobState.model_validate(json.loads(raw)["result"]) if raw else None

    async def recover_once(self):
        """Recover accepted posts after a lost response or a worker restart."""
        if self.store is None:
            return
        for row in await self.store.recovery_jobs(self.namespace):
            if row.job_id in self.inflight_job_ids:
                continue
            if self.namespace == "camoufox":
                # No living lease and no execution owned by this process. The
                # browser may have clicked Publish before the process exited.
                if row.state == "running":
                    await self.store.finish(
                        self.namespace, row.job_id, "uncertain",
                        error="浏览器服务执行中断，平台发布结果尚未确认，请勿重复发布",
                        require_expired_lease=True,
                    )
                continue
            # The result journal is shared across replicas. A browser may have
            # persisted its successful completion and then stopped before any
            # HTTP caller received it; no live browser instance is needed to
            # recover that already verified result.
            browser_row = await self.store.get("camoufox", row.job_id)
            if (browser_row is not None and browser_row.admin_id == row.admin_id
                and browser_row.state in {
                "succeeded", "failed", "uncertain"
            }):
                await self.store.finish(
                    self.namespace, row.job_id, browser_row.state,
                    data=browser_row.data, error=browser_row.error,
                    require_expired_lease=row.state == "running",
                )
                continue
            try:
                state = await self.worker.browser_client.job_status(
                    job_id=row.job_id, admin_id=row.admin_id
                )
            except XHSWorkerUnavailableError:
                # Keep the durable job for the next scan. No publication retry.
                continue
            if state is None:
                await self.store.finish(
                    self.namespace, row.job_id, "uncertain",
                    error="浏览器未返回原任务记录，无法确认平台发布结果，请勿重复发布",
                    require_expired_lease=row.state == "running",
                )
            elif state.state in {"succeeded", "failed", "uncertain"}:
                await self.store.finish(
                    self.namespace, row.job_id,
                    "uncertain" if state.state == "failed" and not state.failure_confirmed
                    else state.state,
                    data=state.data, error=state.error,
                    require_expired_lease=row.state == "running",
                )
            elif row.state == "running":
                await self.store.finish(
                    self.namespace, row.job_id, "reconciling",
                    error="浏览器仍在执行原任务，正在自动等待最终结果",
                    require_expired_lease=True,
                )

    async def maintenance(self, completion_delivery=None):
        async def loop(action):
            while True:
                try:
                    await action()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Unable to recover browser results or deliver completion")
                await asyncio.sleep(5)

        tasks = [asyncio.create_task(loop(self.recover_once))]
        if completion_delivery is not None:
            # An unreachable old browser task must not delay callbacks for
            # other posts whose final outcome is already known.
            tasks.append(asyncio.create_task(loop(completion_delivery.deliver_once)))
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def create_app(
    settings: Settings | None = None, worker_factory=None, *, browser_service=False
) -> FastAPI:
    namespace = "camoufox" if browser_service else "xhs"
    from app.schemas.camoufox import BrowserJobRequest

    request_model = BrowserJobRequest if browser_service else XHSJobRequest

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from app.db.session import AsyncSessionFactory, engine
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
        from app.services.browser_job_store import BrowserJobStore
        from app.services.xhs_completion_delivery import XHSCompletionDelivery
        app.state.job_store = BrowserJobStore(AsyncSessionFactory)
        app.state.runtime = XHSRuntime(
            worker, config, ip, namespace, UPLOAD_DIR / ".screenshots",
            store=app.state.job_store,
        )
        app.state.completion_delivery = (
            XHSCompletionDelivery(config, app.state.job_store)
            if namespace == "xhs" else None
        )
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
            app.state.log_verifier = ServiceVerifier(
                None,
                f"{namespace}-worker",
                "logs:read",
                http=http,
                auth_center_url=config.service_auth_url,
                nacos=nacos,
            )
            beat = None
            worker_beat = None
            artifact_gc = None
            maintenance = asyncio.create_task(
                app.state.runtime.maintenance(app.state.completion_delivery)
            )
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
                if browser_service:
                    artifact_gc = asyncio.create_task(app.state.runtime.expire_screenshots())
                yield
            finally:
                maintenance.cancel()
                with suppress(asyncio.CancelledError):
                    await maintenance
                if app.state.completion_delivery:
                    await app.state.completion_delivery.close()
                if artifact_gc:
                    artifact_gc.cancel()
                    with suppress(asyncio.CancelledError):
                        await artifact_gc
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

    async def authorize_logs(request: Request):
        return await app.state.log_verifier(request)

    @app.get("/v1/logs/stream")
    async def logs(
        claims: dict = Depends(authorize_logs),
        tail: int = Query(default=200, ge=0, le=1000),
    ):
        # Reconnect with a fresh token and rediscover replicas at least every 45s.
        duration = min(45.0, float(claims["exp"]) - time.time() - 1)
        if duration <= 0:
            raise HTTPException(401, "Service token expired")
        if not await asyncio.to_thread(log_path(f"{namespace}-worker").is_file):
            raise HTTPException(503, "Worker 日志文件尚未就绪，请检查日志卷")

        async def events():
            source = stream_log(f"{namespace}-worker", tail=tail)
            try:
                async with asyncio.timeout(duration):
                    async for event in source:
                        yield event
            except TimeoutError:
                pass
            finally:
                await source.aclose()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

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
        async with request.form(max_files=18, max_fields=1, max_part_size=262144) as form:
            try:
                job = request_model.model_validate_json(str(form.get("job", "")))
            except ValidationError:
                raise HTTPException(422, "Invalid browser job") from None
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
        state = await app.state.runtime.job_state(job_id)
        if state is None:
            raise HTTPException(404, "Job result not found or expired")
        return state

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

        @app.get("/v1/jobs/{job_id}/screenshot", dependencies=[Depends(authorize)])
        async def screenshot_result(job_id: str):
            try:
                path = artifact_path(app.state.runtime.artifact_root, job_id)
            except ValueError:
                raise HTTPException(422, "Invalid job ID") from None
            state = await result(job_id)
            if state.state != "succeeded" or not state.data.get("screenshot_ready"):
                raise HTTPException(404, "Screenshot is not available")
            if not path.is_file():
                raise HTTPException(404, "Screenshot expired or browser instance restarted")
            return FileResponse(
                path,
                media_type="image/png",
                filename=f"{state.data['tweet_id']}.png",
                headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
            )

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
