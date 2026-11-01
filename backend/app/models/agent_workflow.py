"""Durable, serial executions. Definition edits never mutate a queued run."""

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class AgentWorkflowRun(Base):
    __tablename__ = "agent_workflow_runs"
    __table_args__ = (
        UniqueConstraint("admin_id", "request_id", name="uq_agent_workflow_run_request"),
        Index("ix_agent_workflow_runs_due", "status", "next_attempt_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    admin_id: Mapped[int] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    request_hash: Mapped[str] = mapped_column(String(64))
    workflow_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_agent_workflows.id", ondelete="SET NULL"), nullable=True, index=True
    )
    workflow_name: Mapped[str] = mapped_column(String(100))
    workflow_version: Mapped[int] = mapped_column(Integer)
    input_text: Mapped[str] = mapped_column(Text)
    source_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    workflow_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    current_position: Mapped[int] = mapped_column(Integer, default=0)
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_drafts.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentWorkflowStepRun(Base):
    __tablename__ = "agent_workflow_step_runs"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_workflow_runs.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_name: Mapped[str] = mapped_column(String(100))
    node_type: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
