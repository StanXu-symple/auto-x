"""Persist browser publish outcomes and retryable completion notifications."""

import sqlalchemy as sa

from alembic import op

revision = "0036_browser_job_results"
down_revision = "0035_publish_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_publish_dispatches", sa.Column("xhs_job_id", sa.String(64), nullable=True))
    op.create_unique_constraint(
        "uq_ai_publish_dispatches_xhs_job_id", "ai_publish_dispatches", ["xhs_job_id"]
    )
    op.create_table(
        "browser_job_results",
        sa.Column("namespace", sa.String(32), nullable=False),
        sa.Column("job_id", sa.String(64), nullable=False),
        sa.Column("admin_id", sa.Integer(), nullable=True),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("state", sa.String(24), server_default="running", nullable=False),
        sa.Column("data", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_recovery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("callback_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_callback_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("callback_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("namespace", "job_id"),
        sa.UniqueConstraint("namespace", "job_id", name="uq_browser_job_results_namespace_job"),
    )
    op.create_index("ix_browser_job_results_admin_id", "browser_job_results", ["admin_id"])
    op.create_index(
        "ix_browser_job_results_recovery", "browser_job_results",
        ["namespace", "state", "next_recovery_at"],
    )
    op.create_index(
        "ix_browser_job_results_callback", "browser_job_results",
        ["namespace", "state", "next_callback_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_browser_job_results_callback", table_name="browser_job_results")
    op.drop_index("ix_browser_job_results_recovery", table_name="browser_job_results")
    op.drop_index("ix_browser_job_results_admin_id", table_name="browser_job_results")
    op.drop_table("browser_job_results")
    op.drop_constraint(
        "uq_ai_publish_dispatches_xhs_job_id", "ai_publish_dispatches", type_="unique"
    )
    op.drop_column("ai_publish_dispatches", "xhs_job_id")
