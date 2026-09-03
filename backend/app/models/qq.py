from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow


class QQBotAccount(Base):
    __tablename__ = "qq_bot_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    app_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    encrypted_app_secret: Mapped[str] = mapped_column(Text)
    secret_hint: Mapped[str] = mapped_column(String(16))
    secret_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
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

    targets: Mapped[list[QQNotificationTarget]] = relationship(
        back_populates="bot", cascade="all, delete-orphan", passive_deletes=True
    )


class QQNotificationTarget(Base):
    __tablename__ = "qq_notification_targets"
    __table_args__ = (Index("uq_qq_notification_target", "bot_id", "group_openid", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id: Mapped[int] = mapped_column(
        ForeignKey("qq_bot_accounts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    group_openid: Mapped[str] = mapped_column(String(128))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    all_monitored_users: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    message_template: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    bot: Mapped[QQBotAccount] = relationship(back_populates="targets")
    subscriptions: Mapped[list[QQTargetSubscription]] = relationship(
        back_populates="target", cascade="all, delete-orphan", passive_deletes=True
    )


class QQTargetSubscription(Base):
    __tablename__ = "qq_target_subscriptions"

    target_id: Mapped[int] = mapped_column(
        ForeignKey("qq_notification_targets.id", ondelete="CASCADE"), primary_key=True
    )
    monitored_user_id: Mapped[int] = mapped_column(
        ForeignKey("monitored_users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    target: Mapped[QQNotificationTarget] = relationship(back_populates="subscriptions")


class QQDelivery(Base):
    __tablename__ = "qq_deliveries"
    __table_args__ = (
        Index("ix_qq_deliveries_due", "status", "next_attempt_at"),
        Index("ix_qq_deliveries_target_created", "target_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    target_id: Mapped[int | None] = mapped_column(
        ForeignKey("qq_notification_targets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_tweet_id: Mapped[int | None] = mapped_column(
        ForeignKey("tweets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(24), default="tweet", server_default="tweet")
    idempotency_key: Mapped[str] = mapped_column(String(191), unique=True)
    bot_name: Mapped[str] = mapped_column(String(100))
    bot_app_id: Mapped[str] = mapped_column(String(64))
    bot_version: Mapped[int] = mapped_column(Integer)
    target_name: Mapped[str] = mapped_column(String(100))
    group_openid: Mapped[str] = mapped_column(String(128))
    message_body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="queued", server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
