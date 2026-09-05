from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import socket
import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prometheus_client import start_http_server
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.process_stats import ProcessStatsSampler
from app.db.session import AsyncSessionFactory, engine
from app.services.metrics import (
    XHS_JOB_DURATION,
    XHS_JOBS,
    XHS_QUEUE_DEPTH,
    XHS_WORKER_HEARTBEAT_METRIC,
)
from app.services.x_credentials import decrypt_token
from app.services.xhs_credentials import get_xhs_credentials
from app.services.xhs_jobs import (
    XHS_JOB_QUEUE,
    XHS_WORKER_HEARTBEAT,
    publish_error,
    xhs_response_key,
)

logger = logging.getLogger(__name__)
CLI_HOME_ROOT = Path(os.getenv("XHS_CLI_HOME") or os.getenv("HOME", "/tmp/xsentinel-xhs"))
UPLOAD_DIR = Path(os.getenv("XHS_UPLOAD_DIR", "/var/lib/xsentinel/xhs-uploads"))


def _validated_image_path(image: object) -> Path | None:
    path = Path(str(image)).resolve()
    if path.is_file() and UPLOAD_DIR in path.parents:
        return path
    return None


class XiaohongshuWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )
        self.stop_event = asyncio.Event()
        self.active_tasks = 0
        self.process_stats = ProcessStatsSampler(include_children=True)

    def request_stop(self) -> None:
        self.stop_event.set()

    async def run(self) -> None:
        await self.redis.ping()
        async with AsyncSessionFactory() as session:
            await session.execute(text("SELECT 1"))
        logger.info("X Sentinel Xiaohongshu worker started", extra={"worker_id": self.worker_id})
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            while not self.stop_event.is_set():
                try:
                    item = await self.redis.blpop(XHS_JOB_QUEUE, timeout=1)
                    if item:
                        await self._handle_job(item[1])
                    await self._heartbeat()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Xiaohongshu worker loop failed")
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
            await self.redis.delete(XHS_WORKER_HEARTBEAT)
            await self.redis.aclose()
            await engine.dispose()
            logger.info(
                "X Sentinel Xiaohongshu worker stopped",
                extra={"worker_id": self.worker_id},
            )

    async def _handle_job(self, raw: str) -> None:
        started = time.perf_counter()
        operation = "unknown"
        job_id = ""
        status = "failed"
        self.active_tasks = 1
        try:
            job = json.loads(raw)
            job_id = str(job["job_id"])
            operation = str(job["operation"])
            logger.info(
                "Xiaohongshu job started",
                extra={"job_id": job_id, "operation": operation, "admin_id": job.get("admin_id")},
            )
            data = await asyncio.wait_for(
                self._execute_job(job), timeout=self.settings.xhs_job_timeout_seconds
            )
            result = {"ok": True, "data": data}
            XHS_JOBS.labels(operation=operation, status="success").inc()
            status = "success"
        except Exception as exc:
            logger.exception(
                "Xiaohongshu job failed", extra={"job_id": job_id, "operation": operation}
            )
            result = {"ok": False, "error": str(exc)}
            XHS_JOBS.labels(operation=operation, status="failed").inc()
            status = "failed"
        finally:
            duration = time.perf_counter() - started
            XHS_JOB_DURATION.labels(operation=operation, status=status).observe(duration)
            self.active_tasks = 0
        if job_id:
            await self.redis.set(
                xhs_response_key(job_id),
                json.dumps(result, ensure_ascii=False),
                ex=self.settings.xhs_job_result_ttl_seconds,
            )

    async def _execute_job(self, job: dict[str, Any]) -> dict[str, Any]:
        operation = str(job["operation"])
        admin_id = int(job["admin_id"])
        payload = job.get("payload") or {}
        if operation == "login":
            a1 = decrypt_token(str(payload["encrypted_a1"]), self.settings)
            web_session = decrypt_token(str(payload["encrypted_web_session"]), self.settings)
            code, out, err = await self._run_cli(
                admin_id, "login", "--cookie", f"a1={a1}; web_session={web_session}"
            )
            if code:
                raise RuntimeError(err.strip() or out.strip() or "小红书登录失败")
            return {"message": "小红书登录态验证成功"}
        if operation == "post":
            return await self._post(admin_id, payload)
        raise ValueError(f"Unsupported Xiaohongshu operation: {operation}")

    async def _post(self, admin_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        async with AsyncSessionFactory() as session:
            credentials = await get_xhs_credentials(session, self.settings, admin_id=admin_id)
        if credentials is None:
            raise RuntimeError("请先保存小红书登录态")
        code, out, err = await self._run_cli(
            admin_id,
            "login",
            "--cookie",
            f"a1={credentials.a1}; web_session={credentials.web_session}",
        )
        if code:
            raise RuntimeError(err.strip() or out.strip() or "恢复小红书登录态失败")

        args = ["post", str(payload["title"]), "--content", str(payload["content"])]
        for image in payload.get("images") or []:
            path = await asyncio.to_thread(_validated_image_path, image)
            if path is None:
                raise RuntimeError("图片路径无效")
            args.extend(["--image", str(path)])
        code, out, err = await self._run_cli(admin_id, *args, "--json")
        if code:
            raise RuntimeError(publish_error(out, err))
        try:
            cli_result = json.loads(out)
        except json.JSONDecodeError:
            cli_result = {"raw": out.strip()}
        return {"message": "笔记发布成功", "result": cli_result}

    async def _run_cli(self, admin_id: int, *args: str) -> tuple[int, str, str]:
        if shutil.which("xhs") is None:
            return 127, "", "xhs-cli 未安装"
        home = CLI_HOME_ROOT / "users" / str(admin_id)
        home.mkdir(parents=True, exist_ok=True)
        home.chmod(0o700)
        process = await asyncio.create_subprocess_exec(
            "xhs",
            *args,
            env={**os.environ, "HOME": str(home)},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = await process.communicate()
        except asyncio.CancelledError:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.communicate(), timeout=5)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.communicate()
            raise
        out = stdout.decode(errors="replace")
        err = stderr.decode(errors="replace")
        logger.info(
            "Xiaohongshu CLI finished",
            extra={
                "command": args[0] if args else "unknown",
                "return_code": process.returncode,
                "stdout": out[-4000:],
                "stderr": err[-4000:],
            },
        )
        return process.returncode or 0, out, err

    async def _heartbeat(self) -> None:
        now = datetime.now(UTC)
        queue_depth = int(await self.redis.llen(XHS_JOB_QUEUE))
        XHS_QUEUE_DEPTH.set(queue_depth)
        payload = {
            "worker_id": self.worker_id,
            "timestamp": now.isoformat().replace("+00:00", "Z"),
            "last_heartbeat": now.isoformat().replace("+00:00", "Z"),
            "active_tasks": self.active_tasks,
            "queue_depth": queue_depth,
            "installed": shutil.which("xhs") is not None,
            **self.process_stats.snapshot(),
        }
        await self.redis.set(
            XHS_WORKER_HEARTBEAT,
            json.dumps(payload),
            ex=self.settings.xhs_worker_heartbeat_ttl_seconds,
        )
        XHS_WORKER_HEARTBEAT_METRIC.set(now.timestamp())

    async def _heartbeat_loop(self) -> None:
        interval = max(3.0, self.settings.xhs_worker_heartbeat_ttl_seconds / 3)
        while not self.stop_event.is_set():
            try:
                await self._heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Xiaohongshu worker heartbeat failed")
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=interval)
            except TimeoutError:
                pass


async def async_main() -> None:
    worker = XiaohongshuWorker(get_settings())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.request_stop)
    await worker.run()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.xhs_worker_metrics_port:
        start_http_server(settings.xhs_worker_metrics_port, addr="0.0.0.0")
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
