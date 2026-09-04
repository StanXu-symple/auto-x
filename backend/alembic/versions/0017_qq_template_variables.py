"""Add per-target QQ template variable configuration."""

from alembic import op
import sqlalchemy as sa


revision = "0017_qq_template_variables"
down_revision = "0016_refresh_qq_task_schedules"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "qq_notification_targets",
        sa.Column("template_variables", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade():
    op.drop_column("qq_notification_targets", "template_variables")
