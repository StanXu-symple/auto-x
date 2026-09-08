"""Remove unused article and draft review status."""

import sqlalchemy as sa

from alembic import op

revision = "0022_remove_review_status"
down_revision = "0021_article_publish_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    draft_columns = {column["name"] for column in inspector.get_columns("ai_drafts")}
    if "status" in draft_columns:
        op.drop_column("ai_drafts", "status")
    setting_columns = {column["name"] for column in inspector.get_columns("ai_settings")}
    if "require_review" in setting_columns:
        op.drop_column("ai_settings", "require_review")


def downgrade() -> None:
    op.add_column(
        "ai_settings",
        sa.Column("require_review", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "ai_drafts",
        sa.Column("status", sa.String(length=24), server_default="draft", nullable=False),
    )
