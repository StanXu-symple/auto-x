"""Lifecycle and health of the independent browser service."""

import asyncio
import json
import logging
import os
import shutil
import socket
import time
import uuid
from contextlib import suppress
from pathlib import Path

from redis.asyncio import Redis

from app.camoufox_executor import CamoufoxExecutor
from app.core.process_stats import ProcessStatsSampler

logger = logging.getLogger(__name__)


class CamoufoxWorker:
    def __init__(self, settings):
        self.settings = settings
        self.worker_id = f"{socket.gethostname()}:{uuid.uuid4().hex}"
        self.redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )
        self.executor = CamoufoxExecutor(settings)
        self.active_tasks = 0
        self.stop_event = asyncio.Event()
        self.process_stats = ProcessStatsSampler(include_children=True)

    async def _wait_for_dependencies(self):
        await self.redis.ping()

    async def _execute_job(self, job):
        return await self.executor.execute(job)

    async def browser_status(self):
        return {
            "installed": shutil.which("xhs") is not None and self.browser_installed(),
            "browser_pool_size": self.executor.browser_pool.size,
            "browser_pool_busy": self.executor.browser_pool.busy_count,
            "browser_pool_limit": self.settings.camoufox_browser_pool_size,
        }

    @staticmethod
    def browser_installed():
        # The Linux image downloads the binary under XDG_CACHE_HOME/camoufox.
        root = Path(os.getenv("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "camoufox"
        return (root / "camoufox-bin").is_file() or (root / "camoufox").is_file()

    async def _heartbeat_loop(self):
        while not self.stop_event.is_set():
            try:
                await self.redis.set(
                    "xsentinel:camoufox-worker:heartbeat",
                    json.dumps(
                        {
                            "worker_id": self.worker_id,
                            "timestamp": time.time(),
                            "active_tasks": self.active_tasks,
                            **await self.browser_status(),
                            **self.process_stats.snapshot(),
                        }
                    ),
                    ex=30,
                )
            except Exception:
                logger.exception("Unable to publish Camoufox heartbeat")
            with suppress(TimeoutError):
                await asyncio.wait_for(self.stop_event.wait(), timeout=10)

    async def close(self):
        await self.executor.close()
