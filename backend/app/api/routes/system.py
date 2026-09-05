from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentAdmin, DbSession, RedisClient, StreamCurrentAdmin
from app.schemas.system import SystemMetricsResponse
from app.services.runtime_logs import LOG_SYSTEMS, stream_log
from app.services.system_health import collect_system_metrics

router = APIRouter(prefix="/system", tags=["System"])


@router.get("/metrics", response_model=SystemMetricsResponse)
async def system_metrics(
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
) -> SystemMetricsResponse:
    metrics = await collect_system_metrics(db, redis)
    return SystemMetricsResponse(**metrics)


@router.get("/logs/systems")
async def log_systems(_: CurrentAdmin) -> list[dict[str, str]]:
    return [{"value": value, "label": label} for value, label in LOG_SYSTEMS.items()]


@router.get("/logs/stream", response_class=StreamingResponse)
async def system_log_stream(
    _: StreamCurrentAdmin,
    system: str = Query(pattern="^(backend|worker|ai-worker|qq-worker|xhs-worker)$"),
    tail: int = Query(default=200, ge=0, le=1000),
) -> StreamingResponse:
    return StreamingResponse(
        stream_log(system, tail=tail),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
