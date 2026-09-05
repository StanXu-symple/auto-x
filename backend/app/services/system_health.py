import json
import os
import time
from datetime import UTC, datetime
from typing import Any

import psutil
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.process_stats import ProcessStatsSampler

PROCESS_STARTED_MONOTONIC = time.monotonic()
PROCESS = psutil.Process(os.getpid())
PROCESS_STATS = ProcessStatsSampler()
# Prime delta-based counters so the first dashboard request is meaningful.
PROCESS.cpu_percent(interval=None)
psutil.cpu_percent(interval=None)


class CumulativeCPUSampler:
    def __init__(self) -> None:
        self.samples: dict[str, tuple[float, float]] = {}

    def sample(self, key: str, cpu_seconds: float, *, now: float | None = None) -> float | None:
        sampled_at = time.monotonic() if now is None else now
        previous = self.samples.get(key)
        self.samples[key] = (cpu_seconds, sampled_at)
        if previous is None:
            return None
        previous_cpu, previous_at = previous
        elapsed = sampled_at - previous_at
        cpu_delta = cpu_seconds - previous_cpu
        if elapsed <= 0 or cpu_delta < 0:
            return None
        return round((cpu_delta / elapsed) * 100, 2)


CPU_SAMPLER = CumulativeCPUSampler()


async def _database_resources(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(
        text(
            "SHOW GLOBAL STATUS WHERE Variable_name IN "
            "('Cpu_time', 'Innodb_buffer_pool_bytes_data')"
        )
    )
    values = {str(row[0]).lower(): str(row[1]) for row in result}
    total_bytes = int(await session.scalar(text("SELECT @@innodb_buffer_pool_size")) or 0)
    used_bytes = int(values.get("innodb_buffer_pool_bytes_data", 0))
    cpu_time_ms = float(values["cpu_time"]) if "cpu_time" in values else None
    return {
        "cpu_percent": (
            CPU_SAMPLER.sample("mysql", cpu_time_ms / 1000) if cpu_time_ms is not None else None
        ),
        "memory_used_bytes": used_bytes,
        "memory_total_bytes": total_bytes,
        "memory_percent": round((used_bytes / total_bytes) * 100, 2) if total_bytes else None,
    }


async def _worker_status(redis: Redis, key: str) -> dict[str, Any]:
    raw = await redis.get(key)
    ttl = await redis.ttl(key)
    if not raw:
        return {"status": "offline"}
    heartbeat = json.loads(raw)
    heartbeat.setdefault("last_heartbeat", heartbeat.get("timestamp"))
    heartbeat["status"] = "online"
    heartbeat["ttl_seconds"] = ttl
    return heartbeat


async def collect_system_metrics(session: AsyncSession, redis: Redis) -> dict[str, Any]:
    process = PROCESS
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    try:
        load_average: list[float] | None = [float(item) for item in os.getloadavg()]
    except (AttributeError, OSError):
        load_average = None

    database_status: dict[str, Any]
    db_started = time.perf_counter()
    try:
        await session.execute(text("SELECT 1"))
        database_status = {
            "status": "healthy",
            "latency_ms": round((time.perf_counter() - db_started) * 1000, 2),
        }
    except Exception as exc:
        database_status = {"status": "unhealthy", "error": str(exc)[:300]}
        await session.rollback()
    if database_status["status"] == "healthy":
        try:
            database_status.update(await _database_resources(session))
        except Exception as exc:
            database_status["resource_error"] = str(exc)[:300]
            await session.rollback()

    redis_status: dict[str, Any]
    redis_started = time.perf_counter()
    try:
        await redis.ping()
        info = await redis.info()
        used_memory = int(info.get("used_memory", 0))
        max_memory = int(info.get("maxmemory", 0))
        cpu_seconds = float(info.get("used_cpu_sys", 0)) + float(
            info.get("used_cpu_user", 0)
        )
        redis_status = {
            "status": "healthy",
            "latency_ms": round((time.perf_counter() - redis_started) * 1000, 2),
            "used_memory": used_memory,
            "memory_used_bytes": used_memory,
            "memory_total_bytes": max_memory or None,
            "memory_percent": (
                round((used_memory / max_memory) * 100, 2) if max_memory else None
            ),
            "cpu_percent": CPU_SAMPLER.sample("redis", cpu_seconds),
        }
    except Exception as exc:
        redis_status = {"status": "unhealthy", "error": str(exc)[:300]}

    worker_status: dict[str, Any] = {"status": "offline"}
    try:
        worker_status = await _worker_status(redis, "xsentinel:worker:heartbeat")
    except Exception as exc:
        worker_status = {"status": "unknown", "error": str(exc)[:300]}

    ai_worker_status: dict[str, Any] = {"status": "offline"}
    try:
        ai_worker_status = await _worker_status(redis, "xsentinel:ai-worker:heartbeat")
    except Exception as exc:
        ai_worker_status = {"status": "unknown", "error": str(exc)[:300]}


    qq_worker_status: dict[str, Any] = {"status": "offline"}
    try:
        qq_worker_status = await _worker_status(redis, "xsentinel:qq-worker:heartbeat")
    except Exception as exc:
        qq_worker_status = {"status": "unknown", "error": str(exc)[:300]}

    xhs_worker_status: dict[str, Any] = {"status": "offline"}
    try:
        xhs_worker_status = await _worker_status(redis, "xsentinel:xhs-worker:heartbeat")
    except Exception as exc:
        xhs_worker_status = {"status": "unknown", "error": str(exc)[:300]}

    with process.oneshot():
        process_metrics = {
            **PROCESS_STATS.snapshot(),
            "threads": process.num_threads(),
            "open_files": len(process.open_files()),
        }

    return {
        "generated_at": datetime.now(UTC),
        "uptime_seconds": round(time.monotonic() - PROCESS_STARTED_MONOTONIC, 2),
        "cpu_percent": psutil.cpu_percent(interval=None),
        "load_average": load_average,
        "memory": {
            "total_bytes": memory.total,
            "available_bytes": memory.available,
            "used_bytes": memory.used,
            "percent": memory.percent,
        },
        "disk": {
            "total_bytes": disk.total,
            "free_bytes": disk.free,
            "used_bytes": disk.used,
            "percent": disk.percent,
        },
        "process": process_metrics,
        "api": {
            "status": "healthy",
            "uptime_seconds": round(time.monotonic() - PROCESS_STARTED_MONOTONIC, 2),
            **process_metrics,
        },
        "database": database_status,
        "redis": redis_status,
        "worker": worker_status,
        "ai_worker": ai_worker_status,
        "qq_worker": qq_worker_status,
        "xhs_worker": xhs_worker_status,
    }
