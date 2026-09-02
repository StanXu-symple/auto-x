"""Add Xiaohongshu MCP configuration and publishing queue.

Revision ID: 0007_xiaohongshu_publishing
Revises: 0006_ai_data_source
"""

import sqlalchemy as sa

from alembic import op

revision = "0007_xiaohongshu_publishing"
down_revision = "0006_ai_data_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "xiaohongshu_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("connector", sa.String(32), server_default="xiaohongshu_mcp", nullable=False),
        sa.Column("mcp_url", sa.String(500), nullable=False),
        sa.Column("encrypted_auth_token", sa.Text(), nullable=True),
        sa.Column("token_hint", sa.String(16), nullable=True),
        sa.Column("token_fingerprint", sa.String(64), nullable=True),
        sa.Column("verification_status", sa.String(24), server_default="unverified", nullable=False),
        sa.Column("login_status", sa.String(24), server_default="unknown", nullable=False),
        sa.Column("risk_acknowledged", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_xiaohongshu_connections"),
    )
    op.create_table(
        "xiaohongshu_publish_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("default_strategy", sa.String(24), server_default="manual", nullable=False),
        sa.Column("default_delay_minutes", sa.Integer(), server_default="60", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("daily_publish_limit", sa.Integer(), server_default="10", nullable=False),
        sa.Column("default_visibility", sa.String(32), server_default="公开可见", nullable=False),
        sa.Column("declare_original", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_xiaohongshu_publish_settings"),
    )
    op.create_table(
        "xiaohongshu_publish_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_ai_draft_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("images", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("products", sa.JSON(), nullable=False),
        sa.Column("visibility", sa.String(32), nullable=False),
        sa.Column("is_original", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("strategy", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), server_default="draft", nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.String(36), nullable=True),
        sa.Column("claimed_by", sa.String(128), nullable=True),
        sa.Column("platform_note_id", sa.String(128), nullable=True),
        sa.Column("platform_url", sa.String(500), nullable=True),
        sa.Column("response_snapshot", sa.JSON(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_ai_draft_id"], ["ai_drafts.id"],
            name="fk_xiaohongshu_publish_jobs_source_ai_draft_id_ai_drafts",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_xiaohongshu_publish_jobs"),
    )
    op.create_index("ix_xiaohongshu_publish_jobs_source_ai_draft_id", "xiaohongshu_publish_jobs", ["source_ai_draft_id"])
    op.create_index("ix_xhs_publish_jobs_due", "xiaohongshu_publish_jobs", ["status", "scheduled_at"])
    op.create_index("ix_xhs_publish_jobs_created", "xiaohongshu_publish_jobs", ["created_at"])


def downgrade() -> None:
    op.drop_table("xiaohongshu_publish_jobs")
    op.drop_table("xiaohongshu_publish_settings")
    op.drop_table("xiaohongshu_connections")
