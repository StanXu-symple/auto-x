"""Record manual verification of uncertain Xiaohongshu publication results."""

import sqlalchemy as sa

from alembic import op

revision = "0035_publish_reconciliation"
down_revision = "0034_ai_publish_dispatches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("ai_publish_dispatches", "article_publish_attempts"):
        op.add_column(table, sa.Column("reconciliation_result", sa.String(24), nullable=True))
        op.add_column(table, sa.Column("reconciliation_note", sa.Text(), nullable=True))
        op.add_column(table, sa.Column("published_url", sa.String(2048), nullable=True))
        op.add_column(table, sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("reconciled_by_admin_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    for table in ("article_publish_attempts", "ai_publish_dispatches"):
        for column in (
            "reconciled_by_admin_id", "reconciled_at", "published_url",
            "reconciliation_note", "reconciliation_result",
        ):
            op.drop_column(table, column)
