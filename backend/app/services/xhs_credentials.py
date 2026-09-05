from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.xhs_credential import XiaohongshuCredential
from app.services.x_credentials import decrypt_token, encrypt_token


@dataclass(frozen=True, slots=True)
class XiaohongshuCredentialValue:
    a1: str
    web_session: str
    version: int


async def has_xhs_credentials(session: AsyncSession, *, admin_id: int) -> bool:
    return await session.get(XiaohongshuCredential, admin_id) is not None


async def save_xhs_credentials(
    session: AsyncSession,
    settings: Settings,
    *,
    admin_id: int,
    a1: str,
    web_session: str,
) -> XiaohongshuCredential:
    row = await session.get(XiaohongshuCredential, admin_id)
    now = datetime.now(UTC)
    encrypted_a1 = encrypt_token(a1, settings)
    encrypted_web_session = encrypt_token(web_session, settings)
    if row is None:
        row = XiaohongshuCredential(
            admin_id=admin_id,
            encrypted_a1=encrypted_a1,
            encrypted_web_session=encrypted_web_session,
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.encrypted_a1 = encrypted_a1
        row.encrypted_web_session = encrypted_web_session
        row.version += 1
        row.updated_at = now
    await session.commit()
    await session.refresh(row)
    return row


async def get_xhs_credentials(
    session: AsyncSession,
    settings: Settings,
    *,
    admin_id: int,
) -> XiaohongshuCredentialValue | None:
    row = await session.get(XiaohongshuCredential, admin_id)
    if row is None:
        return None
    return XiaohongshuCredentialValue(
        a1=decrypt_token(row.encrypted_a1, settings),
        web_session=decrypt_token(row.encrypted_web_session, settings),
        version=row.version,
    )
