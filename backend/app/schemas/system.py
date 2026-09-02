from datetime import datetime
from typing import Any

from app.schemas.common import APIModel


class HealthResponse(APIModel):
    status: str
    service: str = "X Sentinel"
    version: str
    timestamp: datetime
    checks: dict[str, Any] | None = None


class SystemMetricsResponse(APIModel):
    generated_at: datetime
    uptime_seconds: float
    cpu_percent: float
    load_average: list[float] | None
    memory: dict[str, int | float]
    disk: dict[str, int | float]
    process: dict[str, int | float]
    database: dict[str, Any]
    redis: dict[str, Any]
    worker: dict[str, Any]
    ai_worker: dict[str, Any]
    xhs_worker: dict[str, Any]
