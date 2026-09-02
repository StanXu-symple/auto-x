from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class AIDataSource(Base):
    __tablename__ = "ai_data_sources"

    # The current product intentionally supports one unified AI account only.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    name: Mapped[str] = mapped_column(String(100), default="OpenAI")
    protocol: Mapped[str] = mapped_column(
        String(32), default="openai_responses", server_default="openai_responses"
    )
    base_url: Mapped[str] = mapped_column(String(500))
    model_name: Mapped[str] = mapped_column("model", String(128))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    key_hint: Mapped[str] = mapped_column(String(16))
    key_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
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
