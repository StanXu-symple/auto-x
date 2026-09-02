from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.xiaohongshu import XiaohongshuConnection
from app.services.x_credentials import decrypt_token, encrypt_token, token_fingerprint, token_hint

logger = logging.getLogger(__name__)
CONNECTION_ID = 1
CACHE_KEY = "xsentinel:xhs:auth-token:v1"


class XiaohongshuConnectionUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class XiaohongshuRuntime:
    name: str
    mcp_url: str
    auth_token: str | None
    version: int


async def _write_cache(redis: Redis, row: XiaohongshuConnection, settings: Settings) -> None:
    if not row.encrypted_auth_token:
        await redis.delete(CACHE_KEY)
        return
    payload = json.dumps(
        {"encrypted_auth_token": row.encrypted_auth_token, "version": row.version},
        separators=(",", ":"),
    )
    try:
        await redis.set(CACHE_KEY, payload, ex=settings.x_token_cache_ttl_seconds)
    except Exception:
        logger.warning("Unable to cache encrypted Xiaohongshu token", exc_info=True)


async def save_connection(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    *,
    name: str,
    mcp_url: str,
    auth_token: str | None,
    risk_acknowledged: bool,
) -> XiaohongshuConnection:
    row = await session.get(XiaohongshuConnection, CONNECTION_ID)
    now = datetime.now(UTC)
    if row is None:
        row = XiaohongshuConnection(
            id=CONNECTION_ID,
            name=name,
            mcp_url=mcp_url,
            encrypted_auth_token=encrypt_token(auth_token, settings) if auth_token else None,
            token_hint=token_hint(auth_token) if auth_token else None,
            token_fingerprint=token_fingerprint(auth_token) if auth_token else None,
            risk_acknowledged=risk_acknowledged,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.name = name
        row.mcp_url = mcp_url
        row.risk_acknowledged = risk_acknowledged
        if auth_token:
            row.encrypted_auth_token = encrypt_token(auth_token, settings)
            row.token_hint = token_hint(auth_token)
            row.token_fingerprint = token_fingerprint(auth_token)
        row.verification_status = "unverified"
        row.login_status = "unknown"
        row.last_verified_at = None
        row.last_error = None
        row.version += 1
        row.updated_at = now
    await session.commit()
    await session.refresh(row)
    await _write_cache(redis, row, settings)
    return row


async def get_runtime(
    session: AsyncSession, redis: Redis, settings: Settings
) -> XiaohongshuRuntime:
    row = await session.get(XiaohongshuConnection, CONNECTION_ID)
    if row is None:
        raise XiaohongshuConnectionUnavailable("小红书数据源尚未配置")
    token: str | None = None
    if row.encrypted_auth_token:
        try:
            cached = await redis.get(CACHE_KEY)
            if cached:
                payload = json.loads(cached)
                if int(payload.get("version") or 0) == row.version:
                    token = decrypt_token(payload["encrypted_auth_token"], settings)
        except Exception:
            logger.warning("Unable to read Xiaohongshu token cache", exc_info=True)
        if token is None:
            token = decrypt_token(row.encrypted_auth_token, settings)
            await _write_cache(redis, row, settings)
    return XiaohongshuRuntime(row.name, row.mcp_url, token, row.version)


async def delete_connection(session: AsyncSession, redis: Redis) -> bool:
    row = await session.get(XiaohongshuConnection, CONNECTION_ID)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    try:
        await redis.delete(CACHE_KEY)
    except Exception:
        logger.warning("Unable to clear Xiaohongshu token cache", exc_info=True)
    return True


async def cache_status(redis: Redis) -> tuple[bool, int | None]:
    try:
        ttl = int(await redis.ttl(CACHE_KEY))
        return ttl > 0, ttl if ttl > 0 else None
    except Exception:
        return False, None
