from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow


class MonitoredUser(Base):
    __tablename__ = "monitored_users"
    __table_args__ = (Index("ix_monitored_users_due", "is_active", "next_poll_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    x_user_id: Mapped[str | None] = mapped_column(
        String(32), unique=True, nullable=True, index=True
    )
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    include_replies: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    include_retweets: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    poll_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="idle", server_default="idle")
    last_tweet_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pagination_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pagination_since_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pagination_newest_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    manual_poll_token: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    poll_generation: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_poll_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    tweets: Mapped[list["Tweet"]] = relationship(  # noqa: F821
        back_populates="monitored_user", cascade="all, delete-orphan", passive_deletes=True
    )
    polling_logs: Mapped[list["PollingLog"]] = relationship(  # noqa: F821
        back_populates="monitored_user", cascade="all, delete-orphan", passive_deletes=True
    )
