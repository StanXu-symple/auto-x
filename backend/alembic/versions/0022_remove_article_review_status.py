"""Remove unused article and draft review status."""

import sqlalchemy as sa

from alembic import op

revision = "0022_remove_article_review_status"
down_revision = "0021_article_publish_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("ai_drafts", "status")
    op.drop_column("ai_settings", "require_review")


def downgrade() -> None:
    op.add_column(
        "ai_settings",
        sa.Column("require_review", sa.Boolean(), server_default="1", nullable=False),
    )
    op.add_column(
        "ai_drafts",
        sa.Column("status", sa.String(length=24), server_default="draft", nullable=False),
    )
