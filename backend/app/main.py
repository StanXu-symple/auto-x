from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select

from app import __version__
from app.api.errors import APIError
from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.init_db import initialize_database
from app.db.session import AsyncSessionFactory, engine
from app.models.ai import AIGenerationJob
from app.models.monitored_user import MonitoredUser
from app.services.metrics import (
    AI_QUEUE_DUE,
    AI_WORKER_HEARTBEAT,
    HTTP_DURATION,
    HTTP_REQUESTS,
    POLL_QUEUE_DUE,
    WORKER_HEARTBEAT,
)

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=settings.redis_socket_timeout_seconds,
    )
    startup_errors: list[str] = []
    try:
        await initialize_database(settings)
    except Exception as exc:
        startup_errors.append(f"database: {exc}")
        logger.exception("Database initialization failed")
    try:
        await app.state.redis.ping()
    except Exception as exc:
        startup_errors.append(f"redis: {exc}")
        logger.exception("Redis startup check failed")
    app.state.startup_errors = startup_errors
    if startup_errors and settings.startup_strict:
        raise RuntimeError("; ".join(startup_errors))
    logger.info(
        "X Sentinel API started",
        extra={"version": __version__, "environment": settings.environment},
    )
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await engine.dispose()
        logger.info("X Sentinel API stopped")


app = FastAPI(
    title="X Sentinel API",
    summary="Manage X account monitoring and inspect collected tweets.",
    version=__version__,
    debug=settings.debug,
    lifespan=lifespan,
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url="/redoc" if settings.environment != "production" else None,
    openapi_url="/openapi.json" if settings.environment != "production" else None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


def error_body(request: Request, code: str, message: str, details: object = None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": getattr(request.state, "request_id", None),
        }
    }


@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    headers = dict(exc.headers or {})
    if exc.status_code == 401:
        headers.setdefault("WWW-Authenticate", "Bearer")
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder(error_body(request, exc.code, exc.message, exc.details)),
        headers=headers or None,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder(
            error_body(request, "validation_error", "Request validation failed", exc.errors())
        ),
    )


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "HTTP request failed"
    details = None if isinstance(exc.detail, str) else exc.detail
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder(error_body(request, "http_error", message, details)),
        headers=exc.headers,
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled API error",
        extra={"request_id": getattr(request.state, "request_id", None)},
    )
    return JSONResponse(
        status_code=500,
        content=error_body(request, "internal_error", "An unexpected server error occurred"),
    )


@app.middleware("http")
async def request_context_and_metrics(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", "")[:128] or uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        response = await unexpected_error_handler(request, exc)
    route = request.scope.get("route")
    route_path = getattr(route, "path", "__unmatched__") if route else "__unmatched__"
    HTTP_REQUESTS.labels(
        method=request.method, path=route_path, status=str(response.status_code)
    ).inc()
    HTTP_DURATION.labels(method=request.method, path=route_path).observe(
        time.perf_counter() - started
    )
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics(request: Request) -> Response:
    # Worker metrics live in another process, so mirror its Redis heartbeat here.
    try:
        raw = await request.app.state.redis.get("xsentinel:worker:heartbeat")
        if raw:
            import json

            payload = json.loads(raw)
            timestamp = str(payload.get("last_heartbeat") or payload.get("timestamp", "")).replace(
                "Z", "+00:00"
            )
            WORKER_HEARTBEAT.set(datetime.fromisoformat(timestamp).timestamp())
    except Exception:
        pass
    try:
        raw = await request.app.state.redis.get("xsentinel:ai-worker:heartbeat")
        if raw:
            import json

            payload = json.loads(raw)
            timestamp = str(payload.get("last_heartbeat") or payload.get("timestamp", "")).replace(
                "Z", "+00:00"
            )
            AI_WORKER_HEARTBEAT.set(datetime.fromisoformat(timestamp).timestamp())
    except Exception:
        pass
    try:
        now = datetime.now(UTC)
        async with AsyncSessionFactory() as session:
            due = int(
                await session.scalar(
                    select(func.count(MonitoredUser.id)).where(
                        MonitoredUser.is_active.is_(True),
                        or_(
                            MonitoredUser.manual_poll_token.is_not(None),
                            MonitoredUser.next_poll_at.is_(None),
                            MonitoredUser.next_poll_at <= now,
                        ),
                    )
                )
                or 0
            )
            POLL_QUEUE_DUE.set(due)
            ai_due = int(
                await session.scalar(
                    select(func.count(AIGenerationJob.id)).where(
                        or_(
                            and_(
                                AIGenerationJob.status.in_(["queued", "retry_wait"]),
                                AIGenerationJob.next_attempt_at <= now,
                            ),
                            and_(
                                AIGenerationJob.status == "running",
                                AIGenerationJob.lease_expires_at.is_not(None),
                                AIGenerationJob.lease_expires_at <= now,
                            ),
                        )
                    )
                )
                or 0
            )
            AI_QUEUE_DUE.set(ai_due)
    except Exception:
        pass
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(api_router, prefix=settings.api_prefix)
