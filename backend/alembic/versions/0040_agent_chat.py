"""Persistent article conversations and durable ai-worker turns."""

import sqlalchemy as sa

from alembic import op

revision = "0040_agent_chat"
down_revision = "0039_skill_prompt_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "admin_id", sa.Integer(), sa.ForeignKey("admins.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_conversations_admin_id", "agent_conversations", ["admin_id"])
    op.create_table(
        "agent_turns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("skill_snapshot", sa.JSON(), nullable=False),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("claim_token", sa.String(36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("article", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column(
            "article_id",
            sa.BigInteger(),
            sa.ForeignKey("ai_drafts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("conversation_id", "request_id"),
        sa.UniqueConstraint("conversation_id", "sequence"),
    )
    op.create_index("ix_agent_turns_conversation_id", "agent_turns", ["conversation_id"])
    op.create_index("ix_agent_turns_status", "agent_turns", ["status"])
    op.create_index(
        "uq_agent_turn_active",
        "agent_turns",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
        sqlite_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_table("agent_turns")
    op.drop_table("agent_conversations")
