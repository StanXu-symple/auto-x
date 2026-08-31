from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash

from app.core.config import Settings, get_settings

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, password_digest: str) -> bool:
    try:
        return password_hash.verify(password, password_digest)
    except (ValueError, TypeError):
        return False


def create_access_token(
    subject: str,
    *,
    user_id: int,
    settings: Settings | None = None,
    expires_delta: timedelta | None = None,
) -> tuple[str, int]:
    config = settings or get_settings()
    lifetime = expires_delta or timedelta(minutes=config.jwt_expire_minutes)
    now = datetime.now(UTC)
    expires = now + lifetime
    payload: dict[str, Any] = {
        "sub": subject,
        "uid": user_id,
        "iat": now,
        "nbf": now,
        "exp": expires,
        "iss": config.app_name,
        "type": "access",
    }
    token = jwt.encode(payload, config.jwt_secret_key, algorithm=config.jwt_algorithm)
    return token, int(lifetime.total_seconds())


def decode_access_token(token: str, settings: Settings | None = None) -> dict[str, Any]:
    config = settings or get_settings()
    payload = jwt.decode(
        token,
        config.jwt_secret_key,
        algorithms=[config.jwt_algorithm],
        issuer=config.app_name,
        options={"require": ["sub", "uid", "exp", "iat", "type"]},
    )
    if payload.get("type") != "access":
        raise InvalidTokenError("Unexpected token type")
    return payload
