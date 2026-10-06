from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class AIPublishDispatch(Base):
    """One durable automatic publish decision for one generated draft and channel."""

    __tablename__ = "ai_publish_dispatches"
    __table_args__ = (
        UniqueConstraint("job_id", "channel", name="uq_ai_publish_dispatch_job_channel"),
        Index("ix_ai_publish_dispatches_due", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("ai_generation_jobs.id", ondelete="CASCADE"), index=True
    )
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("ai_drafts.id", ondelete="CASCADE"), index=True
    )
    owner_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admins.id", ondelete="SET NULL"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(16))
    # pending/retry_wait -> dispatching (XHS) or accepted (QQ) -> published;
    # failed and uncertain require a human decision and are never auto retried.
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rejection_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    payload_snapshot: Mapped[dict] = mapped_column(JSON)
    article_publish_attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
