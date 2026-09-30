"""Create reusable QQ message templates."""

import sqlalchemy as sa

from alembic import op

revision = "0030_qq_message_templates"
down_revision = "0029_tweet_type_target_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qq_message_templates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("message_template", sa.Text(), nullable=False),
        sa.Column("template_variables", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("qq_message_templates")
