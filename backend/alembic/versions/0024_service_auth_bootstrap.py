"""Persist completion of the one-time legacy service-client import."""

import sqlalchemy as sa

from alembic import op

revision = "0024_service_auth_bootstrap"
down_revision = "0023_service_auth_store"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_auth_bootstrap_state",
        sa.Column("bootstrap_id", sa.String(length=64), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("completed_by", sa.String(length=255), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("bootstrap_id", "source_sha256"),
    )


def downgrade() -> None:
    op.drop_table("service_auth_bootstrap_state")
