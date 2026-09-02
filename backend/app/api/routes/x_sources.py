from datetime import UTC, datetime

from fastapi import APIRouter, status
from sqlalchemy import select, update

from app.api.deps import CurrentAdmin, DbSession, RedisClient
from app.api.errors import APIError
from app.api.routes.x_credentials import _status as official_status
from app.core.config import get_settings
from app.models.monitored_user import MonitoredUser
from app.models.setting import AppSetting
from app.models.x_credential import XCredential
from app.schemas.common import MessageResponse
from app.schemas.x_source import (
    TwscrapeCredentialSave,
    TwscrapeCredentialStatus,
    XSourceProviderUpdate,
    XSourceStatus,
    XSourceTestResult,
)
from app.services.settings_service import (
    X_SOURCE_KEY,
    get_x_source_provider,
    set_x_source_provider,
)
from app.services.twscrape_client import TwscrapeClient
from app.services.twscrape_credentials import (
    CREDENTIAL_TYPE as TWSCRAPE_CREDENTIAL_TYPE,
)
from app.services.twscrape_credentials import (
    delete_twscrape_credentials,
    get_twscrape_credentials,
    save_twscrape_credentials,
    twscrape_cache_status,
)
from app.services.x_credentials import CREDENTIAL_TYPE as OFFICIAL_CREDENTIAL_TYPE
from app.services.x_credentials import XCredentialUnavailableError

router = APIRouter(prefix="/x-sources", tags=["X sources"])
GLOBAL_X_GATE_KEY = "xsentinel:x-api:gate"


async def _twscrape_status(
    db: DbSession,
    redis: RedisClient,
) -> TwscrapeCredentialStatus:
    row = await db.scalar(
        select(XCredential).where(XCredential.credential_type == TWSCRAPE_CREDENTIAL_TYPE)
    )
    cache_active, cache_ttl = await twscrape_cache_status(redis)
    if row is None:
        return TwscrapeCredentialStatus(configured=False)
    return TwscrapeCredentialStatus(
        configured=True,
        account_hint=row.token_hint,
        verification_status=row.verification_status,
        last_verified_at=row.last_verified_at,
        last_error=row.last_error,
        updated_at=row.updated_at,
        version=row.version,
        cache_active=cache_active,
        cache_ttl_seconds=cache_ttl,
    )


async def _source_status(db: DbSession, redis: RedisClient) -> XSourceStatus:
    provider = await get_x_source_provider(db)
    source_row = await db.get(AppSetting, X_SOURCE_KEY)
    return XSourceStatus(
        active_provider=provider,
        official_api=await official_status(db, redis),
        twscrape=await _twscrape_status(db, redis),
        updated_at=source_row.updated_at if source_row else None,
    )


@router.get("/status", response_model=XSourceStatus)
async def read_source_status(
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
) -> XSourceStatus:
    return await _source_status(db, redis)


@router.put("/provider", response_model=XSourceStatus)
async def select_source_provider(
    payload: XSourceProviderUpdate,
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
) -> XSourceStatus:
    credential_type = (
        OFFICIAL_CREDENTIAL_TYPE
        if payload.provider == "official_api"
        else TWSCRAPE_CREDENTIAL_TYPE
    )
    configured = await db.scalar(
        select(XCredential.id).where(XCredential.credential_type == credential_type)
    )
    if configured is None:
        raise APIError(
            409,
            "x_source_not_configured",
            "请先保存并测试该数据源的凭据，再启用它。",
        )
    await set_x_source_provider(db, payload.provider)
    now = datetime.now(UTC)
    await db.execute(
        update(MonitoredUser)
        .where(MonitoredUser.is_active.is_(True))
        .values(
            status="queued",
            next_poll_at=now,
            last_error=None,
            consecutive_failures=0,
        )
    )
    await db.commit()
    await redis.delete(GLOBAL_X_GATE_KEY)
    return await _source_status(db, redis)


@router.put("/twscrape/credentials", response_model=XSourceStatus)
async def replace_twscrape_credentials(
    payload: TwscrapeCredentialSave,
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
) -> XSourceStatus:
    try:
        await save_twscrape_credentials(
            db,
            redis,
            get_settings(),
            account_label=payload.account_label,
            auth_token=payload.auth_token,
            ct0=payload.ct0,
        )
    except XCredentialUnavailableError as exc:
        raise APIError(503, "credential_encryption_unavailable", str(exc)) from None
    await redis.delete(GLOBAL_X_GATE_KEY)
    return await _source_status(db, redis)


@router.post("/twscrape/test", response_model=XSourceTestResult)
async def test_twscrape_credentials(
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
) -> XSourceTestResult:
    settings = get_settings()
    row = await db.scalar(
        select(XCredential).where(XCredential.credential_type == TWSCRAPE_CREDENTIAL_TYPE)
    )
    if row is None:
        raise APIError(409, "twscrape_not_configured", "twscrape Cookies 尚未配置")

    async def credential_provider():
        return await get_twscrape_credentials(db, redis, settings)

    checked_at = datetime.now(UTC)
    valid = True
    verification_status = "valid"
    message = "twscrape Cookies 有效，已成功读取 X 用户。"
    client = TwscrapeClient(credential_provider, max_pages=1, page_size=5)
    try:
        await client.lookup_user("XDevelopers")
        row.last_error = None
    except Exception as exc:
        valid = False
        text = str(exc)
        invalid_markers = ("401", "403", "invalid", "inactive", "unavailable")
        verification_status = (
            "invalid" if any(marker in text.lower() for marker in invalid_markers) else "error"
        )
        message = f"twscrape 校验失败：{text[:300]}"
        row.last_error = message[:500]
    finally:
        await client.aclose()
    row.verification_status = verification_status
    row.last_verified_at = checked_at
    await db.commit()
    return XSourceTestResult(
        provider="twscrape",
        valid=valid,
        verification_status=verification_status,
        message=message,
        checked_at=checked_at,
    )


@router.delete(
    "/twscrape/credentials",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
async def remove_twscrape_credentials(
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
) -> MessageResponse:
    if await get_x_source_provider(db) == "twscrape":
        raise APIError(
            409,
            "x_source_in_use",
            "请先切换到官方 X API，再删除 twscrape Cookies。",
        )
    deleted = await delete_twscrape_credentials(db, redis)
    return MessageResponse(message="twscrape Cookies 已删除" if deleted else "没有已保存的 Cookies")
