"""Refresh existing QQ task schedules after schedule semantics changed."""

from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op
from app.core.config import get_settings
from app.services.qq_schedule import next_qq_task_run

revision = "0016_refresh_qq_task_schedules"
down_revision = "0015_normalize_qq_task_times"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, frequency, interval_value, run_time, weekdays, month_day "
            "FROM qq_scheduled_tasks WHERE is_enabled = :enabled"
        ),
        {"enabled": True},
    ).mappings()
    now = datetime.now(UTC)
    timezone_name = get_settings().app_timezone
    updates = [
        {
            "task_id": row["id"],
            "next_run_at": next_qq_task_run(
                frequency=row["frequency"],
                interval_value=row["interval_value"],
                run_time=row["run_time"],
                weekdays=row["weekdays"],
                month_day=row["month_day"],
                now=now,
                timezone_name=timezone_name,
            ),
        }
        for row in rows
    ]
    if updates:
        connection.execute(
            sa.text(
                "UPDATE qq_scheduled_tasks "
                "SET next_run_at = :next_run_at WHERE id = :task_id"
            ),
            updates,
        )


def downgrade():
    pass
