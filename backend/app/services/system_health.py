import json
import os
import time
from datetime import UTC, datetime
from typing import Any

import psutil
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PROCESS_STARTED_MONOTONIC = time.monotonic()
PROCESS = psutil.Process(os.getpid())
# Prime delta-based counters so the first dashboard request is meaningful.
PROCESS.cpu_percent(interval=None)
psutil.cpu_percent(interval=None)


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

    redis_status: dict[str, Any]
    redis_started = time.perf_counter()
    try:
        await redis.ping()
        info = await redis.info("memory")
        redis_status = {
            "status": "healthy",
            "latency_ms": round((time.perf_counter() - redis_started) * 1000, 2),
            "used_memory": int(info.get("used_memory", 0)),
        }
    except Exception as exc:
        redis_status = {"status": "unhealthy", "error": str(exc)[:300]}

    worker_status: dict[str, Any] = {"status": "offline"}
    try:
        raw = await redis.get("xsentinel:worker:heartbeat")
        ttl = await redis.ttl("xsentinel:worker:heartbeat")
        if raw:
            heartbeat = json.loads(raw)
            heartbeat.setdefault("last_heartbeat", heartbeat.get("timestamp"))
            worker_status = {"status": "online", "ttl_seconds": ttl, **heartbeat}
    except Exception as exc:
        worker_status = {"status": "unknown", "error": str(exc)[:300]}

    ai_worker_status: dict[str, Any] = {"status": "offline"}
    try:
        raw = await redis.get("xsentinel:ai-worker:heartbeat")
        ttl = await redis.ttl("xsentinel:ai-worker:heartbeat")
        if raw:
            heartbeat = json.loads(raw)
            heartbeat.setdefault("last_heartbeat", heartbeat.get("timestamp"))
            heartbeat["worker_state"] = heartbeat.get("status")
            ai_worker_status = {**heartbeat, "status": "online", "ttl_seconds": ttl}
    except Exception as exc:
        ai_worker_status = {"status": "unknown", "error": str(exc)[:300]}

    with process.oneshot():
        process_metrics = {
            "pid": process.pid,
            "cpu_percent": process.cpu_percent(),
            "rss_bytes": process.memory_info().rss,
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
        "database": database_status,
        "redis": redis_status,
        "worker": worker_status,
        "ai_worker": ai_worker_status,
    }
