"""Add per-target QQ template variable configuration."""

from alembic import op
import sqlalchemy as sa


revision = "0017_qq_template_variables"
down_revision = "0016_refresh_qq_task_schedules"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("qq_notification_targets")}
    if "template_variables" not in columns:
        op.add_column(
            "qq_notification_targets",
            sa.Column("template_variables", sa.JSON(), nullable=True),
        )
    op.execute(
        sa.text(
            "UPDATE qq_notification_targets "
            "SET template_variables = JSON_OBJECT() "
            "WHERE template_variables IS NULL"
        )
    )
    op.alter_column(
        "qq_notification_targets",
        "template_variables",
        nullable=False,
        existing_type=sa.JSON(),
    )


def downgrade():
    op.drop_column("qq_notification_targets", "template_variables")
