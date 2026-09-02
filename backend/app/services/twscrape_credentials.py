from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import TypedDict

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.x_credential import XCredential
from app.services.x_credentials import (
    XCredentialUnavailableError,
    decrypt_token,
    encrypt_token,
)

logger = logging.getLogger(__name__)

CACHE_KEY = "xsentinel:x-credentials:twscrape:v1"
CREDENTIAL_TYPE = "twscrape_cookie"
CREDENTIAL_ID = 2


class TwscrapeCredential(TypedDict):
    account_label: str
    auth_token: str
    ct0: str
    version: int


def _serialize(account_label: str, auth_token: str, ct0: str) -> str:
    return json.dumps(
        {"account_label": account_label, "auth_token": auth_token, "ct0": ct0},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _parse(value: str, version: int) -> TwscrapeCredential:
    try:
        payload = json.loads(value)
        account_label = str(payload["account_label"])
        auth_token = str(payload["auth_token"])
        ct0 = str(payload["ct0"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise XCredentialUnavailableError("Stored twscrape credentials are invalid") from exc
    if not account_label or not auth_token or not ct0:
        raise XCredentialUnavailableError("Stored twscrape credentials are incomplete")
    return {
        "account_label": account_label,
        "auth_token": auth_token,
        "ct0": ct0,
        "version": version,
    }


async def _write_cache(
    redis: Redis,
    *,
    encrypted_value: str,
    version: int,
    settings: Settings,
) -> None:
    payload = json.dumps(
        {"encrypted_value": encrypted_value, "version": version}, separators=(",", ":")
    )
    try:
        await redis.set(CACHE_KEY, payload, ex=settings.x_token_cache_ttl_seconds)
    except Exception:
        logger.warning("Unable to update encrypted twscrape credential cache", exc_info=True)


async def save_twscrape_credentials(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    *,
    account_label: str,
    auth_token: str,
    ct0: str,
) -> XCredential:
    serialized = _serialize(account_label, auth_token, ct0)
    encrypted = encrypt_token(serialized, settings)
    fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    row = await session.scalar(
        select(XCredential).where(XCredential.credential_type == CREDENTIAL_TYPE)
    )
    now = datetime.now(UTC)
    if row is None:
        row = XCredential(
            id=CREDENTIAL_ID,
            credential_type=CREDENTIAL_TYPE,
            encrypted_value=encrypted,
            token_hint=f"@{account_label[:14]}",
            token_fingerprint=fingerprint,
            acquisition_method="browser_cookie",
            verification_status="unverified",
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.encrypted_value = encrypted
        row.token_hint = f"@{account_label[:14]}"
        row.token_fingerprint = fingerprint
        row.acquisition_method = "browser_cookie"
        row.verification_status = "unverified"
        row.last_verified_at = None
        row.last_error = None
        row.version += 1
        row.updated_at = now
    await session.commit()
    await session.refresh(row)
    await _write_cache(
        redis,
        encrypted_value=row.encrypted_value,
        version=row.version,
        settings=settings,
    )
    return row


async def get_twscrape_credentials(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
) -> TwscrapeCredential:
    try:
        cached = await redis.get(CACHE_KEY)
        if cached:
            payload = json.loads(cached)
            encrypted = payload.get("encrypted_value")
            version = int(payload.get("version") or 0)
            if isinstance(encrypted, str) and version > 0:
                return _parse(decrypt_token(encrypted, settings), version)
    except XCredentialUnavailableError:
        raise
    except Exception:
        logger.warning("Unable to read encrypted twscrape credential cache", exc_info=True)

    row = await session.scalar(
        select(XCredential).where(XCredential.credential_type == CREDENTIAL_TYPE)
    )
    if row is None:
        raise XCredentialUnavailableError("twscrape cookies have not been configured")
    credential = _parse(decrypt_token(row.encrypted_value, settings), row.version)
    await _write_cache(
        redis,
        encrypted_value=row.encrypted_value,
        version=row.version,
        settings=settings,
    )
    return credential


async def delete_twscrape_credentials(session: AsyncSession, redis: Redis) -> bool:
    row = await session.scalar(
        select(XCredential).where(XCredential.credential_type == CREDENTIAL_TYPE)
    )
    deleted = row is not None
    if row is not None:
        await session.delete(row)
        await session.commit()
    try:
        await redis.delete(CACHE_KEY)
    except Exception:
        logger.warning("Unable to clear encrypted twscrape credential cache", exc_info=True)
    return deleted


async def twscrape_cache_status(redis: Redis) -> tuple[bool, int | None]:
    try:
        ttl = int(await redis.ttl(CACHE_KEY))
        return ttl > 0, ttl if ttl > 0 else None
    except Exception:
        return False, None
