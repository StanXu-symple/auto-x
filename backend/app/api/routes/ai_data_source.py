from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, status

from app.api.deps import CurrentAdmin, DbSession, RedisClient
from app.api.errors import APIError
from app.core.config import get_settings
from app.models.ai_data_source import AIDataSource
from app.schemas.ai_data_source import (
    AIDataSourceSave,
    AIDataSourceStatus,
    AIDataSourceTestResult,
    AIModelList,
)
from app.schemas.common import MessageResponse
from app.services.ai_data_source import (
    AIDataSourceUnavailableError,
    ai_data_source_cache_status,
    delete_ai_data_source,
    get_ai_data_source,
    save_ai_data_source,
)
from app.services.ai_provider import AIProviderClient, AIProviderError

router = APIRouter(prefix="/ai-data-source", tags=["AI Data Source"])


async def _status(db: DbSession, redis: RedisClient) -> AIDataSourceStatus:
    row = await db.get(AIDataSource, 1)
    cache_active, cache_ttl = await ai_data_source_cache_status(redis)
    if row is None:
        return AIDataSourceStatus(configured=False)
    return AIDataSourceStatus(
        configured=True,
        name=row.name,
        protocol="openai_responses",
        base_url=row.base_url,
        model=row.model_name,
        key_hint=row.key_hint,
        verification_status=row.verification_status,
        last_verified_at=row.last_verified_at,
        last_error=row.last_error,
        version=row.version,
        cache_active=cache_active,
        cache_ttl_seconds=cache_ttl,
        updated_at=row.updated_at,
    )


def _validate_runtime_destination(base_url: str) -> None:
    hostname = urlsplit(base_url).hostname or ""
    try:
        AIProviderClient._validate_destination(
            base_url.rstrip("/") + "/models",
            allowed_hosts=[hostname],
            sends_credential=True,
        )
    except AIProviderError as exc:
        raise APIError(422, "ai_destination_invalid", str(exc)) from None


async def _fetch_models(db: DbSession, redis: RedisClient) -> list[str]:
    settings = get_settings()
    try:
        source = await get_ai_data_source(db, redis, settings)
    except AIDataSourceUnavailableError as exc:
        raise APIError(409, "ai_data_source_not_configured", str(exc)) from None
    _validate_runtime_destination(source.base_url)
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.get(
                source.base_url.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {source.api_key}"},
            )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise APIError(502, "ai_data_source_unreachable", f"连接模型服务失败：{exc}") from None
    if response.is_error:
        code = (
            "ai_data_source_invalid_key"
            if response.status_code in {401, 403}
            else "ai_data_source_error"
        )
        raise APIError(
            502,
            code,
            f"模型服务返回 HTTP {response.status_code}",
        )
    try:
        payload = response.json()
    except ValueError:
        raise APIError(502, "ai_data_source_invalid_response", "模型服务没有返回 JSON") from None
    items = payload.get("data") if isinstance(payload, dict) else None
    models = sorted(
        {
            str(item.get("id"))
            for item in (items or [])
            if isinstance(item, dict) and item.get("id")
        }
    )
    return models


@router.get("", response_model=AIDataSourceStatus)
async def read_ai_data_source(
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
) -> AIDataSourceStatus:
    return await _status(db, redis)


@router.put("", response_model=AIDataSourceStatus)
async def replace_ai_data_source(
    payload: AIDataSourceSave,
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
) -> AIDataSourceStatus:
    _validate_runtime_destination(payload.base_url)
    try:
        await save_ai_data_source(
            db,
            redis,
            get_settings(),
            name=payload.name,
            protocol=payload.protocol,
            base_url=payload.base_url,
            model=payload.model,
            api_key=payload.api_key,
        )
    except AIDataSourceUnavailableError as exc:
        raise APIError(409, "ai_data_source_key_required", str(exc)) from None
    except Exception as exc:
        if "encryption key" in str(exc).lower():
            raise APIError(503, "credential_encryption_unavailable", str(exc)) from None
        raise
    return await _status(db, redis)


@router.get("/models", response_model=AIModelList)
async def list_ai_models(
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
) -> AIModelList:
    return AIModelList(models=await _fetch_models(db, redis))


@router.post("/test", response_model=AIDataSourceTestResult)
async def test_ai_data_source(
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
) -> AIDataSourceTestResult:
    row = await db.get(AIDataSource, 1)
    if row is None:
        raise APIError(409, "ai_data_source_not_configured", "AI 数据源尚未配置")
    checked_at = datetime.now(UTC)
    try:
        models = await _fetch_models(db, redis)
        valid = True
        verification_status = "valid"
        selected_available = not models or row.model_name in models
        message = (
            "连接成功，API Key 与模型均可用。"
            if selected_available
            else f"连接成功，但模型列表中没有 {row.model_name}。"
        )
        if not selected_available:
            valid = False
            verification_status = "error"
        row.last_error = None if valid else message
    except APIError as exc:
        models = []
        valid = False
        verification_status = (
            "invalid" if exc.code == "ai_data_source_invalid_key" else "error"
        )
        message = exc.message
        row.last_error = message[:500]
    row.verification_status = verification_status
    row.last_verified_at = checked_at
    await db.commit()
    return AIDataSourceTestResult(
        valid=valid,
        verification_status=verification_status,
        message=message,
        models=models,
        checked_at=checked_at,
    )


@router.delete("", response_model=MessageResponse, status_code=status.HTTP_200_OK)
async def remove_ai_data_source(
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
) -> MessageResponse:
    deleted = await delete_ai_data_source(db, redis)
    message = "AI 数据源已删除，AI 创作已停用" if deleted else "没有已配置的 AI 数据源"
    return MessageResponse(message=message)
