"""Persist QQ group membership observed from authenticated events.

Revision ID: 0010_qq_joined_groups
Revises: 0009_qq_notifications
"""

import sqlalchemy as sa

from alembic import op

revision = "0010_qq_joined_groups"
down_revision = "0009_qq_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qq_joined_groups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bot_id", sa.Integer(), nullable=False),
        sa.Column("app_id", sa.String(64), nullable=False),
        sa.Column("group_openid", sa.String(128), nullable=False),
        sa.Column("is_joined", sa.Boolean(), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["bot_id"], ["qq_bot_accounts.id"],
            name="fk_qq_joined_groups_bot_id_qq_bot_accounts", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_qq_joined_groups"),
    )
    op.create_index("ix_qq_joined_groups_bot_id", "qq_joined_groups", ["bot_id"])
    op.create_index(
        "uq_qq_joined_group", "qq_joined_groups",
        ["bot_id", "app_id", "group_openid"], unique=True,
    )


def downgrade() -> None:
    op.drop_table("qq_joined_groups")
