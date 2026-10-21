"""Remove account Skill bindings and implicit automatic generation settings.

Existing jobs, task selections and frozen Skill snapshots remain unchanged.
Downgrading recreates empty bindings/defaults; deleted configuration cannot be recovered.
"""

import sqlalchemy as sa

from alembic import op

revision = "0038_explicit_task_skills"
down_revision = "0037_github_skills"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("ai_user_skill_bindings")
    op.drop_column("ai_settings", "default_skill_ids")
    op.drop_column("ai_settings", "auto_trigger_mode")


def downgrade() -> None:
    # Restore the schema without enabling the removed implicit trigger mode.
    op.add_column(
        "ai_settings",
        sa.Column(
            "auto_trigger_mode", sa.String(24), nullable=False, server_default="listening_tasks"
        ),
    )
    op.add_column(
        "ai_settings",
        sa.Column("default_skill_ids", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.alter_column("ai_settings", "default_skill_ids", server_default=None)
    op.create_table(
        "ai_user_skill_bindings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("monitored_user_id", sa.Integer(), nullable=False),
        sa.Column("ai_feature_id", sa.Integer(), nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["monitored_user_id"],
            ["monitored_users.id"],
            name="fk_ai_user_skill_bindings_monitored_user_id_monitored_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ai_feature_id"],
            ["ai_features.id"],
            name="fk_ai_user_skill_bindings_ai_feature_id_ai_features",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["ai_skills.id"],
            name="fk_ai_user_skill_bindings_skill_id_ai_skills",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_user_skill_bindings"),
    )
    op.create_index(
        "ix_ai_user_skill_bindings_monitored_user_id",
        "ai_user_skill_bindings",
        ["monitored_user_id"],
    )
    op.create_index(
        "ix_ai_user_skill_bindings_ai_feature_id",
        "ai_user_skill_bindings",
        ["ai_feature_id"],
    )
    op.create_index(
        "ix_ai_user_skill_bindings_skill_id",
        "ai_user_skill_bindings",
        ["skill_id"],
    )
    op.create_index(
        "uq_ai_user_skill_binding",
        "ai_user_skill_bindings",
        ["monitored_user_id", "ai_feature_id", "skill_id"],
        unique=True,
    )
