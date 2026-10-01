import asyncio

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.api.deps import CurrentAdmin, DbSession, RedisClient, StreamCurrentAdmin
from app.core.config import get_settings
from app.schemas.system import SystemMetricsResponse
from app.services.runtime_log_client import RuntimeLogUnavailableError, system_logs
from app.services.runtime_logs import LOG_SYSTEMS, sse_event
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
    system: str = Query(
        pattern="^(backend|worker|ai-worker|qq-worker|xhs-worker|camoufox-worker)$"
    ),
    tail: int = Query(default=200, ge=0, le=1000),
) -> StreamingResponse:
    source = system_logs(get_settings(), system, tail=tail)
    try:
        first = await asyncio.wait_for(anext(source), timeout=15)
    except (RuntimeLogUnavailableError, TimeoutError, StopAsyncIteration) as exc:
        await source.aclose()
        detail = (
            str(exc)
            if isinstance(exc, RuntimeLogUnavailableError)
            else "日志服务连接超时或流已关闭"
        )
        raise HTTPException(503, detail) from None

    async def events():
        try:
            yield first
            async for event in source:
                yield event
        except RuntimeLogUnavailableError as exc:
            yield sse_event("error", {"message": str(exc)})
        finally:
            await source.aclose()

    return StreamingResponse(
        events(),
        background=BackgroundTask(source.aclose),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
