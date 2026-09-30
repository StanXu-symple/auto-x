"""Create database-backed QQ placeholder mappings and seed built-ins."""

import sqlalchemy as sa

from alembic import op

revision = "0027_qq_placeholders"
down_revision = "0026_qq_target_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = op.create_table(
        "qq_placeholders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("placeholder", sa.String(66), nullable=False, unique=True),
        sa.Column("source_field", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.bulk_insert(
        table,
        [
            {"placeholder": "{author}", "source_field": "display_name"},
            {"placeholder": "{username}", "source_field": "username"},
            {"placeholder": "{text}", "source_field": "text"},
            {"placeholder": "{url}", "source_field": "url"},
            {"placeholder": "{posted_at}", "source_field": "posted_at"},
            {"placeholder": "{title}", "source_field": "title"},
        ],
    )


def downgrade() -> None:
    op.drop_table("qq_placeholders")
