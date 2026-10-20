"""Track disk-backed GitHub Skills alongside custom database Skills."""

import sqlalchemy as sa

from alembic import op

revision = "0037_github_skills"
down_revision = "0036_browser_job_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_skills",
        sa.Column("source_type", sa.String(16), server_default="custom", nullable=False),
    )
    op.add_column("ai_skills", sa.Column("source_key", sa.String(64), nullable=True))
    op.add_column("ai_skills", sa.Column("source_metadata", sa.JSON(), nullable=True))
    op.create_unique_constraint("uq_ai_skills_source_key", "ai_skills", ["source_key"])


def downgrade() -> None:
    op.drop_constraint("uq_ai_skills_source_key", "ai_skills", type_="unique")
    op.drop_column("ai_skills", "source_metadata")
    op.drop_column("ai_skills", "source_key")
    op.drop_column("ai_skills", "source_type")
