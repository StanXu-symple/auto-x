import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from app import __version__
from app.db.session import AsyncSessionFactory
from app.schemas.system import HealthResponse

router = APIRouter(prefix="/health", tags=["Health"])
logger = logging.getLogger(__name__)


@router.get("/live", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        timestamp=datetime.now(UTC),
    )


@router.get("/ready", response_model=HealthResponse)
async def readiness(request: Request, response: Response) -> HealthResponse:
    checks: dict[str, object] = {}
    try:
        async with AsyncSessionFactory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = {"status": "healthy"}
    except Exception:
        logger.exception("Readiness database check failed")
        checks["database"] = {"status": "unhealthy"}

    try:
        await request.app.state.redis.ping()
        checks["redis"] = {"status": "healthy"}
    except Exception:
        logger.exception("Readiness Redis check failed")
        checks["redis"] = {"status": "unhealthy"}

    ready = all(
        isinstance(check, dict) and check.get("status") == "healthy" for check in checks.values()
    )
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if ready else "degraded",
        version=__version__,
        timestamp=datetime.now(UTC),
        checks=checks,
    )
