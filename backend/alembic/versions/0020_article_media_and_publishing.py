"""Add article media and channel publishing state."""

import sqlalchemy as sa

from alembic import op

revision = "0020_article_media_publishing"
down_revision = "0019_article_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_drafts", sa.Column("images", sa.JSON(), nullable=True))
    op.execute(sa.text("UPDATE ai_drafts SET images = '[]' WHERE images IS NULL"))
    op.alter_column("ai_drafts", "images", existing_type=sa.JSON(), nullable=False)
    op.add_column(
        "ai_drafts",
        sa.Column(
            "publish_status",
            sa.String(length=24),
            server_default="unpublished",
            nullable=False,
        ),
    )
    op.add_column("ai_drafts", sa.Column("publish_channel", sa.String(length=16)))
    op.add_column("ai_drafts", sa.Column("publish_attempt_id", sa.String(length=36)))
    op.add_column("ai_drafts", sa.Column("publish_error", sa.Text()))
    op.add_column("ai_drafts", sa.Column("published_at", sa.DateTime(timezone=True)))
    op.create_index("ix_ai_drafts_publish_status", "ai_drafts", ["publish_status"])
    op.create_index("ix_ai_drafts_publish_attempt_id", "ai_drafts", ["publish_attempt_id"])

    op.add_column(
        "qq_deliveries",
        sa.Column("article_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "qq_deliveries",
        sa.Column("article_publish_attempt_id", sa.String(length=36), nullable=True),
    )
    op.add_column("qq_deliveries", sa.Column("sequence", sa.Integer(), nullable=True))
    op.add_column("qq_deliveries", sa.Column("media_path", sa.String(length=1000), nullable=True))
    op.create_foreign_key(
        "fk_qq_deliveries_article_id_ai_drafts",
        "qq_deliveries",
        "ai_drafts",
        ["article_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_qq_deliveries_article_id", "qq_deliveries", ["article_id"])
    op.create_index(
        "ix_qq_deliveries_article_publish_attempt_id",
        "qq_deliveries",
        ["article_publish_attempt_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_qq_deliveries_article_publish_attempt_id", table_name="qq_deliveries")
    op.drop_index("ix_qq_deliveries_article_id", table_name="qq_deliveries")
    op.drop_constraint("fk_qq_deliveries_article_id_ai_drafts", "qq_deliveries", type_="foreignkey")
    op.drop_column("qq_deliveries", "media_path")
    op.drop_column("qq_deliveries", "sequence")
    op.drop_column("qq_deliveries", "article_publish_attempt_id")
    op.drop_column("qq_deliveries", "article_id")
    op.drop_index("ix_ai_drafts_publish_attempt_id", table_name="ai_drafts")
    op.drop_index("ix_ai_drafts_publish_status", table_name="ai_drafts")
    op.drop_column("ai_drafts", "published_at")
    op.drop_column("ai_drafts", "publish_error")
    op.drop_column("ai_drafts", "publish_attempt_id")
    op.drop_column("ai_drafts", "publish_channel")
    op.drop_column("ai_drafts", "publish_status")
    op.drop_column("ai_drafts", "images")
