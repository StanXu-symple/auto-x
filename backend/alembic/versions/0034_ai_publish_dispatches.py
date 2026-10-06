"""Add durable per-channel AI draft publication dispatches."""

import sqlalchemy as sa

from alembic import op

revision = "0034_ai_publish_dispatches"
down_revision = "0033_ai_listen_auto_publish"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_generation_jobs",
        sa.Column("publish_outbox_initialized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "ai_publish_dispatches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_admin_id", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rejection_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_snapshot", sa.JSON(), nullable=False),
        sa.Column("article_publish_attempt_id", sa.String(length=36), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["ai_generation_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["draft_id"], ["ai_drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_admin_id"], ["admins.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "channel", name="uq_ai_publish_dispatch_job_channel"),
    )
    op.create_index("ix_ai_publish_dispatches_job_id", "ai_publish_dispatches", ["job_id"])
    op.create_index("ix_ai_publish_dispatches_draft_id", "ai_publish_dispatches", ["draft_id"])
    op.create_index(
        "ix_ai_publish_dispatches_owner_admin_id", "ai_publish_dispatches", ["owner_admin_id"]
    )
    op.create_index(
        "ix_ai_publish_dispatches_due", "ai_publish_dispatches", ["status", "next_attempt_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_ai_publish_dispatches_due", table_name="ai_publish_dispatches")
    op.drop_index("ix_ai_publish_dispatches_draft_id", table_name="ai_publish_dispatches")
    op.drop_index("ix_ai_publish_dispatches_owner_admin_id", table_name="ai_publish_dispatches")
    op.drop_index("ix_ai_publish_dispatches_job_id", table_name="ai_publish_dispatches")
    op.drop_table("ai_publish_dispatches")
    op.drop_column("ai_generation_jobs", "publish_outbox_initialized_at")
