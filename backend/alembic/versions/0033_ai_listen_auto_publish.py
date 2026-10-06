"""Add optional automatic publishing channels to AI listening tasks."""

import sqlalchemy as sa

from alembic import op

revision = "0033_ai_listen_auto_publish"
down_revision = "0032_ai_listen_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_listen_tasks", sa.Column("owner_admin_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_ai_listen_tasks_owner_admin_id_admins",
        "ai_listen_tasks",
        "admins",
        ["owner_admin_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "ai_listen_tasks",
        sa.Column(
            "auto_publish_channels",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column("ai_listen_tasks", sa.Column("qq_bot_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_ai_listen_tasks_qq_bot_id_qq_bot_accounts",
        "ai_listen_tasks",
        "qq_bot_accounts",
        ["qq_bot_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "ai_listen_tasks",
        sa.Column("qq_group_openids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("ai_listen_tasks", "qq_group_openids")
    op.drop_constraint(
        "fk_ai_listen_tasks_qq_bot_id_qq_bot_accounts", "ai_listen_tasks", type_="foreignkey"
    )
    op.drop_column("ai_listen_tasks", "qq_bot_id")
    op.drop_column("ai_listen_tasks", "auto_publish_channels")
    op.drop_constraint(
        "fk_ai_listen_tasks_owner_admin_id_admins", "ai_listen_tasks", type_="foreignkey"
    )
    op.drop_column("ai_listen_tasks", "owner_admin_id")
