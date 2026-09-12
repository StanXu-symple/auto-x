from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import os
import time
import uuid
from collections.abc import Awaitable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr
from redis.asyncio import Redis
from sqlalchemy import select, text

from app import __version__
from app.control_plane.auth_store import (
    ALGORITHM,
    ISSUER,
    SESSION_CACHE_PREFIX,
    USER_AUDIENCE,
    AuthStore,
    KeyEncryptionKeyMismatchError,
    resolve_key_encryption_secret,
)
from app.control_plane.config import load_runtime_config, runtime_float, runtime_int
from app.control_plane.contracts import ServiceTokenResponse
from app.control_plane.nacos import (
    NacosClient,
    NacosServiceRegistration,
    advertise_identity,
)
from app.core.config import get_settings
from app.core.security import hash_password, verify_password
from app.db.base import Base
from app.db.session import AsyncSessionFactory, engine
from app.models import service_auth as _service_auth_models  # noqa: F401
from app.models.admin import Admin
from app.schemas.auth import AdminPublic, ChangePasswordRequest, LoginRequest, TokenResponse
from app.schemas.common import MessageResponse

logger = logging.getLogger(__name__)
settings = get_settings()
DUMMY_PASSWORD_HASH = hash_password("x-sentinel-invalid-login-dummy-password")

_TRUSTED_PROXY_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::1/128",
        "fc00::/7",
    )
)

RATE_LIMIT_SCRIPT = """
local attempts = redis.call('incr', KEYS[1])
if attempts == 1 then
  redis.call('expire', KEYS[1], ARGV[1])
end
local ttl = redis.call('ttl', KEYS[1])
return {attempts, ttl}
"""


class TokenRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=128)
    client_secret: SecretStr = Field(min_length=32, max_length=256)
    audience: str = Field(min_length=1, max_length=128)


def _remote_address(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    try:
        peer_address = ipaddress.ip_address(peer)
    except ValueError:
        peer_address = None
    trusted_peer = peer_address is not None and any(
        peer_address in network for network in _TRUSTED_PROXY_NETWORKS
    )
    forwarded_values = request.headers.getlist("x-forwarded-for")
    if trusted_peer and len(forwarded_values) == 1:
        # The backend sends one canonical address. Never select an address from
        # a chain: that would make the attacker-controlled left-most value the
        # rate-limit identity when an upstream proxy was configured to append.
        forwarded = forwarded_values[0].strip()
        try:
            if forwarded and "," not in forwarded and len(forwarded) <= 64:
                return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return peer[:64]


def _rate_key(category: str, request: Request, identity: str) -> str:
    digest = hashlib.sha256(
        f"{_remote_address(request)}|{identity.strip().lower()}".encode()
    ).hexdigest()
    return f"xsentinel:auth:rate:{category}:{digest}"


def _ip_rate_key(category: str, request: Request) -> str:
    digest = hashlib.sha256(_remote_address(request).encode()).hexdigest()
    return f"xsentinel:auth:rate:{category}-ip:{digest}"


async def _check_limit(redis: Redis, key: str, limit: int) -> None:
    try:
        attempts = int(await redis.get(key) or 0)
        if attempts >= limit:
            ttl = max(1, int(await redis.ttl(key)))
            raise HTTPException(
                429,
                "Authentication request limit reached",
                headers={"Retry-After": str(ttl)},
            )
    except HTTPException:
        raise
    except Exception as exc:
        # The authentication authority depends on Redis for a consistent
        # multi-replica rate limit. Do not silently downgrade to per-process.
        raise HTTPException(503, "Authentication state store unavailable") from exc


async def _record_attempt(redis: Redis, key: str, limit: int, window: int) -> None:
    try:
        attempts, ttl = await redis.eval(RATE_LIMIT_SCRIPT, 1, key, window)
    except Exception as exc:
        raise HTTPException(503, "Authentication state store unavailable") from exc
    if int(attempts) >= limit:
        raise HTTPException(
            429,
            "Authentication request limit reached",
            headers={"Retry-After": str(max(1, int(ttl)))},
        )


async def _cleanup_loop(store: AuthStore) -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            await store.cleanup_expired()
            await store.ensure_active_signing_key()
        except Exception:  # noqa: BLE001 - next pass retries; readiness catches failures
            logger.exception("Authentication key maintenance failed")


async def _authority_ready(store: AuthStore, redis: Redis) -> None:
    """Raise unless this replica can serve the shared authentication state."""

    async with AsyncSessionFactory() as session:
        await session.execute(text("SELECT 1"))
    await redis.ping()
    await store.active_signing_material()


async def _best_effort_after_commit(operation: Awaitable[Any], description: str) -> None:
    """Run cache/audit work without changing an already committed security result."""

    try:
        await operation
    except Exception:  # noqa: BLE001 - PostgreSQL remains the security authority
        logger.exception("Post-commit authentication task failed: %s", description)


async def _cache_token_revocation(store: AuthStore, redis: Redis, sid: str, exp: Any) -> None:
    expires_at = datetime.fromtimestamp(float(exp), UTC)
    await store.cache_revoked_sessions([(sid, expires_at)], redis=redis)


def _bearer_token(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            401,
            "Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


async def _current_admin(request: Request) -> tuple[dict[str, Any], Admin]:
    result = await request.app.state.store.decode_and_validate_user_token(
        _bearer_token(request), redis=request.app.state.redis
    )
    if result is None:
        raise HTTPException(
            401,
            "Invalid or revoked access token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return result


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime: dict[str, Any] = {}
        nacos = None
        registration = None
        http = None
        cleanup = None
        redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )
        try:
            if settings.auto_create_tables:
                async with engine.begin() as connection:
                    await connection.run_sync(Base.metadata.create_all)
            async with AsyncSessionFactory() as session:
                await session.execute(text("SELECT 1"))
            await redis.ping()

            if os.environ.get("NACOS_SERVER_ADDR"):
                timeout = runtime_float(
                    {},
                    "nacos_config_timeout_seconds",
                    "NACOS_CONFIG_TIMEOUT_SECONDS",
                    3,
                    minimum=0.1,
                    maximum=30,
                )
                http = httpx.AsyncClient(timeout=timeout, trust_env=False)
                nacos = NacosClient(
                    http,
                    os.environ["NACOS_SERVER_ADDR"],
                    os.environ.get("NACOS_NAMESPACE", "public"),
                    os.environ.get("NACOS_GROUP", "X_SENTINEL"),
                    os.environ.get("NACOS_USERNAME", ""),
                    os.environ.get("NACOS_PASSWORD", ""),
                )
                runtime = await load_runtime_config(nacos)
            else:
                runtime = await load_runtime_config(None)

            master_secret = resolve_key_encryption_secret(
                settings.environment,
                os.environ.get("SERVICE_AUTH_KEY_ENCRYPTION_KEY"),
                legacy_x_token_secret=settings.x_token_encryption_key,
                legacy_jwt_secret=settings.jwt_secret_key,
            )
            service_token_lifetime = runtime_int(
                runtime,
                "service_auth_token_lifetime_seconds",
                "SERVICE_AUTH_TOKEN_LIFETIME_SECONDS",
                120,
                minimum=30,
                maximum=3600,
            )
            store = AuthStore(
                AsyncSessionFactory,
                master_secret=master_secret,
                # Files are one-time migration inputs only. PostgreSQL becomes
                # authoritative as soon as the rows are inserted.
                legacy_private_key_file=os.environ.get("SERVICE_AUTH_PRIVATE_KEY_FILE", ""),
                legacy_clients_file=os.environ.get("SERVICE_AUTH_CLIENTS_FILE", ""),
                rotation_days=runtime_int(
                    runtime,
                    "service_auth_key_rotation_days",
                    "SERVICE_AUTH_KEY_ROTATION_DAYS",
                    30,
                    minimum=1,
                    maximum=365,
                ),
                overlap_days=runtime_int(
                    runtime,
                    "service_auth_key_overlap_days",
                    "SERVICE_AUTH_KEY_OVERLAP_DAYS",
                    7,
                    minimum=1,
                    maximum=90,
                ),
                # Never remove a retired public key while a token signed by it
                # may still be valid. Include room for verifier JWKS caches and
                # small clock differences between nodes.
                minimum_overlap=max(
                    timedelta(minutes=settings.jwt_expire_minutes),
                    timedelta(seconds=service_token_lifetime),
                )
                + timedelta(minutes=10),
            )
            await store.initialize()
            app.state.store = store
            app.state.redis = redis
            app.state.rate_limit = runtime_int(
                runtime,
                "service_auth_rate_limit",
                "SERVICE_AUTH_RATE_LIMIT",
                120,
                minimum=1,
                maximum=10000,
            )
            app.state.rate_window = runtime_int(
                runtime,
                "service_auth_rate_window_seconds",
                "SERVICE_AUTH_RATE_WINDOW_SECONDS",
                60,
                minimum=10,
                maximum=3600,
            )
            app.state.login_limit = runtime_int(
                runtime,
                "login_rate_limit_attempts",
                "LOGIN_RATE_LIMIT_ATTEMPTS",
                settings.login_rate_limit_attempts,
                minimum=1,
                maximum=100,
            )
            app.state.login_window = runtime_int(
                runtime,
                "login_rate_limit_window_seconds",
                "LOGIN_RATE_LIMIT_WINDOW_SECONDS",
                settings.login_rate_limit_window_seconds,
                minimum=30,
                maximum=3600,
            )
            app.state.token_lifetime = service_token_lifetime
            app.state.user_lifetime = timedelta(minutes=settings.jwt_expire_minutes)

            if nacos is not None:

                async def registration_ready() -> bool:
                    try:
                        await _authority_ready(store, redis)
                    except Exception:  # noqa: BLE001 - heartbeat removes an unready replica
                        return False
                    return True

                ip, port = advertise_identity()
                service_name = os.environ.get("NACOS_SERVICE_NAME", "xsentinel-auth-center")
                registration = NacosServiceRegistration(
                    http,
                    nacos,
                    service_name,
                    ip,
                    port,
                    metadata={"component": "auth-center", "version": __version__},
                    readiness=registration_ready,
                )
                await registration.start()
            cleanup = asyncio.create_task(_cleanup_loop(store))
            yield
        finally:
            if cleanup:
                cleanup.cancel()
                with suppress(asyncio.CancelledError):
                    await cleanup
            if registration:
                await registration.aclose()
            elif http:
                await http.aclose()
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(
        title="X Sentinel authentication authority",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health/live")
    async def live():
        return {"status": "healthy"}

    @app.get("/health/ready")
    async def ready(request: Request):
        try:
            await _authority_ready(request.app.state.store, request.app.state.redis)
        except KeyEncryptionKeyMismatchError as exc:
            logger.error("Authentication authority KEK mismatch: %s", exc)
            raise HTTPException(
                503,
                "Shared signing key cannot be decrypted; verify "
                "SERVICE_AUTH_KEY_ENCRYPTION_KEY on every auth-center replica",
            ) from None
        except Exception as exc:  # noqa: BLE001 - readiness deliberately collapses details
            logger.warning("Authentication authority is not ready: %s", exc)
            raise HTTPException(503, "Authentication authority is not ready") from None
        return {"status": "healthy", "postgres": "healthy", "redis": "healthy"}

    @app.get("/.well-known/jwks.json")
    async def jwks(request: Request, response: Response):
        response.headers["Cache-Control"] = "public, max-age=60, must-revalidate"
        return await request.app.state.store.jwks()

    @app.post("/v1/token")
    async def issue(request_body: TokenRequest, request: Request) -> ServiceTokenResponse:
        rate_key = _rate_key("service", request, request_body.client_id)
        await _record_attempt(
            request.app.state.redis,
            rate_key,
            request.app.state.rate_limit,
            request.app.state.rate_window,
        )
        scopes = await request.app.state.store.authenticate_client(
            request_body.client_id,
            request_body.client_secret.get_secret_value(),
            request_body.audience,
        )
        if scopes is None:
            await request.app.state.store.audit(
                "service_token_denied",
                actor_type="service",
                actor_id=request_body.client_id,
                audience=request_body.audience,
                success=False,
                remote_address=_remote_address(request),
            )
            raise HTTPException(401, "Invalid service credentials or audience")
        signing = await request.app.state.store.active_signing_material()
        now = int(time.time())
        jti = str(uuid.uuid4())
        lifetime = request.app.state.token_lifetime
        token = jwt.encode(
            {
                "sub": request_body.client_id,
                "aud": request_body.audience,
                "iss": ISSUER,
                "iat": now,
                "nbf": now,
                "exp": now + lifetime,
                "jti": jti,
                "kid": signing.kid,
                "type": "service",
                "scope": scopes,
            },
            signing.private_key,
            algorithm=ALGORITHM,
            headers={"kid": signing.kid, "typ": "JWT"},
        )
        await request.app.state.store.audit(
            "service_token_issued",
            actor_type="service",
            actor_id=request_body.client_id,
            audience=request_body.audience,
            jti=jti,
            success=True,
            remote_address=_remote_address(request),
        )
        return ServiceTokenResponse(access_token=token, expires_in=lifetime)

    @app.post("/v1/admin/login", response_model=TokenResponse)
    async def admin_login(payload: LoginRequest, request: Request) -> TokenResponse:
        username = payload.username.strip()
        account_rate_key = _rate_key("admin", request, username)
        ip_rate_key = _ip_rate_key("admin", request)
        # The account bucket slows targeted guessing; the independent IP bucket
        # prevents an attacker from defeating Argon2 CPU protection by rotating
        # arbitrary usernames.
        # Count every request before the expensive password hash. Unlike the
        # failed-account bucket this operation is atomic, so a concurrent burst
        # with many made-up usernames cannot all pass a read-only precheck.
        await _record_attempt(
            request.app.state.redis,
            ip_rate_key,
            request.app.state.login_limit,
            request.app.state.login_window,
        )
        await _check_limit(request.app.state.redis, account_rate_key, request.app.state.login_limit)
        signing = await request.app.state.store.active_signing_material()
        auth_session = None
        async with AsyncSessionFactory() as session, session.begin():
            admin = await session.scalar(
                select(Admin).where(Admin.username == username).with_for_update()
            )
            password_digest = admin.password_hash if admin is not None else DUMMY_PASSWORD_HASH
            valid = verify_password(payload.password, password_digest)
            if admin is not None and admin.is_active and valid:
                admin.last_login_at = datetime.now(UTC)
                # Keep credential verification, the last-login update and the
                # durable session insert under the same administrator row lock.
                # A concurrent password change will therefore revoke this
                # session or make this login re-check the new password hash.
                auth_session = await request.app.state.store.create_user_session(
                    admin.id,
                    lifetime=request.app.state.user_lifetime,
                    session=session,
                )
        if admin is None or not admin.is_active or not valid:
            try:
                await _record_attempt(
                    request.app.state.redis,
                    account_rate_key,
                    request.app.state.login_limit,
                    request.app.state.login_window,
                )
            finally:
                await request.app.state.store.audit(
                    "admin_login_denied",
                    actor_type="user",
                    actor_id=username or None,
                    success=False,
                    remote_address=_remote_address(request),
                )
            raise HTTPException(401, "Invalid username or password")
        if auth_session is None:  # Defensive narrowing; successful auth always creates it.
            raise HTTPException(503, "Authentication session could not be created")
        try:
            await request.app.state.redis.delete(account_rate_key)
        except Exception:  # noqa: BLE001 - durable session is already committed
            logger.exception("Unable to clear successful login rate-limit state")
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": admin.username,
                "uid": admin.id,
                "sid": auth_session.sid,
                "jti": auth_session.jti,
                "kid": signing.kid,
                "aud": USER_AUDIENCE,
                "iss": ISSUER,
                "iat": int(now.timestamp()),
                "nbf": int(now.timestamp()),
                "exp": int(auth_session.expires_at.timestamp()),
                "type": "user",
                "scope": "admin",
            },
            signing.private_key,
            algorithm=ALGORITHM,
            headers={"kid": signing.kid, "typ": "JWT"},
        )
        lifetime = max(1, int((auth_session.expires_at - now).total_seconds()))
        await request.app.state.redis.set(
            f"{SESSION_CACHE_PREFIX}{auth_session.sid}", "active", ex=lifetime
        )
        await request.app.state.store.audit(
            "admin_login_succeeded",
            actor_type="user",
            actor_id=str(admin.id),
            subject=admin.username,
            sid=auth_session.sid,
            jti=auth_session.jti,
            success=True,
            remote_address=_remote_address(request),
        )
        return TokenResponse(
            access_token=token,
            expires_in=lifetime,
            user=AdminPublic.model_validate(admin),
        )

    @app.get("/v1/admin/me", response_model=AdminPublic)
    async def admin_me(request: Request) -> AdminPublic:
        _claims, admin = await _current_admin(request)
        return AdminPublic.model_validate(admin)

    @app.patch("/v1/admin/password", response_model=MessageResponse)
    async def admin_password(payload: ChangePasswordRequest, request: Request) -> MessageResponse:
        claims, admin = await _current_admin(request)
        if payload.new_password == payload.current_password:
            raise HTTPException(400, "New password must be different from the current password")
        async with AsyncSessionFactory() as session, session.begin():
            persisted = await session.get(Admin, admin.id, with_for_update=True)
            if persisted is None or not persisted.is_active:
                raise HTTPException(401, "Administrator is inactive")
            # Recheck under the row lock so concurrent password changes cannot
            # overwrite one another using a stale credential.
            if not verify_password(payload.current_password, persisted.password_hash):
                raise HTTPException(400, "Current password is incorrect")
            persisted.password_hash = hash_password(payload.new_password)
            revoked = await request.app.state.store.revoke_admin_sessions_in_transaction(
                session, admin.id, reason="password_changed"
            )
        await _best_effort_after_commit(
            request.app.state.store.cache_revoked_sessions(revoked, redis=request.app.state.redis),
            "cache password-change revocations",
        )
        await _best_effort_after_commit(
            request.app.state.store.audit(
                "admin_password_changed",
                actor_type="user",
                actor_id=str(admin.id),
                subject=admin.username,
                sid=str(claims["sid"]),
                jti=str(claims["jti"]),
                success=True,
                remote_address=_remote_address(request),
            ),
            "audit password change",
        )
        return MessageResponse(message="Password updated successfully")

    @app.post("/v1/admin/logout", response_model=MessageResponse)
    async def admin_logout(request: Request) -> MessageResponse:
        claims, admin = await _current_admin(request)
        sid = str(claims["sid"])
        await request.app.state.store.revoke_session(sid, reason="logout", redis=None)
        await _best_effort_after_commit(
            _cache_token_revocation(
                request.app.state.store,
                request.app.state.redis,
                sid,
                claims["exp"],
            ),
            "cache logout revocation",
        )
        await _best_effort_after_commit(
            request.app.state.store.audit(
                "admin_logout",
                actor_type="user",
                actor_id=str(admin.id),
                subject=admin.username,
                sid=sid,
                jti=str(claims["jti"]),
                success=True,
                remote_address=_remote_address(request),
            ),
            "audit logout",
        )
        return MessageResponse(message="Logged out successfully")

    return app


app = create_app()
