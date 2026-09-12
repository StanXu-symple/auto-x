from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class ServiceAuthClient(Base):
    """A service identity; secret material is kept in the credential table."""

    __tablename__ = "service_auth_clients"

    client_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    last_authenticated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ServiceAuthClientCredential(Base):
    """One of potentially many node-local credentials for a service identity.

    Service secrets are generated with 256 bits of entropy.  A SHA-256 digest
    is therefore sufficient for verification and means the original secret is
    never stored in PostgreSQL.
    """

    __tablename__ = "service_auth_client_credentials"
    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "secret_sha256",
            name="uq_service_auth_credentials_client_secret",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("service_auth_clients.client_id", ondelete="CASCADE"),
        index=True,
    )
    secret_sha256: Mapped[str] = mapped_column(String(64))
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ServiceAuthGrant(Base):
    __tablename__ = "service_auth_grants"

    client_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("service_auth_clients.client_id", ondelete="CASCADE"),
        primary_key=True,
    )
    audience: Mapped[str] = mapped_column(String(128), primary_key=True)
    scopes: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ServiceAuthBootstrapState(Base):
    """Cluster-wide record of a completed one-time legacy import."""

    __tablename__ = "service_auth_bootstrap_state"

    bootstrap_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    completed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ServiceAuthSigningKey(Base):
    """Encrypted signing material shared by every auth-center replica."""

    __tablename__ = "service_auth_signing_keys"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'retired', 'revoked')",
            name="ck_service_auth_signing_keys_status",
        ),
        Index("ix_service_auth_signing_keys_status_expiry", "status", "expires_at"),
        Index(
            "uq_service_auth_one_active_key",
            "status",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    kid: Mapped[str] = mapped_column(String(64), primary_key=True)
    algorithm: Mapped[str] = mapped_column(String(16), default="RS256", server_default="RS256")
    private_key_ciphertext: Mapped[str] = mapped_column(Text)
    public_key_pem: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ServiceAuthSession(Base):
    """Durable administrator login session used by all backend replicas."""

    __tablename__ = "service_auth_sessions"

    sid: Mapped[str] = mapped_column(String(36), primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"), index=True)
    # The unique constraint already owns a PostgreSQL index; declaring a
    # second ORM index here makes Alembic see a schema drift after migration.
    jti: Mapped[str] = mapped_column(String(36), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ServiceAuthRevocation(Base):
    """Explicit JWT deny-list retained until the JWT can no longer validate."""

    __tablename__ = "service_auth_revocations"

    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    sid: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("service_auth_sessions.sid", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reason: Mapped[str] = mapped_column(String(64))


class ServiceAuthAudit(Base):
    """Security audit stream; it contains identifiers but never raw secrets."""

    __tablename__ = "service_auth_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    actor_type: Mapped[str] = mapped_column(String(24))
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    subject: Mapped[str | None] = mapped_column(String(128), nullable=True)
    audience: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sid: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    jti: Mapped[str | None] = mapped_column(String(36), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, server_default=true())
    remote_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
