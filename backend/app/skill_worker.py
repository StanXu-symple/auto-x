"""Skill management, bindings and resolution behind an authenticated HTTP boundary."""

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis
from sqlalchemy import select, text

from app import __version__
from app.api.deps import DbSession, authenticate_admin, get_current_admin
from app.api.errors import APIError
from app.control_plane.nacos import start_service_registration
from app.control_plane.security import ServiceVerifier
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import AsyncSessionFactory, engine
from app.models.ai import AIFeature, AIGenerationJob, AIListenTaskSkill
from app.schemas.ai import AISkillOut
from app.schemas.skill_service import (
    SkillContextRequest,
    SkillResolution,
    SkillResolveRequest,
    TaskSkillSelection,
)
from app.services.auth_center import AuthCenterClient
from app.services.metrics import HTTP_DURATION, HTTP_REQUESTS
from app.skills import listening_routes, routes, store
from app.skills.bootstrap import seed_skills
from app.skills.packages import read_package_instructions

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    app.state.auth_center = AuthCenterClient(settings)
    app.state.redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=settings.redis_socket_timeout_seconds,
    )
    for scope in ("read", "manage"):
        setattr(
            app.state,
            f"skill_{scope}_verifier",
            ServiceVerifier(
                None,
                "skill-worker",
                f"skills:{scope}",
                http=app.state.auth_center.http,
                auth_center_url=settings.service_auth_url,
                nacos=app.state.auth_center.nacos,
            ),
        )
    registration = None
    try:
        async with AsyncSessionFactory() as session:
            await session.execute(text("SELECT 1"))
        await seed_skills()
        registration = await start_service_registration(
            settings,
            "xsentinel-skill-worker",
            8204,
            metadata={"component": "skill_worker", "version": __version__},
        )
        yield
    finally:
        if registration is not None:
            await registration.aclose()
        await app.state.redis.aclose()
        await app.state.auth_center.aclose()
        await engine.dispose()


async def require_reader(request: Request):
    return await request.app.state.skill_read_verifier(request)


async def require_manager(request: Request):
    return await request.app.state.skill_manage_verifier(request)


async def delegated_admin(request: Request, db: DbSession):
    scheme, _, token = request.headers.get("x-user-authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise APIError(401, "not_authenticated", "Authentication credentials were not provided")
    return await authenticate_admin(db, token, request.app.state.auth_center)


app = FastAPI(title="X Sentinel Skill Worker", version=__version__, lifespan=lifespan)
app.dependency_overrides[get_current_admin] = delegated_admin
app.include_router(routes.router, dependencies=[Depends(require_manager)])
app.include_router(listening_routes.router, prefix="/ai", dependencies=[Depends(require_manager)])
internal = APIRouter(prefix="/v1", dependencies=[Depends(require_reader)])


@internal.post("/skills/resolve", response_model=list[AISkillOut])
async def resolve(payload: SkillResolveRequest, db: DbSession):
    return await store.resolve_active_skills(db, payload.skill_ids)


@internal.post("/skills/context", response_model=SkillResolution)
async def resolve_context(payload: SkillContextRequest, db: DbSession):
    feature = await db.get(AIFeature, payload.feature_id)
    if feature is None:
        raise APIError(404, "ai_feature_not_found", "AI 功能点不存在")
    skills, source = await store.resolve_context_skills(
        db,
        feature=feature,
        monitored_user_id=payload.monitored_user_id,
        fallback_skill_ids=payload.fallback_skill_ids,
        override_skill_ids=payload.override_skill_ids,
    )
    return SkillResolution(skills=[AISkillOut.model_validate(s) for s in skills], source=source)


@internal.get("/tasks/{task_id}/skills", response_model=TaskSkillSelection)
async def resolve_task(task_id: int, db: DbSession):
    # Consumers hold setting → task locks until their job/cursor commit. Never
    # acquire those locks here: that would deadlock the calling transaction.
    ids = list(
        await db.scalars(
            select(AIListenTaskSkill.skill_id)
            .where(AIListenTaskSkill.task_id == task_id)
            .order_by(AIListenTaskSkill.priority, AIListenTaskSkill.skill_id)
        )
    )
    skills = await store.resolve_active_skills(db, ids)
    return TaskSkillSelection(
        task_id=task_id,
        selected_ids=ids,
        skills=[AISkillOut.model_validate(s) for s in skills],
    )


@internal.get("/jobs/{job_id}/skills")
async def job_skills(job_id: int, db: DbSession):
    job = await db.get(AIGenerationJob, job_id)
    if job is None:
        raise APIError(404, "ai_job_not_found", "AI 任务不存在")
    materials = []
    for frozen in job.skill_snapshot or []:
        material = dict(frozen)
        if frozen.get("source_type") == "github":
            material["instructions"] = await asyncio.to_thread(
                read_package_instructions, frozen.get("source_metadata") or {},
            )
        materials.append(material)
    return {"job_id": job_id, "skills": materials}


app.include_router(internal)


def error_body(request, code, message, details=None):
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": getattr(request.state, "request_id", None),
        }
    }


@app.exception_handler(APIError)
async def api_error(request: Request, exc: APIError):
    headers = dict(exc.headers or {})
    if exc.status_code == 401:
        headers.setdefault("WWW-Authenticate", "Bearer")
    return JSONResponse(
        jsonable_encoder(error_body(request, exc.code, exc.message, exc.details)),
        status_code=exc.status_code,
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        jsonable_encoder(
            error_body(
                request,
                "validation_error",
                "Request validation failed",
                exc.errors(),
            )
        ),
        status_code=422,
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse(
        error_body(request, "http_error", str(exc.detail)),
        status_code=exc.status_code,
        headers=exc.headers,
    )


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-ID", "")[:128] or uuid.uuid4().hex
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Skill request failed", extra={"request_id": request.state.request_id})
        response = JSONResponse(
            error_body(request, "internal_error", "Skill request failed"), status_code=500
        )
    path = getattr(request.scope.get("route"), "path", "unmatched")
    HTTP_REQUESTS.labels(request.method, path, response.status_code).inc()
    HTTP_DURATION.labels(request.method, path).observe(time.perf_counter() - started)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.get("/health/live")
async def live():
    return {"status": "ok", "service": "skill-worker"}


@app.get("/health/ready")
async def ready():
    try:
        async with AsyncSessionFactory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(503, "Database unavailable") from None
    return {"status": "ok"}


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
