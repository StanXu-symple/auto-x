from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.ai import AISetting
from app.models.ai_data_source import AIDataSource
from app.services.x_credentials import (
    XCredentialUnavailableError,
    decrypt_token,
    encrypt_token,
    token_fingerprint,
    token_hint,
)

logger = logging.getLogger(__name__)

CACHE_KEY = "xsentinel:ai-data-source:api-key:v1"
DATA_SOURCE_ID = 1


class AIDataSourceUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AIDataSourceRuntime:
    name: str
    protocol: str
    base_url: str
    model: str
    api_key: str
    version: int


async def _write_cache(
    redis: Redis,
    *,
    encrypted_api_key: str,
    version: int,
    settings: Settings,
) -> None:
    payload = json.dumps(
        {"encrypted_api_key": encrypted_api_key, "version": version},
        separators=(",", ":"),
    )
    try:
        await redis.set(CACHE_KEY, payload, ex=settings.x_token_cache_ttl_seconds)
    except Exception:
        logger.warning("Unable to update encrypted AI API key cache", exc_info=True)


async def save_ai_data_source(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    *,
    name: str,
    protocol: str,
    base_url: str,
    model: str,
    api_key: str | None,
) -> AIDataSource:
    row = await session.get(AIDataSource, DATA_SOURCE_ID)
    if row is None and not api_key:
        raise AIDataSourceUnavailableError("API Key is required for the first configuration")
    now = datetime.now(UTC)
    if row is None:
        assert api_key is not None
        row = AIDataSource(
            id=DATA_SOURCE_ID,
            name=name,
            protocol=protocol,
            base_url=base_url,
            model_name=model,
            encrypted_api_key=encrypt_token(api_key, settings),
            key_hint=token_hint(api_key),
            key_fingerprint=token_fingerprint(api_key),
            verification_status="unverified",
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.name = name
        row.protocol = protocol
        row.base_url = base_url
        row.model_name = model
        if api_key:
            row.encrypted_api_key = encrypt_token(api_key, settings)
            row.key_hint = token_hint(api_key)
            row.key_fingerprint = token_fingerprint(api_key)
        row.verification_status = "unverified"
        row.last_verified_at = None
        row.last_error = None
        row.version += 1
        row.updated_at = now

    ai_setting = await session.get(AISetting, 1)
    if ai_setting is not None:
        ai_setting.provider = "openai_responses"
        ai_setting.model_name = model
        ai_setting.base_url = base_url
        ai_setting.bridge_url = None
    await session.commit()
    await session.refresh(row)
    await _write_cache(
        redis,
        encrypted_api_key=row.encrypted_api_key,
        version=row.version,
        settings=settings,
    )
    return row


async def _decrypt_api_key(
    row: AIDataSource,
    redis: Redis,
    settings: Settings,
) -> str:
    try:
        cached = await redis.get(CACHE_KEY)
        if cached:
            payload = json.loads(cached)
            if int(payload.get("version") or 0) == row.version:
                encrypted = payload.get("encrypted_api_key")
                if isinstance(encrypted, str):
                    return decrypt_token(encrypted, settings)
    except XCredentialUnavailableError as exc:
        raise AIDataSourceUnavailableError(str(exc)) from exc
    except Exception:
        logger.warning("Unable to read encrypted AI API key cache", exc_info=True)
    try:
        api_key = decrypt_token(row.encrypted_api_key, settings)
    except XCredentialUnavailableError as exc:
        raise AIDataSourceUnavailableError(str(exc)) from exc
    await _write_cache(
        redis,
        encrypted_api_key=row.encrypted_api_key,
        version=row.version,
        settings=settings,
    )
    return api_key


async def get_ai_data_source(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
) -> AIDataSourceRuntime:
    row = await session.get(AIDataSource, DATA_SOURCE_ID)
    if row is None:
        raise AIDataSourceUnavailableError("AI data source has not been configured")
    return AIDataSourceRuntime(
        name=row.name,
        protocol=row.protocol,
        base_url=row.base_url,
        model=row.model_name,
        api_key=await _decrypt_api_key(row, redis, settings),
        version=row.version,
    )


async def delete_ai_data_source(session: AsyncSession, redis: Redis) -> bool:
    row = await session.get(AIDataSource, DATA_SOURCE_ID)
    if row is None:
        return False
    await session.delete(row)
    ai_setting = await session.get(AISetting, 1)
    if ai_setting is not None:
        ai_setting.enabled = False
    await session.commit()
    try:
        await redis.delete(CACHE_KEY)
    except Exception:
        logger.warning("Unable to clear encrypted AI API key cache", exc_info=True)
    return True


async def ai_data_source_cache_status(redis: Redis) -> tuple[bool, int | None]:
    try:
        ttl = int(await redis.ttl(CACHE_KEY))
        return ttl > 0, ttl if ttl > 0 else None
    except Exception:
        return False, None
