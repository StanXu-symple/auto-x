"""Persist initial history windows for QQ group targets."""

import sqlalchemy as sa

from alembic import op

revision = "0026_qq_target_history"
down_revision = "0025_initial_sync_days"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "qq_notification_targets", sa.Column("initial_sync_days", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("qq_notification_targets", "initial_sync_days")
