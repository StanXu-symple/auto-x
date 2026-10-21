"""Classify existing custom instructions as prompts without changing associations."""

import sqlalchemy as sa

from alembic import op

revision = "0039_skill_prompt_types"
down_revision = "0038_explicit_task_skills"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_skills",
        sa.Column("content_type", sa.String(16), server_default="prompt", nullable=False),
    )
    op.execute("UPDATE ai_skills SET content_type = 'skill' WHERE source_type = 'github'")
    op.create_index("ix_ai_skills_content_type", "ai_skills", ["content_type"])


def downgrade() -> None:
    op.drop_index("ix_ai_skills_content_type", table_name="ai_skills")
    op.drop_column("ai_skills", "content_type")
