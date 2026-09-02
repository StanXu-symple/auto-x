from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class XiaohongshuConnection(Base):
    __tablename__ = "xiaohongshu_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    name: Mapped[str] = mapped_column(String(100), default="小红书 MCP")
    connector: Mapped[str] = mapped_column(
        String(32), default="xiaohongshu_mcp", server_default="xiaohongshu_mcp"
    )
    mcp_url: Mapped[str] = mapped_column(String(500))
    encrypted_auth_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_hint: Mapped[str | None] = mapped_column(String(16), nullable=True)
    token_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(24), default="unverified", server_default="unverified"
    )
    login_status: Mapped[str] = mapped_column(
        String(24), default="unknown", server_default="unknown"
    )
    risk_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class XiaohongshuPublishSetting(Base):
    __tablename__ = "xiaohongshu_publish_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    default_strategy: Mapped[str] = mapped_column(
        String(24), default="manual", server_default="manual"
    )
    default_delay_minutes: Mapped[int] = mapped_column(Integer, default=60, server_default="60")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    daily_publish_limit: Mapped[int] = mapped_column(Integer, default=10, server_default="10")
    default_visibility: Mapped[str] = mapped_column(
        String(32), default="公开可见", server_default="公开可见"
    )
    declare_original: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class XiaohongshuPublishJob(Base):
    __tablename__ = "xiaohongshu_publish_jobs"
    __table_args__ = (
        Index("ix_xhs_publish_jobs_due", "status", "scheduled_at"),
        Index("ix_xhs_publish_jobs_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_ai_draft_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_drafts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    images: Mapped[list[str]] = mapped_column(JSON)
    tags: Mapped[list[str]] = mapped_column(JSON)
    products: Mapped[list[str]] = mapped_column(JSON)
    visibility: Mapped[str] = mapped_column(String(32), default="公开可见")
    is_original: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    strategy: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="draft", server_default="draft")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    platform_note_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    platform_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    response_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
