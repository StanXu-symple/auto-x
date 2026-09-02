from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class XCredential(Base):
    __tablename__ = "x_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    credential_type: Mapped[str] = mapped_column(
        String(32), unique=True, default="app_bearer", server_default="app_bearer"
    )
    encrypted_value: Mapped[str] = mapped_column(Text)
    token_hint: Mapped[str] = mapped_column(String(16))
    token_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    acquisition_method: Mapped[str] = mapped_column(String(32))
    verification_status: Mapped[str] = mapped_column(
        String(24), default="unverified", server_default="unverified"
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
