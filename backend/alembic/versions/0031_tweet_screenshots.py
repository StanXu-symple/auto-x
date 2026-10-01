"""Create the durable X post screenshot outbox."""

import sqlalchemy as sa

from alembic import op

revision = "0031_tweet_screenshots"
down_revision = "0030_qq_message_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tweet_screenshots",
        sa.Column("tweet_id", sa.String(32), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(32), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("image_path", sa.String(160), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("canonical_url", sa.String(256), nullable=True),
        sa.Column("author_username", sa.String(64), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tweet_id"], ["tweets.tweet_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_tweet_screenshots_due", "tweet_screenshots", ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_table("tweet_screenshots")
