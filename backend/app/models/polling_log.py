from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow


class PollingLog(Base):
    __tablename__ = "polling_logs"
    __table_args__ = (Index("ix_polling_logs_started_status", "started_at", "status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    monitored_user_id: Mapped[int] = mapped_column(
        ForeignKey("monitored_users.id", ondelete="CASCADE"), index=True
    )
    trigger: Mapped[str] = mapped_column(String(16), default="scheduled")
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tweets_fetched: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tweets_inserted: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    rate_limit_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    monitored_user: Mapped["MonitoredUser"] = relationship(  # noqa: F821
        back_populates="polling_logs"
    )
