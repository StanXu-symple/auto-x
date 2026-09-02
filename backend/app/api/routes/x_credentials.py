from datetime import UTC, datetime

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import CurrentAdmin, DbSession, RedisClient
from app.api.errors import APIError
from app.core.config import get_settings
from app.models.x_credential import XCredential
from app.schemas.common import MessageResponse
from app.schemas.x_credential import (
    XCredentialSave,
    XCredentialStatus,
    XCredentialTestResult,
)
from app.services.x_client import XAPIError, XClient
from app.services.x_credentials import (
    CREDENTIAL_TYPE,
    XCredentialUnavailableError,
    cache_status,
    delete_bearer_token,
    get_bearer_token,
    save_bearer_token,
)

router = APIRouter(prefix="/x-credentials", tags=["X credentials"])
GLOBAL_X_GATE_KEY = "xsentinel:x-api:gate"


async def _status(db: DbSession, redis: RedisClient) -> XCredentialStatus:
    row = await db.scalar(
        select(XCredential).where(XCredential.credential_type == CREDENTIAL_TYPE)
    )
    cache_active, cache_ttl = await cache_status(redis)
    if row is None:
        return XCredentialStatus(configured=False, cache_active=False)
    return XCredentialStatus(
        configured=True,
        token_hint=row.token_hint,
        acquisition_method=row.acquisition_method,
        verification_status=row.verification_status,
        last_verified_at=row.last_verified_at,
        last_error=row.last_error,
        updated_at=row.updated_at,
        version=row.version,
        cache_active=cache_active,
        cache_ttl_seconds=cache_ttl,
    )


@router.get("/status", response_model=XCredentialStatus)
async def credential_status(
    db: DbSession, redis: RedisClient, _: CurrentAdmin
) -> XCredentialStatus:
    return await _status(db, redis)


@router.put("/bearer-token", response_model=XCredentialStatus)
async def replace_bearer_token(
    payload: XCredentialSave,
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
) -> XCredentialStatus:
    settings = get_settings()
    try:
        await save_bearer_token(
            db,
            redis,
            settings,
            token=payload.bearer_token,
            acquisition_method=payload.acquisition_method,
        )
    except XCredentialUnavailableError as exc:
        raise APIError(503, "credential_encryption_unavailable", str(exc)) from None
    await redis.delete(GLOBAL_X_GATE_KEY)
    return await _status(db, redis)


@router.post("/test", response_model=XCredentialTestResult)
async def test_bearer_token(
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
) -> XCredentialTestResult:
    settings = get_settings()
    row = await db.scalar(
        select(XCredential).where(XCredential.credential_type == CREDENTIAL_TYPE)
    )
    if row is None:
        raise APIError(409, "x_credential_not_configured", "X Bearer Token is not configured")
    try:
        token = await get_bearer_token(db, redis, settings)
    except XCredentialUnavailableError as exc:
        raise APIError(503, "x_credential_unavailable", str(exc)) from None

    checked_at = datetime.now(UTC)
    verification_status = "valid"
    message = "Token 有效，已成功访问 X API。"
    valid = True
    try:
        async with XClient(
            token,
            base_url=settings.x_api_base_url,
            timeout_seconds=settings.x_request_timeout_seconds,
            max_pages=1,
            page_size=5,
        ) as client:
            await client.lookup_user("XDevelopers")
        row.last_error = None
    except XAPIError as exc:
        valid = False
        if exc.status_code == 401:
            verification_status = "invalid"
            message = "Token 无效或已过期，请重新生成。"
        else:
            verification_status = "error"
            message = f"Token 已保存，但 X API 校验失败：{str(exc)[:240]}"
        row.last_error = message[:500]
    row.verification_status = verification_status
    row.last_verified_at = checked_at
    await db.commit()
    return XCredentialTestResult(
        valid=valid,
        verification_status=verification_status,
        message=message,
        checked_at=checked_at,
    )


@router.delete("/bearer-token", response_model=MessageResponse, status_code=status.HTTP_200_OK)
async def remove_bearer_token(
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
) -> MessageResponse:
    deleted = await delete_bearer_token(db, redis)
    return MessageResponse(
        message="X Bearer Token 已删除" if deleted else "没有已保存的 X Bearer Token"
    )
