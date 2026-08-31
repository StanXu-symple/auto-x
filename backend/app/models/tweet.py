from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow


class Tweet(Base):
    __tablename__ = "tweets"
    __table_args__ = (Index("ix_tweets_user_posted", "monitored_user_id", "posted_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tweet_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    monitored_user_id: Mapped[int] = mapped_column(
        ForeignKey("monitored_users.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[str] = mapped_column(String(32), index=True)
    text: Mapped[str] = mapped_column(Text)
    lang: Mapped[str | None] = mapped_column(String(16), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    like_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    retweet_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reply_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    quote_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    bookmark_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    impression_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    entities: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    attachments: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    referenced_tweets: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    monitored_user: Mapped["MonitoredUser"] = relationship(back_populates="tweets")  # noqa: F821
