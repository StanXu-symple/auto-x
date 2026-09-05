from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import APIError
from app.core.security import decode_access_token
from app.db.session import AsyncSessionFactory, get_db
from app.models.admin import Admin

DbSession = Annotated[AsyncSession, Depends(get_db)]
bearer_scheme = HTTPBearer(auto_error=False)


async def get_redis(request: Request) -> Redis:
    return request.app.state.redis


RedisClient = Annotated[Redis, Depends(get_redis)]


async def get_current_admin(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Admin:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise APIError(401, "not_authenticated", "Authentication credentials were not provided")
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = int(payload["uid"])
    except (InvalidTokenError, KeyError, TypeError, ValueError):
        raise APIError(401, "invalid_token", "The access token is invalid or expired") from None

    admin = await db.scalar(select(Admin).where(Admin.id == user_id, Admin.is_active.is_(True)))
    if admin is None:
        raise APIError(401, "invalid_token", "The administrator no longer exists")
    return admin


CurrentAdmin = Annotated[Admin, Depends(get_current_admin)]


async def get_current_admin_for_stream(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Admin:
    async with AsyncSessionFactory() as db:
        return await get_current_admin(db, credentials)


StreamCurrentAdmin = Annotated[Admin, Depends(get_current_admin_for_stream)]
