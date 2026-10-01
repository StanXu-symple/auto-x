"""Browser execution lives exclusively in camoufox-worker."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

from app.services.x_credentials import decrypt_token
from app.services.xhs_browser_pool import XiaohongshuBrowserPool
from app.services.xhs_jobs import publish_error

logger = logging.getLogger(__name__)
XHS_STAGE_LOG_PREFIX = "XHS_STAGE "
CLI_HOME_ROOT = Path(os.getenv("CAMOUFOX_HOME", "/var/lib/xsentinel/xhs-home"))
CGROUP_MEMORY_ROOT = Path("/sys/fs/cgroup")


def _cgroup_memory_snapshot(root: Path = CGROUP_MEMORY_ROOT) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for filename, key in (
        ("memory.current", "current_bytes"),
        ("memory.peak", "peak_bytes"),
        ("memory.max", "limit_bytes"),
    ):
        try:
            raw = (root / filename).read_text(encoding="ascii").strip()
            snapshot[key] = None if raw == "max" else int(raw)
        except (OSError, ValueError):
            continue
    try:
        events: dict[str, int] = {}
        for line in (root / "memory.events").read_text(encoding="ascii").splitlines():
            name, value = line.split(maxsplit=1)
            events[name] = int(value)
        snapshot["events"] = events
    except (OSError, ValueError):
        pass
    return snapshot


def _oom_kill_count(snapshot: dict[str, Any]) -> int:
    events = snapshot.get("events")
    if not isinstance(events, dict):
        return 0
    try:
        return int(events.get("oom_kill", 0))
    except (TypeError, ValueError):
        return 0


def _cli_executable(args: tuple[str, ...]) -> tuple[str, ...]:
    if args and args[0] == "post":
        return sys.executable, "-m", "app.xhs_cli_compat"
    return ("xhs",)


def _parse_cli_stage_line(line: str) -> dict[str, Any] | None:
    if not line.startswith(XHS_STAGE_LOG_PREFIX):
        return None
    try:
        payload = json.loads(line.removeprefix(XHS_STAGE_LOG_PREFIX))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or not payload.get("message"):
        return None
    return payload


def _strip_cli_stage_lines(output: str) -> str:
    return "\n".join(
        line for line in output.splitlines() if not line.startswith(XHS_STAGE_LOG_PREFIX)
    )


async def _capture_cli_stream(
    stream: asyncio.StreamReader,
    chunks: list[bytes],
    *,
    command: str,
    stream_name: str,
    admin_id: int,
) -> None:
    while line := await stream.readline():
        chunks.append(line)
        decoded = line.decode(errors="replace").rstrip("\r\n")
        stage = _parse_cli_stage_line(decoded)
        if stage is not None:
            details = {
                key: value for key, value in stage.items() if key not in {"level", "message"}
            }
            logger.info(
                str(stage["message"]),
                extra={
                    "command": command,
                    "admin_id": admin_id,
                    **details,
                },
            )
        elif decoded:
            logger.info(
                "Xiaohongshu CLI output",
                extra={
                    "command": command,
                    "admin_id": admin_id,
                    "stream": stream_name,
                    "output": decoded[:4000],
                },
            )


class CamoufoxExecutor:
    def __init__(self, settings):
        self.settings = settings
        self.browser_pool = XiaohongshuBrowserPool(
            root=CLI_HOME_ROOT,
            max_browsers=settings.camoufox_browser_pool_size,
            max_concurrency=settings.camoufox_max_concurrency,
        )

    async def execute(self, job):
        admin_id = job["admin_id"]
        payload = job["payload"]
        if job["operation"] == "x_screenshot":
            return await self.browser_pool.capture_tweet(
                tweet_id=payload["tweet_id"],
                username=payload["username"],
                expected_text=payload["expected_text"],
                expected_media_count=payload.get("expected_media_count", 0),
            )
        cookies = {
            "a1": decrypt_token(payload["encrypted_a1"], self.settings),
            "web_session": decrypt_token(payload["encrypted_web_session"], self.settings),
        }
        if job["operation"] == "login":
            code, out, err = await self._run_cli(
                admin_id,
                "login",
                "--cookie",
                f"a1={cookies['a1']}; web_session={cookies['web_session']}",
            )
            if code:
                raise RuntimeError(err.strip() or out.strip() or "小红书登录失败")
            await self.browser_pool.invalidate(admin_id)
            return {"message": "小红书登录态已保存"}
        if job["operation"] != "post":
            raise ValueError("Unsupported browser operation")
        try:
            result = await self.browser_pool.publish(
                admin_id=admin_id,
                cookie_version=payload["cookie_version"],
                cookie_dict=cookies,
                title=payload["title"],
                content=payload["content"],
                image_paths=payload["images"],
            )
        except Exception as exc:
            raise RuntimeError(publish_error("", str(exc))) from exc
        return {"message": "笔记发布成功", "result": result}

    async def close(self):
        await self.browser_pool.close()

    async def _run_cli(self, admin_id: int, *args: str) -> tuple[int, str, str]:
        if shutil.which("xhs") is None:
            return 127, "", "xhs-cli 未安装"
        home = CLI_HOME_ROOT / "users" / str(admin_id)
        home.mkdir(parents=True, exist_ok=True)
        home.chmod(0o700)
        memory_before = _cgroup_memory_snapshot()
        executable = _cli_executable(args)
        process = await asyncio.create_subprocess_exec(
            *executable,
            *args,
            env={**os.environ, "HOME": str(home)},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        command = args[0] if args else "unknown"
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        stdout_task = asyncio.create_task(
            _capture_cli_stream(
                process.stdout,
                stdout_chunks,
                command=command,
                stream_name="stdout",
                admin_id=admin_id,
            )
        )
        stderr_task = asyncio.create_task(
            _capture_cli_stream(
                process.stderr,
                stderr_chunks,
                command=command,
                stream_name="stderr",
                admin_id=admin_id,
            )
        )
        try:
            await process.wait()
            await asyncio.gather(stdout_task, stderr_task)
        except asyncio.CancelledError:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise
        stdout = b"".join(stdout_chunks)
        stderr = b"".join(stderr_chunks)
        out = stdout.decode(errors="replace")
        err = _strip_cli_stage_lines(stderr.decode(errors="replace"))
        memory_after = _cgroup_memory_snapshot()
        oom_kill_delta = max(
            0,
            _oom_kill_count(memory_after) - _oom_kill_count(memory_before),
        )
        if process.returncode and oom_kill_delta:
            err = (
                f"{err.rstrip()}\nXHS_WORKER_CGROUP_OOM: oom_kill increased by {oom_kill_delta}"
            ).lstrip()
        logger.info(
            "Xiaohongshu CLI finished",
            extra={
                "command": args[0] if args else "unknown",
                "return_code": process.returncode,
                "stdout": out[-4000:],
                "stderr": err[-4000:],
                "cgroup_memory_before": memory_before,
                "cgroup_memory_after": memory_after,
                "cgroup_oom_kill_delta": oom_kill_delta,
            },
        )
        return process.returncode or 0, out, err
