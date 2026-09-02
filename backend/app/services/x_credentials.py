from __future__ import annotations

import base64
import hashlib
import json
import logging
from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.x_credential import XCredential

logger = logging.getLogger(__name__)

CACHE_KEY = "xsentinel:x-credentials:app-bearer:v1"
CREDENTIAL_TYPE = "app_bearer"
ACQUISITION_METHODS = {"developer_console", "api_exchange"}


class XCredentialUnavailableError(RuntimeError):
    pass


def _fernet(settings: Settings) -> Fernet:
    source = settings.x_token_encryption_key
    if len(source) < 32:
        raise XCredentialUnavailableError(
            "X token encryption key must contain at least 32 characters"
        )
    key = base64.urlsafe_b64encode(hashlib.sha256(source.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_token(token: str, settings: Settings) -> str:
    return _fernet(settings).encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_token(ciphertext: str, settings: Settings) -> str:
    try:
        return _fernet(settings).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise XCredentialUnavailableError(
            "Stored X token cannot be decrypted; check X_TOKEN_ENCRYPTION_KEY"
        ) from exc


def token_hint(token: str) -> str:
    return f"••••••••{token[-4:]}"


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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
        logger.warning("Unable to update encrypted X token cache", exc_info=True)


async def save_bearer_token(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    *,
    token: str,
    acquisition_method: str,
) -> XCredential:
    if acquisition_method not in ACQUISITION_METHODS:
        raise ValueError("Unsupported X credential acquisition method")
    encrypted = encrypt_token(token, settings)
    row = await session.scalar(
        select(XCredential).where(XCredential.credential_type == CREDENTIAL_TYPE)
    )
    now = datetime.now(UTC)
    if row is None:
        row = XCredential(
            id=1,
            credential_type=CREDENTIAL_TYPE,
            encrypted_value=encrypted,
            token_hint=token_hint(token),
            token_fingerprint=token_fingerprint(token),
            acquisition_method=acquisition_method,
            verification_status="unverified",
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.encrypted_value = encrypted
        row.token_hint = token_hint(token)
        row.token_fingerprint = token_fingerprint(token)
        row.acquisition_method = acquisition_method
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


async def get_bearer_token(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
) -> str:
    try:
        cached = await redis.get(CACHE_KEY)
        if cached:
            payload = json.loads(cached)
            ciphertext = payload.get("encrypted_value")
            if isinstance(ciphertext, str):
                return decrypt_token(ciphertext, settings)
    except XCredentialUnavailableError:
        raise
    except Exception:
        logger.warning("Unable to read encrypted X token cache", exc_info=True)

    row = await session.scalar(
        select(XCredential).where(XCredential.credential_type == CREDENTIAL_TYPE)
    )
    if row is not None:
        token = decrypt_token(row.encrypted_value, settings)
        await _write_cache(
            redis,
            encrypted_value=row.encrypted_value,
            version=row.version,
            settings=settings,
        )
        return token
    raise XCredentialUnavailableError("X Bearer Token has not been configured")


async def delete_bearer_token(session: AsyncSession, redis: Redis) -> bool:
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
        logger.warning("Unable to clear encrypted X token cache", exc_info=True)
    return deleted


async def cache_status(redis: Redis) -> tuple[bool, int | None]:
    try:
        ttl = int(await redis.ttl(CACHE_KEY))
        return ttl > 0, ttl if ttl > 0 else None
    except Exception:
        return False, None
