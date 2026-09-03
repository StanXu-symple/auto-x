"""Add QQ bot accounts, notification targets, subscriptions, and delivery outbox.

Revision ID: 0009_qq_notifications
Revises: 0008_ai_user_context
"""

from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "0009_qq_notifications"
down_revision = "0008_ai_user_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    now = datetime.now(UTC)
    op.create_table(
        "qq_bot_accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("app_id", sa.String(64), nullable=False),
        sa.Column("encrypted_app_secret", sa.Text(), nullable=False),
        sa.Column("secret_hint", sa.String(16), nullable=False),
        sa.Column("secret_fingerprint", sa.String(64), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "verification_status", sa.String(24), server_default="unverified", nullable=False
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), default=now, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_qq_bot_accounts"),
    )
    op.create_index("ix_qq_bot_accounts_app_id", "qq_bot_accounts", ["app_id"], unique=True)
    op.create_index(
        "ix_qq_bot_accounts_secret_fingerprint",
        "qq_bot_accounts",
        ["secret_fingerprint"],
        unique=False,
    )
    op.create_table(
        "qq_notification_targets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bot_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("group_openid", sa.String(128), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("all_monitored_users", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("message_template", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), default=now, nullable=False),
        sa.ForeignKeyConstraint(
            ["bot_id"], ["qq_bot_accounts.id"], name="fk_qq_targets_bot_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_qq_notification_targets"),
    )
    op.create_index(
        "ix_qq_notification_targets_bot_id", "qq_notification_targets", ["bot_id"], unique=False
    )
    op.create_index(
        "uq_qq_notification_target",
        "qq_notification_targets",
        ["bot_id", "group_openid"],
        unique=True,
    )
    op.create_table(
        "qq_target_subscriptions",
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("monitored_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), default=now, nullable=False),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["qq_notification_targets.id"],
            name="fk_qq_subscriptions_target_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["monitored_user_id"],
            ["monitored_users.id"],
            name="fk_qq_subscriptions_monitored_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "target_id", "monitored_user_id", name="pk_qq_target_subscriptions"
        ),
    )
    op.create_index(
        "ix_qq_target_subscriptions_monitored_user_id",
        "qq_target_subscriptions",
        ["monitored_user_id"],
        unique=False,
    )
    op.create_table(
        "qq_deliveries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("source_tweet_id", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.String(24), server_default="tweet", nullable=False),
        sa.Column("idempotency_key", sa.String(191), nullable=False),
        sa.Column("bot_name", sa.String(100), nullable=False),
        sa.Column("bot_app_id", sa.String(64), nullable=False),
        sa.Column("bot_version", sa.Integer(), nullable=False),
        sa.Column("target_name", sa.String(100), nullable=False),
        sa.Column("group_openid", sa.String(128), nullable=False),
        sa.Column("message_body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), default=now, nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.String(36), nullable=True),
        sa.Column("claimed_by", sa.String(128), nullable=True),
        sa.Column("provider_message_id", sa.String(128), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), default=now, nullable=False),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["qq_notification_targets.id"],
            name="fk_qq_deliveries_target_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_tweet_id"],
            ["tweets.id"],
            name="fk_qq_deliveries_source_tweet_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_qq_deliveries"),
        sa.UniqueConstraint("idempotency_key", name="uq_qq_deliveries_idempotency_key"),
    )
    op.create_index("ix_qq_deliveries_target_id", "qq_deliveries", ["target_id"], unique=False)
    op.create_index(
        "ix_qq_deliveries_source_tweet_id", "qq_deliveries", ["source_tweet_id"], unique=False
    )
    op.create_index(
        "ix_qq_deliveries_due", "qq_deliveries", ["status", "next_attempt_at"], unique=False
    )
    op.create_index(
        "ix_qq_deliveries_target_created",
        "qq_deliveries",
        ["target_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("qq_deliveries")
    op.drop_table("qq_target_subscriptions")
    op.drop_table("qq_notification_targets")
    op.drop_table("qq_bot_accounts")
