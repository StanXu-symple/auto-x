"""Add QQ scheduled tasks and many-to-many destinations."""
from alembic import op
import sqlalchemy as sa

revision = "0011_qq_scheduled_tasks"
down_revision = "0010_qq_joined_groups"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("qq_scheduled_tasks", sa.Column("id", sa.Integer, primary_key=True, autoincrement=True), sa.Column("name", sa.String(100), nullable=False), sa.Column("message", sa.Text, nullable=False), sa.Column("frequency", sa.String(16), nullable=False), sa.Column("run_time", sa.String(5), nullable=False), sa.Column("weekdays", sa.String(32), nullable=False, server_default=""), sa.Column("month_day", sa.Integer), sa.Column("is_enabled", sa.Boolean, nullable=False, server_default="1"), sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False), sa.Column("last_run_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("qq_scheduled_task_bots", sa.Column("task_id", sa.Integer, sa.ForeignKey("qq_scheduled_tasks.id", ondelete="CASCADE"), primary_key=True), sa.Column("bot_id", sa.Integer, sa.ForeignKey("qq_bot_accounts.id", ondelete="CASCADE"), primary_key=True))
    op.create_table("qq_scheduled_task_groups", sa.Column("task_id", sa.Integer, sa.ForeignKey("qq_scheduled_tasks.id", ondelete="CASCADE"), primary_key=True), sa.Column("bot_id", sa.Integer, sa.ForeignKey("qq_bot_accounts.id", ondelete="CASCADE"), primary_key=True), sa.Column("group_openid", sa.String(128), primary_key=True))

def downgrade():
    op.drop_table("qq_scheduled_task_groups")
    op.drop_table("qq_scheduled_task_bots")
    op.drop_table("qq_scheduled_tasks")
