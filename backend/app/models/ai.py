from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow


class AISkill(Base):
    __tablename__ = "ai_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    instructions: Mapped[str] = mapped_column(Text)
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    remote_skill_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    remote_skill_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AIFeature(Base):
    __tablename__ = "ai_features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_prompt: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AIUserSkillBinding(Base):
    __tablename__ = "ai_user_skill_bindings"
    __table_args__ = (
        Index(
            "uq_ai_user_skill_binding",
            "monitored_user_id",
            "ai_feature_id",
            "skill_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    monitored_user_id: Mapped[int] = mapped_column(
        ForeignKey("monitored_users.id", ondelete="CASCADE"), index=True
    )
    ai_feature_id: Mapped[int] = mapped_column(
        ForeignKey("ai_features.id", ondelete="CASCADE"), index=True
    )
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("ai_skills.id", ondelete="CASCADE"), index=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AIUserProfile(Base):
    __tablename__ = "ai_user_profiles"

    monitored_user_id: Mapped[int] = mapped_column(
        ForeignKey("monitored_users.id", ondelete="CASCADE"), primary_key=True
    )
    identity_summary: Mapped[str] = mapped_column(Text, default="")
    focus_summary: Mapped[str] = mapped_column(Text, default="")
    relationship_summary: Mapped[str] = mapped_column(Text, default="")
    recurring_topics: Mapped[list[str]] = mapped_column(JSON)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(default=0.0, server_default="0")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    last_source_tweet_id: Mapped[int | None] = mapped_column(
        ForeignKey("tweets.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AISetting(Base):
    __tablename__ = "ai_settings"

    # A singleton row (id=1) keeps updates transactional and easy to lock.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    auto_generate: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    provider: Mapped[str] = mapped_column(
        String(32), default="openai_responses", server_default="openai_responses"
    )
    model_name: Mapped[str] = mapped_column(
        "model", String(128), default="gpt-5.6-terra", server_default="gpt-5.6-terra"
    )
    base_url: Mapped[str] = mapped_column(
        String(500), default="https://api.openai.com/v1", server_default="https://api.openai.com/v1"
    )
    bridge_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    prompt_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(32), default="zh-CN", server_default="zh-CN")
    tone: Mapped[str] = mapped_column(String(64), default="专业自然", server_default="专业自然")
    require_review: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    reasoning_effort: Mapped[str] = mapped_column(
        String(16), default="medium", server_default="medium"
    )
    default_skill_ids: Mapped[list[int]] = mapped_column(JSON)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=2500, server_default="2500")
    request_timeout_seconds: Mapped[int] = mapped_column(Integer, default=60, server_default="60")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AIGenerationJob(Base):
    __tablename__ = "ai_generation_jobs"
    __table_args__ = (
        Index("ix_ai_generation_jobs_due", "status", "next_attempt_at"),
        Index("ix_ai_generation_jobs_tweet_created", "source_tweet_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_tweet_id: Mapped[int] = mapped_column(
        ForeignKey("tweets.id", ondelete="CASCADE"), index=True
    )
    feature_code: Mapped[str] = mapped_column(
        String(64), default="article_generation", server_default="article_generation"
    )
    skill_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_skills.id", ondelete="SET NULL"), nullable=True, index=True
    )
    skill_ids: Mapped[list[int]] = mapped_column(JSON)
    skill_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(191), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", server_default="queued")
    provider: Mapped[str] = mapped_column(String(32))
    model_name: Mapped[str] = mapped_column("model", String(128))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    manual: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    response_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    source_tweet: Mapped[Tweet] = relationship()  # noqa: F821
    skill: Mapped[AISkill | None] = relationship()
    draft: Mapped[AIDraft | None] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )


class AIDraft(Base):
    __tablename__ = "ai_drafts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("ai_generation_jobs.id", ondelete="CASCADE"), unique=True, index=True
    )
    source_tweet_id: Mapped[int] = mapped_column(
        ForeignKey("tweets.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", server_default="draft")
    draft_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    job: Mapped[AIGenerationJob] = relationship(back_populates="draft")
