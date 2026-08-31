"""Add pagination checkpoints and polling fencing fields.

Revision ID: 0002_polling_fencing
Revises: 0001_initial
Create Date: 2026-08-31

The migration uses operations supported by MySQL 5.7. It intentionally avoids
``ADD COLUMN IF NOT EXISTS`` because that syntax is version-dependent.
"""

import sqlalchemy as sa

from alembic import op

revision = "0002_polling_fencing"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "monitored_users",
        sa.Column("pagination_token", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "monitored_users",
        sa.Column("pagination_since_id", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "monitored_users",
        sa.Column("pagination_newest_id", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "monitored_users",
        sa.Column("manual_poll_token", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "monitored_users",
        sa.Column("poll_generation", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.create_index(
        "ix_monitored_users_manual_poll_token",
        "monitored_users",
        ["manual_poll_token"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_monitored_users_manual_poll_token", table_name="monitored_users")
    op.drop_column("monitored_users", "poll_generation")
    op.drop_column("monitored_users", "manual_poll_token")
    op.drop_column("monitored_users", "pagination_newest_id")
    op.drop_column("monitored_users", "pagination_since_id")
    op.drop_column("monitored_users", "pagination_token")
