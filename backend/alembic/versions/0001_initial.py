"""Create the X Sentinel schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-31
"""

import sqlalchemy as sa

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admins",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_admins"),
        sa.UniqueConstraint("username", name="uq_admins_username"),
    )
    op.create_index("ix_admins_username", "admins", ["username"])

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_app_settings"),
    )

    op.create_table(
        "monitored_users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("x_user_id", sa.String(32), nullable=True),
        sa.Column("display_name", sa.String(128), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("include_replies", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("include_retweets", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(24), server_default="idle", nullable=False),
        sa.Column("last_tweet_id", sa.String(32), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_monitored_users"),
        sa.UniqueConstraint("username", name="uq_monitored_users_username"),
        sa.UniqueConstraint("x_user_id", name="uq_monitored_users_x_user_id"),
    )
    op.create_index("ix_monitored_users_username", "monitored_users", ["username"])
    op.create_index("ix_monitored_users_x_user_id", "monitored_users", ["x_user_id"])
    op.create_index("ix_monitored_users_next_poll_at", "monitored_users", ["next_poll_at"])
    op.create_index("ix_monitored_users_due", "monitored_users", ["is_active", "next_poll_at"])

    op.create_table(
        "polling_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("monitored_user_id", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("tweets_fetched", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tweets_inserted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("rate_limit_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["monitored_user_id"],
            ["monitored_users.id"],
            name="fk_polling_logs_monitored_user_id_monitored_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_polling_logs"),
    )
    op.create_index("ix_polling_logs_monitored_user_id", "polling_logs", ["monitored_user_id"])
    op.create_index("ix_polling_logs_status", "polling_logs", ["status"])
    op.create_index("ix_polling_logs_started_status", "polling_logs", ["started_at", "status"])

    op.create_table(
        "tweets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tweet_id", sa.String(32), nullable=False),
        sa.Column("monitored_user_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.String(32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("lang", sa.String(16), nullable=True),
        sa.Column("conversation_id", sa.String(32), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("like_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("retweet_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reply_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("quote_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("bookmark_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("impression_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("entities", sa.JSON(), nullable=True),
        sa.Column("attachments", sa.JSON(), nullable=True),
        sa.Column("referenced_tweets", sa.JSON(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["monitored_user_id"],
            ["monitored_users.id"],
            name="fk_tweets_monitored_user_id_monitored_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tweets"),
        sa.UniqueConstraint("tweet_id", name="uq_tweets_tweet_id"),
    )
    op.create_index("ix_tweets_tweet_id", "tweets", ["tweet_id"])
    op.create_index("ix_tweets_monitored_user_id", "tweets", ["monitored_user_id"])
    op.create_index("ix_tweets_author_id", "tweets", ["author_id"])
    op.create_index("ix_tweets_posted_at", "tweets", ["posted_at"])
    op.create_index("ix_tweets_user_posted", "tweets", ["monitored_user_id", "posted_at"])


def downgrade() -> None:
    op.drop_table("tweets")
    op.drop_table("polling_logs")
    op.drop_table("monitored_users")
    op.drop_table("app_settings")
    op.drop_table("admins")
