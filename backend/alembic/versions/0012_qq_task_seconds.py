"""Allow seconds in QQ task schedules."""
from alembic import op
import sqlalchemy as sa

revision = "0012_qq_task_seconds"
down_revision = "0011_qq_scheduled_tasks"
branch_labels = None
depends_on = None

def upgrade():
    op.alter_column("qq_scheduled_tasks", "run_time", type_=sa.String(8))

def downgrade():
    op.alter_column("qq_scheduled_tasks", "run_time", type_=sa.String(5))
