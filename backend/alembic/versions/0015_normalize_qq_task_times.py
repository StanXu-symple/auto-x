"""Normalize legacy QQ task times to include seconds."""

from alembic import op
import sqlalchemy as sa


revision = "0015_normalize_qq_task_times"
down_revision = "0014_qq_task_interval"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        sa.text(
            "UPDATE qq_scheduled_tasks "
            "SET run_time = CONCAT(run_time, :seconds) "
            "WHERE run_time REGEXP '^[0-9]{2}:[0-9]{2}$'"
        ).bindparams(seconds=":00")
    )


def downgrade():
    pass
