"""Durable prompt generation requests handled by ai-worker."""

import sqlalchemy as sa
from alembic import op

revision = "0043_prompt_generation"
down_revision = "0042_agent_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prompt_generations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "admin_id", sa.Integer(), sa.ForeignKey("admins.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("requirement", sa.Text(), nullable=False),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.String(500)),
        sa.Column("claim_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("admin_id", "request_id", name="uq_prompt_generation_request"),
    )
    op.create_index("ix_prompt_generations_due", "prompt_generations", ["status", "created_at"])


def downgrade() -> None:
    op.drop_table("prompt_generations")
