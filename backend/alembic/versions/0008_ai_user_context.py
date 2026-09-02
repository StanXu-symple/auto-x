"""Add AI features, per-user skill bindings and evolving author profiles.

Revision ID: 0008_ai_user_context
Revises: 0007_xiaohongshu_publishing
"""

import sqlalchemy as sa

from alembic import op

revision = "0008_ai_user_context"
down_revision = "0007_xiaohongshu_publishing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MySQL applies DDL non-transactionally. These guards let an interrupted
    # migration safely resume without dropping any table created before failure.
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "ai_features" not in existing_tables:
        op.create_table(
            "ai_features",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("code", sa.String(64), nullable=False),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("base_prompt", sa.Text(), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id", name="pk_ai_features"),
        )
        op.create_index("ix_ai_features_code", "ai_features", ["code"], unique=True)
    if "ai_user_profiles" not in existing_tables:
        op.create_table(
            "ai_user_profiles",
            sa.Column("monitored_user_id", sa.Integer(), nullable=False),
            sa.Column("identity_summary", sa.Text(), nullable=False),
            sa.Column("focus_summary", sa.Text(), nullable=False),
            sa.Column("relationship_summary", sa.Text(), nullable=False),
            sa.Column("recurring_topics", sa.JSON(), nullable=False),
            sa.Column("evidence", sa.JSON(), nullable=False),
            sa.Column("confidence", sa.Float(), server_default="0", nullable=False),
            sa.Column("version", sa.Integer(), server_default="1", nullable=False),
            sa.Column("last_source_tweet_id", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["monitored_user_id"],
                ["monitored_users.id"],
                name="fk_ai_user_profiles_monitored_user_id_monitored_users",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["last_source_tweet_id"],
                ["tweets.id"],
                name="fk_ai_user_profiles_last_source_tweet_id_tweets",
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("monitored_user_id", name="pk_ai_user_profiles"),
        )
    if "ai_user_skill_bindings" not in existing_tables:
        op.create_table(
            "ai_user_skill_bindings",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("monitored_user_id", sa.Integer(), nullable=False),
            sa.Column("ai_feature_id", sa.Integer(), nullable=False),
            sa.Column("skill_id", sa.Integer(), nullable=False),
            sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
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
    job_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("ai_generation_jobs")
    }
    if "feature_code" not in job_columns:
        op.add_column(
            "ai_generation_jobs",
            sa.Column(
                "feature_code",
                sa.String(64),
                server_default="article_generation",
                nullable=False,
            ),
        )


def downgrade() -> None:
    op.drop_column("ai_generation_jobs", "feature_code")
    op.drop_table("ai_user_skill_bindings")
    op.drop_table("ai_user_profiles")
    op.drop_table("ai_features")
