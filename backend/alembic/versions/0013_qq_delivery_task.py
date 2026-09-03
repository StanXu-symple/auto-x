"""Associate scheduled deliveries with their task."""
from alembic import op
import sqlalchemy as sa
revision="0013_qq_delivery_task"; down_revision="0012_qq_task_seconds"; branch_labels=None; depends_on=None
def upgrade():
    op.add_column("qq_deliveries", sa.Column("task_id", sa.Integer(), nullable=True)); op.create_index("ix_qq_deliveries_task_id", "qq_deliveries", ["task_id"]); op.create_foreign_key("fk_qq_deliveries_task_id", "qq_deliveries", "qq_scheduled_tasks", ["task_id"], ["id"], ondelete="SET NULL")
def downgrade():
    op.drop_constraint("fk_qq_deliveries_task_id", "qq_deliveries", type_="foreignkey"); op.drop_index("ix_qq_deliveries_task_id", table_name="qq_deliveries"); op.drop_column("qq_deliveries", "task_id")
