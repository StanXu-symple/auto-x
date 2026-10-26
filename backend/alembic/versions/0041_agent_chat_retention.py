"""Index conversation activity time for bounded retention scans."""

from alembic import op

revision = "0041_agent_chat_retention"
down_revision = "0040_agent_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_agent_conversations_updated_at", "agent_conversations", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_conversations_updated_at", table_name="agent_conversations")
