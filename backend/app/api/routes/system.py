from fastapi import APIRouter

from app.api.deps import CurrentAdmin, DbSession, RedisClient
from app.schemas.system import SystemMetricsResponse
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
