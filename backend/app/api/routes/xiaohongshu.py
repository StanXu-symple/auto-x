from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select

from app.api.deps import CurrentAdmin, DbSession, RedisClient
from app.api.errors import APIError
from app.core.config import get_settings
from app.models.ai import AIDraft
from app.models.xiaohongshu import (
    XiaohongshuConnection,
    XiaohongshuPublishJob,
    XiaohongshuPublishSetting,
)
from app.schemas.common import MessageResponse, Page
from app.schemas.xiaohongshu import (
    XiaohongshuConnectionSave,
    XiaohongshuConnectionStatus,
    XiaohongshuConnectionTestResult,
    XiaohongshuLoginQr,
    XiaohongshuPublishJobCreate,
    XiaohongshuPublishJobOut,
    XiaohongshuPublishSettingsOut,
    XiaohongshuPublishSettingsPatch,
    XiaohongshuScheduleRequest,
)
from app.services.xiaohongshu_connection import (
    cache_status,
    delete_connection,
    get_runtime,
    save_connection,
)
from app.services.xiaohongshu_mcp import XiaohongshuMCPClient, XiaohongshuMCPError

router = APIRouter(prefix="/xiaohongshu", tags=["Xiaohongshu"])


async def _connection_status(db: DbSession, redis: RedisClient) -> XiaohongshuConnectionStatus:
    row = await db.get(XiaohongshuConnection, 1)
    cached, ttl = await cache_status(redis)
    if row is None:
        return XiaohongshuConnectionStatus(configured=False)
    return XiaohongshuConnectionStatus(
        configured=True,
        name=row.name,
        connector=row.connector,
        mcp_url=row.mcp_url,
        token_configured=bool(row.encrypted_auth_token),
        token_hint=row.token_hint,
        verification_status=row.verification_status,
        login_status=row.login_status,
        risk_acknowledged=row.risk_acknowledged,
        last_verified_at=row.last_verified_at,
        last_error=row.last_error,
        version=row.version,
        cache_active=cached,
        cache_ttl_seconds=ttl,
        updated_at=row.updated_at,
    )


async def _settings_row(db: DbSession) -> XiaohongshuPublishSetting:
    row = await db.get(XiaohongshuPublishSetting, 1)
    if row is None:
        row = XiaohongshuPublishSetting(id=1)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def _settings_out(
    row: XiaohongshuPublishSetting, redis: RedisClient
) -> XiaohongshuPublishSettingsOut:
    worker_status = "offline"
    heartbeat_at = None
    try:
        raw = await redis.get("xsentinel:xhs-worker:heartbeat")
        if raw:
            payload = json.loads(raw)
            heartbeat_raw = str(payload.get("last_heartbeat") or "").replace("Z", "+00:00")
            heartbeat_at = datetime.fromisoformat(heartbeat_raw)
            worker_status = "online"
    except Exception:
        worker_status = "unknown"
    return XiaohongshuPublishSettingsOut(
        enabled=row.enabled,
        default_strategy=row.default_strategy,
        default_delay_minutes=row.default_delay_minutes,
        max_attempts=row.max_attempts,
        daily_publish_limit=row.daily_publish_limit,
        default_visibility=row.default_visibility,
        declare_original=row.declare_original,
        worker_status=worker_status,
        worker_last_heartbeat=heartbeat_at,
        updated_at=row.updated_at,
    )


@router.get("/connection", response_model=XiaohongshuConnectionStatus)
async def read_connection(
    db: DbSession, redis: RedisClient, _: CurrentAdmin
) -> XiaohongshuConnectionStatus:
    return await _connection_status(db, redis)


@router.put("/connection", response_model=XiaohongshuConnectionStatus)
async def replace_connection(
    payload: XiaohongshuConnectionSave,
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
) -> XiaohongshuConnectionStatus:
    await save_connection(
        db,
        redis,
        get_settings(),
        name=payload.name,
        mcp_url=payload.mcp_url,
        auth_token=payload.auth_token,
        risk_acknowledged=payload.risk_acknowledged,
    )
    return await _connection_status(db, redis)


@router.delete("/connection", response_model=MessageResponse)
async def remove_connection(
    db: DbSession, redis: RedisClient, _: CurrentAdmin
) -> MessageResponse:
    deleted = await delete_connection(db, redis)
    settings = await _settings_row(db)
    settings.enabled = False
    await db.commit()
    message = "小红书连接已删除，自动发布已停用" if deleted else "没有已配置的连接"
    return MessageResponse(message=message)


@router.post("/connection/test", response_model=XiaohongshuConnectionTestResult)
async def test_connection(
    db: DbSession, redis: RedisClient, _: CurrentAdmin
) -> XiaohongshuConnectionTestResult:
    row = await db.get(XiaohongshuConnection, 1)
    if row is None:
        raise APIError(409, "xhs_not_configured", "小红书连接尚未配置")
    checked_at = datetime.now(UTC)
    try:
        runtime = await get_runtime(db, redis, get_settings())
        client = XiaohongshuMCPClient(
            runtime.mcp_url,
            runtime.auth_token,
            timeout_seconds=get_settings().xhs_request_timeout_seconds,
        )
        logged_in, message = await client.check_login()
        row.verification_status = "valid"
        row.login_status = "logged_in" if logged_in else "logged_out"
        row.last_error = None if logged_in else message[:1000]
        valid = True
    except XiaohongshuMCPError as exc:
        logged_in = False
        valid = False
        message = str(exc)
        row.verification_status = "invalid" if not exc.retryable else "error"
        row.login_status = "unknown"
        row.last_error = message[:1000]
    row.last_verified_at = checked_at
    await db.commit()
    return XiaohongshuConnectionTestResult(
        valid=valid,
        logged_in=logged_in,
        verification_status=row.verification_status,
        login_status=row.login_status,
        message=message,
        checked_at=checked_at,
    )


@router.post("/connection/login-qrcode", response_model=XiaohongshuLoginQr)
async def login_qrcode(
    db: DbSession, redis: RedisClient, _: CurrentAdmin
) -> XiaohongshuLoginQr:
    try:
        runtime = await get_runtime(db, redis, get_settings())
        image, mime_type, message = await XiaohongshuMCPClient(
            runtime.mcp_url,
            runtime.auth_token,
            timeout_seconds=get_settings().xhs_request_timeout_seconds,
        ).get_login_qr()
    except XiaohongshuMCPError as exc:
        raise APIError(502, "xhs_mcp_error", str(exc)) from None
    if not image:
        raise APIError(502, "xhs_qrcode_missing", message)
    return XiaohongshuLoginQr(image_data=image, mime_type=mime_type, message=message)


@router.get("/settings", response_model=XiaohongshuPublishSettingsOut)
async def read_publish_settings(
    db: DbSession, redis: RedisClient, _: CurrentAdmin
) -> XiaohongshuPublishSettingsOut:
    return await _settings_out(await _settings_row(db), redis)


@router.put("/settings", response_model=XiaohongshuPublishSettingsOut)
async def replace_publish_settings(
    payload: XiaohongshuPublishSettingsPatch,
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
) -> XiaohongshuPublishSettingsOut:
    if payload.enabled:
        connection = await db.get(XiaohongshuConnection, 1)
        if connection is None or not connection.risk_acknowledged:
            raise APIError(409, "xhs_connection_required", "请先配置小红书连接并确认风险")
    row = await _settings_row(db)
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    row.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    return await _settings_out(row, redis)


@router.get("/jobs", response_model=Page[XiaohongshuPublishJobOut])
async def list_publish_jobs(
    db: DbSession,
    _: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    publish_status: str | None = Query(default=None, alias="status", max_length=24),
) -> Page[XiaohongshuPublishJobOut]:
    conditions = []
    if publish_status:
        conditions.append(XiaohongshuPublishJob.status == publish_status)
    total = int(
        await db.scalar(select(func.count(XiaohongshuPublishJob.id)).where(*conditions)) or 0
    )
    rows = list(
        await db.scalars(
            select(XiaohongshuPublishJob)
            .where(*conditions)
            .order_by(XiaohongshuPublishJob.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page(items=rows, total=total, page=page, page_size=page_size)


@router.post(
    "/jobs", response_model=XiaohongshuPublishJobOut, status_code=status.HTTP_201_CREATED
)
async def create_publish_job(
    payload: XiaohongshuPublishJobCreate,
    db: DbSession,
    _: CurrentAdmin,
) -> XiaohongshuPublishJob:
    settings = await _settings_row(db)
    if payload.source_ai_draft_id is not None:
        draft = await db.get(AIDraft, payload.source_ai_draft_id)
        if draft is None:
            raise APIError(404, "ai_draft_not_found", "AI 草稿不存在")
    strategy = payload.strategy or settings.default_strategy
    now = datetime.now(UTC)
    scheduled_at = payload.scheduled_at
    if strategy == "manual":
        job_status = "draft"
        scheduled_at = None
        next_attempt_at = None
    elif strategy == "automatic":
        job_status = "queued"
        scheduled_at = now
        next_attempt_at = now
    else:
        scheduled_at = scheduled_at or now + timedelta(minutes=settings.default_delay_minutes)
        job_status = "queued"
        next_attempt_at = scheduled_at
    job = XiaohongshuPublishJob(
        source_ai_draft_id=payload.source_ai_draft_id,
        title=payload.title,
        content=payload.content,
        images=payload.images,
        tags=payload.tags,
        products=payload.products,
        visibility=payload.visibility or settings.default_visibility,
        is_original=(
            payload.is_original if payload.is_original is not None else settings.declare_original
        ),
        strategy=strategy,
        status=job_status,
        scheduled_at=scheduled_at,
        next_attempt_at=next_attempt_at,
        max_attempts=settings.max_attempts,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def _mutable_job(db: DbSession, job_id: int) -> XiaohongshuPublishJob:
    job = await db.get(XiaohongshuPublishJob, job_id)
    if job is None:
        raise APIError(404, "xhs_job_not_found", "发布任务不存在")
    if job.status in {"publishing", "published"}:
        raise APIError(409, "xhs_job_immutable", "正在发布或已发布的任务不能修改")
    return job


@router.post("/jobs/{job_id}/publish", response_model=XiaohongshuPublishJobOut)
async def publish_now(job_id: int, db: DbSession, _: CurrentAdmin) -> XiaohongshuPublishJob:
    job = await _mutable_job(db, job_id)
    now = datetime.now(UTC)
    job.strategy = "automatic"
    job.status = "queued"
    job.scheduled_at = now
    job.next_attempt_at = now
    job.last_error = None
    await db.commit()
    await db.refresh(job)
    return job


@router.post("/jobs/{job_id}/schedule", response_model=XiaohongshuPublishJobOut)
async def schedule_job(
    job_id: int,
    payload: XiaohongshuScheduleRequest,
    db: DbSession,
    _: CurrentAdmin,
) -> XiaohongshuPublishJob:
    job = await _mutable_job(db, job_id)
    job.strategy = "delayed"
    job.status = "queued"
    job.scheduled_at = payload.scheduled_at
    job.next_attempt_at = payload.scheduled_at
    job.last_error = None
    await db.commit()
    await db.refresh(job)
    return job


@router.post("/jobs/{job_id}/cancel", response_model=XiaohongshuPublishJobOut)
async def cancel_job(job_id: int, db: DbSession, _: CurrentAdmin) -> XiaohongshuPublishJob:
    job = await _mutable_job(db, job_id)
    job.status = "cancelled"
    job.next_attempt_at = None
    await db.commit()
    await db.refresh(job)
    return job


@router.post("/jobs/{job_id}/retry", response_model=XiaohongshuPublishJobOut)
async def retry_job(job_id: int, db: DbSession, _: CurrentAdmin) -> XiaohongshuPublishJob:
    job = await _mutable_job(db, job_id)
    now = datetime.now(UTC)
    job.status = "queued"
    job.next_attempt_at = now
    job.scheduled_at = now
    job.last_error = None
    await db.commit()
    await db.refresh(job)
    return job
