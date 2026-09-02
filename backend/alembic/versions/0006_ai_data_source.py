"""Add the singleton encrypted AI data source.

Revision ID: 0006_ai_data_source
Revises: 0005_x_source_provider
"""

import sqlalchemy as sa

from alembic import op

revision = "0006_ai_data_source"
down_revision = "0005_x_source_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_data_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column(
            "protocol",
            sa.String(32),
            server_default="openai_responses",
            nullable=False,
        ),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("key_hint", sa.String(16), nullable=False),
        sa.Column("key_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "verification_status",
            sa.String(24),
            server_default="unverified",
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ai_data_sources"),
    )
    op.create_index(
        "ix_ai_data_sources_key_fingerprint",
        "ai_data_sources",
        ["key_fingerprint"],
    )


def downgrade() -> None:
    op.drop_table("ai_data_sources")
