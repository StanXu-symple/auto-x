"""Add the initial history window for monitored accounts."""

import sqlalchemy as sa

from alembic import op

revision = "0025_initial_sync_days"
down_revision = "0024_service_auth_bootstrap"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing accounts retain their current synchronization checkpoints.
    op.add_column("monitored_users", sa.Column("initial_sync_days", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("monitored_users", "initial_sync_days")
