"""Store encrypted X credentials managed by the administration UI.

Revision ID: 0004_x_credentials
Revises: 0003_ai_creation
"""

import sqlalchemy as sa

from alembic import op

revision = "0004_x_credentials"
down_revision = "0003_ai_creation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "x_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "credential_type",
            sa.String(32),
            server_default="app_bearer",
            nullable=False,
        ),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("token_hint", sa.String(16), nullable=False),
        sa.Column("token_fingerprint", sa.String(64), nullable=False),
        sa.Column("acquisition_method", sa.String(32), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_x_credentials"),
        sa.UniqueConstraint("credential_type", name="uq_x_credentials_credential_type"),
    )
    op.create_index(
        "ix_x_credentials_token_fingerprint",
        "x_credentials",
        ["token_fingerprint"],
    )


def downgrade() -> None:
    op.drop_table("x_credentials")
