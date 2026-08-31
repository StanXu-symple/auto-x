import hashlib
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.deps import CurrentAdmin, DbSession, RedisClient
from app.api.errors import APIError
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models.admin import Admin
from app.schemas.auth import AdminPublic, LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = logging.getLogger(__name__)
settings = get_settings()
DUMMY_PASSWORD_HASH = hash_password("x-sentinel-invalid-login-dummy-password")

LOGIN_FAILURE_SCRIPT = """
local attempts = redis.call('incr', KEYS[1])
if attempts == 1 then
  redis.call('expire', KEYS[1], ARGV[1])
end
local ttl = redis.call('ttl', KEYS[1])
return {attempts, ttl}
"""


def _login_rate_key(request: Request, username: str) -> str:
    client_ip = request.client.host if request.client else "unknown"
    digest = hashlib.sha256(f"{client_ip}|{username.strip().lower()}".encode()).hexdigest()
    return f"xsentinel:login:failures:{digest}"


def _rate_limited(ttl: int) -> APIError:
    retry_after = max(1, ttl)
    return APIError(
        429,
        "login_rate_limited",
        "Too many failed login attempts; try again later",
        headers={"Retry-After": str(retry_after)},
    )


async def _check_login_limit(redis: RedisClient, key: str) -> None:
    try:
        attempts = int(await redis.get(key) or 0)
        if attempts >= settings.login_rate_limit_attempts:
            raise _rate_limited(int(await redis.ttl(key)))
    except APIError:
        raise
    except Exception:
        logger.exception("Login rate-limit precheck failed open")


async def _record_login_failure(redis: RedisClient, key: str) -> None:
    try:
        attempts, ttl = await redis.eval(
            LOGIN_FAILURE_SCRIPT,
            1,
            key,
            settings.login_rate_limit_window_seconds,
        )
        if int(attempts) >= settings.login_rate_limit_attempts:
            raise _rate_limited(int(ttl))
    except APIError:
        raise
    except Exception:
        logger.exception("Login rate-limit update failed open")


async def _clear_login_limit(redis: RedisClient, key: str) -> None:
    try:
        await redis.delete(key)
    except Exception:
        logger.exception("Could not clear successful login rate-limit state")


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: DbSession,
    redis: RedisClient,
) -> TokenResponse:
    rate_key = _login_rate_key(request, payload.username)
    await _check_login_limit(redis, rate_key)
    admin = await db.scalar(select(Admin).where(Admin.username == payload.username.strip()))
    password_digest = admin.password_hash if admin is not None else DUMMY_PASSWORD_HASH
    valid_password = verify_password(payload.password, password_digest)
    if admin is None or not admin.is_active or not valid_password:
        await _record_login_failure(redis, rate_key)
        raise APIError(401, "invalid_credentials", "Invalid username or password")

    admin.last_login_at = datetime.now(UTC)
    await db.commit()
    await _clear_login_limit(redis, rate_key)
    token, expires_in = create_access_token(admin.username, user_id=admin.id)
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=AdminPublic.model_validate(admin),
    )


@router.get("/me", response_model=AdminPublic)
async def current_admin(admin: CurrentAdmin) -> AdminPublic:
    return AdminPublic.model_validate(admin)
