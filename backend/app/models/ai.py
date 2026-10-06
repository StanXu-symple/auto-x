from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow


class AISkill(Base):
    __tablename__ = "ai_skills"

    id: Mapped[int] = mapped_column(
        Integer, Identity(start=1000), primary_key=True, autoincrement=True
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    instructions: Mapped[str] = mapped_column(Text)
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    remote_skill_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    remote_skill_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AIFeature(Base):
    __tablename__ = "ai_features"

    id: Mapped[int] = mapped_column(
        Integer, Identity(start=1000), primary_key=True, autoincrement=True
    )
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_prompt: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
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
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
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
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    auto_generate: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    auto_trigger_mode: Mapped[str] = mapped_column(
        String(24), default="listening_tasks", server_default="listening_tasks"
    )
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
        UniqueConstraint("listen_task_id", "source_tweet_id", name="uq_ai_job_listen_task_tweet"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_tweet_id: Mapped[int] = mapped_column(
        ForeignKey("tweets.id", ondelete="CASCADE"), index=True
    )
    listen_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_listen_tasks.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    trigger_type: Mapped[str] = mapped_column(
        String(24), default="legacy_auto", server_default="legacy_auto", index=True
    )
    task_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    task_config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lifetime_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
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
    manual: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    response_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publish_outbox_initialized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    source_tweet: Mapped[Tweet] = relationship()  # noqa: F821
    skill: Mapped[AISkill | None] = relationship()
    draft: Mapped[AIDraft | None] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )
    listen_task: Mapped[AIListenTask | None] = relationship(back_populates="jobs")


class AIListenTask(Base):
    __tablename__ = "ai_listen_tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admins.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(100))
    desired_state: Mapped[str] = mapped_column(
        String(16), default="paused", server_default="paused", index=True
    )
    queue_hold_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    all_monitored_users: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    listen_mode: Mapped[str] = mapped_column(
        String(16), default="original", server_default="original"
    )
    auto_publish_channels: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default="[]"
    )
    qq_bot_id: Mapped[int | None] = mapped_column(
        ForeignKey("qq_bot_accounts.id", ondelete="SET NULL"), nullable=True
    )
    qq_group_openids: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    feature_code: Mapped[str] = mapped_column(
        String(64), default="article_generation", server_default="article_generation"
    )
    config_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    initial_sync_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    initial_backfill_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    initial_backfill_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_attempts_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language_override: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tone_override: Mapped[str | None] = mapped_column(String(64), nullable=True)
    max_output_tokens_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    subscriptions: Mapped[list[AIListenTaskSubscription]] = relationship(
        back_populates="task", cascade="all, delete-orphan", lazy="selectin"
    )
    skills: Mapped[list[AIListenTaskSkill]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="AIListenTaskSkill.priority",
    )
    jobs: Mapped[list[AIGenerationJob]] = relationship(back_populates="listen_task")


class AIListenTaskSubscription(Base):
    __tablename__ = "ai_listen_task_subscriptions"
    __table_args__ = (UniqueConstraint("task_id", "monitored_user_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("ai_listen_tasks.id", ondelete="CASCADE"), index=True
    )
    monitored_user_id: Mapped[int] = mapped_column(
        ForeignKey("monitored_users.id", ondelete="RESTRICT"), index=True
    )
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    task: Mapped[AIListenTask] = relationship(back_populates="subscriptions")
    monitored_user: Mapped[MonitoredUser] = relationship()  # noqa: F821


class AIListenTaskSkill(Base):
    __tablename__ = "ai_listen_task_skills"
    __table_args__ = (UniqueConstraint("task_id", "skill_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("ai_listen_tasks.id", ondelete="CASCADE"), index=True
    )
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("ai_skills.id", ondelete="RESTRICT"), index=True
    )
    priority: Mapped[int] = mapped_column(Integer)
    task: Mapped[AIListenTask] = relationship(back_populates="skills")
    skill: Mapped[AISkill] = relationship(lazy="selectin")


class AIListenTaskBackfill(Base):
    __tablename__ = "ai_listen_task_backfills"
    __table_args__ = (UniqueConstraint("task_id", "request_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("ai_listen_tasks.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[str] = mapped_column(String(120))
    from_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    to_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    cursor_posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cursor_tweet_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    scanned_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    enqueued_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    task: Mapped[AIListenTask] = relationship()


class AIListenTaskEvent(Base):
    __tablename__ = "ai_listen_task_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("ai_listen_tasks.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_generation_jobs.id", ondelete="SET NULL"), nullable=True
    )
    backfill_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_listen_task_backfills.id", ondelete="SET NULL"), nullable=True
    )
    summary: Mapped[str] = mapped_column(String(500))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AIGenerationAttempt(Base):
    __tablename__ = "ai_generation_attempts"
    __table_args__ = (UniqueConstraint("job_id", "lifetime_number"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("ai_generation_jobs.id", ondelete="CASCADE"), index=True
    )
    lifetime_number: Mapped[int] = mapped_column(Integer)
    round_number: Mapped[int] = mapped_column(Integer)
    attempt_number: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="running", server_default="running")
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    data_source_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    data_source_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AIDraft(Base):
    __tablename__ = "ai_drafts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_generation_jobs.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=True,
    )
    source_tweet_id: Mapped[int | None] = mapped_column(
        ForeignKey("tweets.id", ondelete="CASCADE"), index=True, nullable=True
    )
    article_source: Mapped[str] = mapped_column(
        String(16), default="ai", server_default="ai", index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    images: Mapped[list[str]] = mapped_column(JSON, default=list)
    publish_status: Mapped[str] = mapped_column(
        String(24), default="unpublished", server_default="unpublished", index=True
    )
    publish_channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    publish_attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    publish_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    job: Mapped[AIGenerationJob | None] = relationship(back_populates="draft")


class ArticlePublishAttempt(Base):
    __tablename__ = "article_publish_attempts"

    attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("ai_drafts.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", server_default="queued")
    target_summary: Mapped[str] = mapped_column(String(1000))
    delivery_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
