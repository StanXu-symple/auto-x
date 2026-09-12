from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import APIError
from app.db.session import AsyncSessionFactory, get_db
from app.models.admin import Admin
from app.models.service_auth import ServiceAuthRevocation, ServiceAuthSession
from app.services.auth_center import AuthCenterClient, AuthCenterUnavailable

DbSession = Annotated[AsyncSession, Depends(get_db)]
bearer_scheme = HTTPBearer(auto_error=False)


async def get_redis(request: Request) -> Redis:
    return request.app.state.redis


RedisClient = Annotated[Redis, Depends(get_redis)]


def _auth_center(request: Request) -> AuthCenterClient:
    client = getattr(request.app.state, "auth_center", None)
    if not isinstance(client, AuthCenterClient):
        raise APIError(503, "auth_unavailable", "Authentication service is unavailable")
    return client


async def authenticate_admin(
    db: AsyncSession,
    token: str,
    auth_center: AuthCenterClient,
) -> Admin:
    try:
        payload = await auth_center.verify_user_token(token)
        user_id = payload["uid"]
        sid = payload["sid"]
        jti = payload["jti"]
    except AuthCenterUnavailable:
        raise APIError(503, "auth_unavailable", "Authentication service is unavailable") from None
    except (InvalidTokenError, KeyError, TypeError, ValueError):
        raise APIError(401, "invalid_token", "The access token is invalid or expired") from None

    now = datetime.now(UTC)
    session = await db.scalar(
        select(ServiceAuthSession).where(
            ServiceAuthSession.sid == sid,
            ServiceAuthSession.admin_id == user_id,
            ServiceAuthSession.jti == jti,
            ServiceAuthSession.revoked_at.is_(None),
            ServiceAuthSession.expires_at > now,
        )
    )
    if session is None:
        raise APIError(401, "invalid_token", "The administrator session is no longer active")
    revoked = await db.scalar(
        select(ServiceAuthRevocation.jti).where(ServiceAuthRevocation.jti == jti)
    )
    if revoked is not None:
        raise APIError(401, "invalid_token", "The administrator session has been revoked")

    admin = await db.scalar(select(Admin).where(Admin.id == user_id, Admin.is_active.is_(True)))
    if admin is None or admin.username != payload["sub"]:
        raise APIError(401, "invalid_token", "The administrator no longer exists")
    return admin


async def get_current_admin(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Admin:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise APIError(401, "not_authenticated", "Authentication credentials were not provided")
    return await authenticate_admin(db, credentials.credentials, _auth_center(request))


CurrentAdmin = Annotated[Admin, Depends(get_current_admin)]


async def get_current_admin_for_stream(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Admin:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise APIError(401, "not_authenticated", "Authentication credentials were not provided")
    async with AsyncSessionFactory() as db:
        return await authenticate_admin(db, credentials.credentials, _auth_center(request))


StreamCurrentAdmin = Annotated[Admin, Depends(get_current_admin_for_stream)]
