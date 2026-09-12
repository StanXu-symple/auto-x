"""Add the shared service authentication authority store."""

import sqlalchemy as sa

from alembic import op

revision = "0023_service_auth_store"
down_revision = "0022_remove_review_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_auth_clients",
        sa.Column("client_id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_authenticated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("client_id"),
    )
    op.create_table(
        "service_auth_client_credentials",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_id", sa.String(length=128), nullable=False),
        sa.Column("secret_sha256", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["client_id"], ["service_auth_clients.client_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "client_id",
            "secret_sha256",
            name="uq_service_auth_credentials_client_secret",
        ),
    )
    op.create_index(
        "ix_service_auth_client_credentials_client_id",
        "service_auth_client_credentials",
        ["client_id"],
    )
    op.create_table(
        "service_auth_grants",
        sa.Column("client_id", sa.String(length=128), nullable=False),
        sa.Column("audience", sa.String(length=128), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"], ["service_auth_clients.client_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("client_id", "audience"),
    )
    op.create_table(
        "service_auth_signing_keys",
        sa.Column("kid", sa.String(length=64), nullable=False),
        sa.Column("algorithm", sa.String(length=16), server_default="RS256", nullable=False),
        sa.Column("private_key_ciphertext", sa.Text(), nullable=False),
        sa.Column("public_key_pem", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'retired', 'revoked')",
            name="ck_service_auth_signing_keys_status",
        ),
        sa.PrimaryKeyConstraint("kid"),
    )
    op.create_index(
        "ix_service_auth_signing_keys_status_expiry",
        "service_auth_signing_keys",
        ["status", "expires_at"],
    )
    # PostgreSQL guarantees a single signing authority during concurrent
    # replica startup and key rotation. Retired keys remain for JWKS overlap.
    op.create_index(
        "uq_service_auth_one_active_key",
        "service_auth_signing_keys",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "service_auth_sessions",
        sa.Column("sid", sa.String(length=36), nullable=False),
        sa.Column("admin_id", sa.Integer(), nullable=False),
        sa.Column("jti", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["admin_id"], ["admins.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("sid"),
        sa.UniqueConstraint("jti"),
    )
    op.create_index(
        "ix_service_auth_sessions_admin_id", "service_auth_sessions", ["admin_id"]
    )
    op.create_index(
        "ix_service_auth_sessions_expires_at", "service_auth_sessions", ["expires_at"]
    )
    op.create_table(
        "service_auth_revocations",
        sa.Column("jti", sa.String(length=36), nullable=False),
        sa.Column("sid", sa.String(length=36), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["sid"], ["service_auth_sessions.sid"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("jti"),
    )
    op.create_index(
        "ix_service_auth_revocations_sid", "service_auth_revocations", ["sid"]
    )
    op.create_index(
        "ix_service_auth_revocations_expires_at",
        "service_auth_revocations",
        ["expires_at"],
    )
    op.create_table(
        "service_auth_audit",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_type", sa.String(length=24), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("subject", sa.String(length=128), nullable=True),
        sa.Column("audience", sa.String(length=128), nullable=True),
        sa.Column("sid", sa.String(length=36), nullable=True),
        sa.Column("jti", sa.String(length=36), nullable=True),
        sa.Column("success", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("remote_address", sa.String(length=64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_service_auth_audit_event_type", "service_auth_audit", ["event_type"])
    op.create_index("ix_service_auth_audit_actor_id", "service_auth_audit", ["actor_id"])
    op.create_index("ix_service_auth_audit_sid", "service_auth_audit", ["sid"])
    op.create_index("ix_service_auth_audit_created_at", "service_auth_audit", ["created_at"])


def downgrade() -> None:
    op.drop_table("service_auth_audit")
    op.drop_table("service_auth_revocations")
    op.drop_table("service_auth_sessions")
    op.drop_index(
        "uq_service_auth_one_active_key", table_name="service_auth_signing_keys"
    )
    op.drop_table("service_auth_signing_keys")
    op.drop_table("service_auth_grants")
    op.drop_table("service_auth_client_credentials")
    op.drop_table("service_auth_clients")
