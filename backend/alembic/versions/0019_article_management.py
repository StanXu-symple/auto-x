"""Unify AI drafts and user-created articles for article management."""

import sqlalchemy as sa

from alembic import op

revision = "0019_article_management"
down_revision = "0018_xhs_user_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_drafts",
        sa.Column("article_source", sa.String(length=16), server_default="ai", nullable=False),
    )
    op.create_index("ix_ai_drafts_article_source", "ai_drafts", ["article_source"])
    op.alter_column(
        "ai_drafts",
        "job_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.alter_column(
        "ai_drafts",
        "source_tweet_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM ai_drafts WHERE article_source = 'user'"))
    op.alter_column(
        "ai_drafts",
        "source_tweet_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.alter_column(
        "ai_drafts",
        "job_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.drop_index("ix_ai_drafts_article_source", table_name="ai_drafts")
    op.drop_column("ai_drafts", "article_source")
