from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import and_, delete, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.admin import Admin
from app.models.service_auth import (
    ServiceAuthAudit,
    ServiceAuthBootstrapState,
    ServiceAuthClient,
    ServiceAuthClientCredential,
    ServiceAuthGrant,
    ServiceAuthRevocation,
    ServiceAuthSession,
    ServiceAuthSigningKey,
)

ISSUER = "xsentinel-auth"
USER_AUDIENCE = "xsentinel-backend"
ALGORITHM = "RS256"
SESSION_CACHE_PREFIX = "xsentinel:auth:session:"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_KEY_LOCK = 0x5853454E54494E45  # "XSENTINE", stable across processes.
_CLIENT_BOOTSTRAP_LOCK = 0x5853454E434C4945  # "XSENCLIE", stable across processes.
LEGACY_CLIENTS_BOOTSTRAP_ID = "legacy-clients-v1"
LEGACY_PRIVATE_KEY_RETIRED_MARKER = "MIGRATED TO POSTGRESQL; PRIVATE KEY REMOVED\n"
_PLACEHOLDER_PREFIXES = ("development-only-", "change-me-", "replace-with-")

logger = logging.getLogger(__name__)


class KeyEncryptionKeyMismatchError(RuntimeError):
    """The configured KEK cannot decrypt the cluster's signing material."""


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _key_encryption_key(master_secret: str) -> bytes:
    if len(master_secret.encode()) < 32:
        raise RuntimeError("SERVICE_AUTH_KEY_ENCRYPTION_KEY must contain at least 32 bytes")
    return hashlib.sha256(master_secret.encode()).digest()


def resolve_key_encryption_secret(
    environment: str,
    configured_secret: str | None,
    *,
    legacy_x_token_secret: str = "",
    legacy_jwt_secret: str = "",
) -> str:
    """Resolve the auth KEK while keeping production secrets independent.

    Older development and test setups may temporarily reuse an application
    secret. Staging and production must explicitly provision the dedicated
    variable and must not reuse either legacy secret.
    """

    value = (configured_secret or "").strip()
    strict = environment.strip().lower() not in {"development", "test"}
    if strict:
        if not value:
            raise RuntimeError(
                "SERVICE_AUTH_KEY_ENCRYPTION_KEY is required in production and must "
                "be identical on every auth-center replica"
            )
        if value.lower().startswith(_PLACEHOLDER_PREFIXES):
            raise RuntimeError(
                "SERVICE_AUTH_KEY_ENCRYPTION_KEY must not contain a placeholder value"
            )
        for legacy_name, legacy_value in (
            ("X_TOKEN_ENCRYPTION_KEY", legacy_x_token_secret),
            ("JWT_SECRET_KEY", legacy_jwt_secret),
        ):
            candidate = legacy_value.strip()
            if candidate and hmac.compare_digest(value, candidate):
                raise RuntimeError(
                    f"SERVICE_AUTH_KEY_ENCRYPTION_KEY must be independent from {legacy_name}"
                )
    elif not value:
        value = legacy_x_token_secret.strip() or legacy_jwt_secret.strip()
        logger.warning(
            "SERVICE_AUTH_KEY_ENCRYPTION_KEY is unset; %s compatibility fallback "
            "is enabled only because ENVIRONMENT=%s",
            "X_TOKEN_ENCRYPTION_KEY" if legacy_x_token_secret.strip() else "JWT_SECRET_KEY",
            environment,
        )
    if not value:
        raise RuntimeError("SERVICE_AUTH_KEY_ENCRYPTION_KEY is empty")
    _key_encryption_key(value)
    return value


def public_key_kid(public_key_pem: str) -> str:
    public_key = serialization.load_pem_public_key(public_key_pem.encode())
    der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return _b64url(hashlib.sha256(der).digest())[:32]


def public_key_jwk(public_key_pem: str, kid: str) -> dict[str, str]:
    public_key = serialization.load_pem_public_key(public_key_pem.encode())
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise ValueError("Only RSA service-auth signing keys are supported")
    numbers = public_key.public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": ALGORITHM,
        "kid": kid,
        "n": _b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }


def encrypt_private_key(private_key_pem: str, kid: str, master_secret: str) -> str:
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key_encryption_key(master_secret)).encrypt(
        nonce,
        private_key_pem.encode(),
        f"xsentinel-signing-key:{kid}".encode(),
    )
    return f"v1.{_b64url(nonce + ciphertext)}"


def decrypt_private_key(ciphertext: str, kid: str, master_secret: str) -> str:
    version, separator, encoded = ciphertext.partition(".")
    if version != "v1" or not separator:
        raise ValueError("Unsupported signing-key envelope")
    padded = encoded + "=" * (-len(encoded) % 4)
    payload = base64.urlsafe_b64decode(padded)
    if len(payload) < 29:
        raise ValueError("Invalid signing-key envelope")
    try:
        plaintext = AESGCM(_key_encryption_key(master_secret)).decrypt(
            payload[:12],
            payload[12:],
            f"xsentinel-signing-key:{kid}".encode(),
        )
    except InvalidTag as exc:
        raise KeyEncryptionKeyMismatchError(
            "Unable to decrypt the shared service-auth signing key "
            f"{kid!r}; verify that SERVICE_AUTH_KEY_ENCRYPTION_KEY is identical "
            "on every auth-center replica and matches the value used to create the key"
        ) from exc
    return plaintext.decode()


def generate_signing_key() -> tuple[str, str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return public_key_kid(public_pem), private_pem, public_pem


def load_signing_key(path: str) -> tuple[str, str, str]:
    private_pem = Path(path).read_text().strip() + "\n"
    private_key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise ValueError("Only RSA private keys can be imported")
    public_pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return public_key_kid(public_pem), private_pem, public_pem


@dataclass(frozen=True)
class SigningMaterial:
    kid: str
    private_key: str
    public_key: str
    algorithm: str = ALGORITHM


class AuthStore:
    """PostgreSQL-backed authentication authority shared by all replicas."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        master_secret: str,
        legacy_private_key_file: str = "",
        legacy_clients_file: str = "",
        rotation_days: int = 30,
        overlap_days: int = 7,
        minimum_overlap: timedelta | None = None,
    ) -> None:
        # Validate at construction so a replica with the wrong provisioning
        # never reports ready or issues tokens it cannot later decrypt.
        _key_encryption_key(master_secret)
        self.session_factory = session_factory
        self.master_secret = master_secret
        self.legacy_private_key_file = legacy_private_key_file
        self.legacy_clients_file = legacy_clients_file
        self.rotation_after = timedelta(days=max(1, rotation_days))
        configured_overlap = timedelta(days=max(1, overlap_days))
        self.overlap = max(configured_overlap, minimum_overlap or timedelta())

    async def initialize(self) -> None:
        signing = await self.ensure_active_signing_key()
        # Validate the cluster KEK before accepting any node-local bootstrap
        # credentials into the shared authority. A misconfigured replica must
        # fail without mutating PostgreSQL.
        await self._bootstrap_legacy_clients()
        self._retire_legacy_private_key(signing.kid)

    async def _advisory_lock(self, session: AsyncSession, lock_id: int) -> None:
        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": lock_id},
            )

    async def _bootstrap_legacy_clients(self) -> None:
        async with self.session_factory() as session, session.begin():
            # A row lock cannot protect a missing marker. The transaction-level
            # advisory lock serializes the first import across PostgreSQL-backed
            # replicas; the marker then makes every later startup a no-op.
            await self._advisory_lock(session, _CLIENT_BOOTSTRAP_LOCK)
            path = Path(self.legacy_clients_file) if self.legacy_clients_file else None
            if path is None or not path.is_file():
                return
            source = path.read_bytes()
            source_sha256 = hashlib.sha256(source).hexdigest()
            marker = await session.get(
                ServiceAuthBootstrapState,
                (LEGACY_CLIENTS_BOOTSTRAP_ID, source_sha256),
                with_for_update=True,
            )
            if marker is not None:
                return

            raw_clients = json.loads(source)
            if not isinstance(raw_clients, Mapping):
                raise RuntimeError("SERVICE_AUTH_CLIENTS_FILE must contain a JSON object")
            now = datetime.now(UTC)
            node_label = os.environ.get("HOSTNAME", "legacy-import")[:255]
            for raw_client_id, raw_client in sorted(
                raw_clients.items(), key=lambda item: str(item[0])
            ):
                client_id = str(raw_client_id).strip()
                if not client_id or len(client_id) > 128 or not isinstance(raw_client, Mapping):
                    raise RuntimeError("Invalid client in SERVICE_AUTH_CLIENTS_FILE")
                client = await session.get(
                    ServiceAuthClient,
                    client_id,
                    with_for_update=True,
                )
                client_is_new = client is None
                if client_is_new:
                    session.add(
                        ServiceAuthClient(
                            client_id=client_id,
                            display_name=client_id,
                            is_active=True,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    await session.flush()
                digest = str(raw_client.get("secret_sha256", "")).lower()
                if not _DIGEST.fullmatch(digest):
                    raise RuntimeError(f"Invalid secret digest for service client {client_id!r}")
                statement = postgres_insert(ServiceAuthClientCredential).values(
                    client_id=client_id,
                    secret_sha256=digest,
                    label=node_label,
                    is_active=True,
                    created_at=now,
                )
                await session.execute(
                    statement.on_conflict_do_nothing(
                        constraint="uq_service_auth_credentials_client_secret"
                    )
                )
                grants = raw_client.get("grants", {})
                if not isinstance(grants, Mapping):
                    raise RuntimeError(f"Invalid grants for service client {client_id!r}")
                for raw_audience, raw_scopes in sorted(
                    grants.items(), key=lambda item: str(item[0])
                ):
                    audience = str(raw_audience).strip()
                    scopes = " ".join(dict.fromkeys(str(raw_scopes).split()))
                    if not audience or not scopes:
                        raise RuntimeError(f"Invalid grant for service client {client_id!r}")
                    # A new node/source may add its credential to an existing
                    # service identity, but legacy JSON never becomes an
                    # ongoing authority for that identity's grants.
                    if not client_is_new:
                        continue
                    statement = postgres_insert(ServiceAuthGrant).values(
                        client_id=client_id,
                        audience=audience,
                        scopes=scopes,
                        created_at=now,
                        updated_at=now,
                    )
                    await session.execute(
                        statement.on_conflict_do_nothing(index_elements=["client_id", "audience"])
                    )
            session.add(
                ServiceAuthBootstrapState(
                    bootstrap_id=LEGACY_CLIENTS_BOOTSTRAP_ID,
                    source_sha256=source_sha256,
                    completed_by=node_label,
                    completed_at=now,
                )
            )

    def _retire_legacy_private_key(self, active_kid: str) -> None:
        """Remove imported PEM material without risking the PostgreSQL copy.

        A bind-mounted file cannot normally be unlinked from inside a
        container. In that case replace its contents with a non-secret marker;
        the marker is non-empty so the legacy initialization script will not
        silently generate a new, divergent key on the next run.
        """

        path = Path(self.legacy_private_key_file) if self.legacy_private_key_file else None
        if path is None or not path.is_file():
            return
        try:
            if path.read_text() == LEGACY_PRIVATE_KEY_RETIRED_MARKER:
                return
            legacy_kid, _private_pem, _public_pem = load_signing_key(str(path))
        except (OSError, UnicodeError, ValueError):
            logger.warning(
                "Legacy service-auth private key at %s is not a readable RSA key; "
                "leaving it untouched",
                path,
            )
            return
        if legacy_kid != active_kid:
            logger.warning(
                "Legacy service-auth private key at %s does not match PostgreSQL "
                "active kid %s; erasing the obsolete local key",
                path,
                active_kid,
            )
        try:
            path.unlink()
            logger.info("Removed imported legacy service-auth private key %s", path)
            return
        except OSError:
            pass

        try:
            size = path.stat().st_size
            marker = LEGACY_PRIVATE_KEY_RETIRED_MARKER.encode()
            with path.open("r+b", buffering=0) as stream:
                remaining = size
                while remaining > 0:
                    chunk_size = min(remaining, 4096)
                    stream.write(os.urandom(chunk_size))
                    remaining -= chunk_size
                stream.flush()
                os.fsync(stream.fileno())
                stream.seek(0)
                stream.write(marker)
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
            path.chmod(0o400)
            logger.info("Erased imported legacy service-auth private key %s", path)
        except OSError:
            logger.exception(
                "PostgreSQL contains the imported signing key, but legacy private key %s "
                "could not be removed; remove it from the host manually",
                path,
            )

    async def ensure_active_signing_key(self, *, force_rotate: bool = False) -> SigningMaterial:
        now = datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            await self._advisory_lock(session, _KEY_LOCK)
            active = await session.scalar(
                select(ServiceAuthSigningKey)
                .where(ServiceAuthSigningKey.status == "active")
                .order_by(ServiceAuthSigningKey.created_at.desc())
                .with_for_update()
            )
            rotate = bool(
                active and (force_rotate or active.created_at <= now - self.rotation_after)
            )
            if active is not None and not rotate:
                return self._material(active)
            if active is not None:
                # Prove that this replica has the cluster KEK before it can
                # retire the current authority and create replacement material.
                # Without this guard, a wrong-KEK replica reaching the rotation
                # window first could make every correctly configured peer fail.
                self._material(active)
                active.status = "retired"
                active.retired_at = now
                active.expires_at = now + self.overlap
                await session.flush()

            legacy_path = (
                Path(self.legacy_private_key_file) if self.legacy_private_key_file else None
            )
            legacy_available = bool(
                active is None
                and legacy_path is not None
                and legacy_path.is_file()
                and legacy_path.read_text() != LEGACY_PRIVATE_KEY_RETIRED_MARKER
            )
            if legacy_available and legacy_path is not None:
                kid, private_pem, public_pem = load_signing_key(str(legacy_path))
            else:
                kid, private_pem, public_pem = generate_signing_key()
            row = ServiceAuthSigningKey(
                kid=kid,
                algorithm=ALGORITHM,
                private_key_ciphertext=encrypt_private_key(private_pem, kid, self.master_secret),
                public_key_pem=public_pem,
                status="active",
                not_before=now,
                created_at=now,
            )
            session.add(row)
            await session.flush()
            return SigningMaterial(kid, private_pem, public_pem)

    def _material(self, row: ServiceAuthSigningKey) -> SigningMaterial:
        return SigningMaterial(
            kid=row.kid,
            private_key=decrypt_private_key(
                row.private_key_ciphertext, row.kid, self.master_secret
            ),
            public_key=row.public_key_pem,
            algorithm=row.algorithm,
        )

    async def active_signing_material(self) -> SigningMaterial:
        async with self.session_factory() as session:
            row = await session.scalar(
                select(ServiceAuthSigningKey).where(ServiceAuthSigningKey.status == "active")
            )
            if row is not None and row.created_at > datetime.now(UTC) - self.rotation_after:
                return self._material(row)
        return await self.ensure_active_signing_key()

    async def jwks(self) -> dict[str, list[dict[str, str]]]:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            rows = (
                await session.scalars(
                    select(ServiceAuthSigningKey)
                    .where(
                        or_(
                            ServiceAuthSigningKey.status == "active",
                            and_(
                                ServiceAuthSigningKey.status == "retired",
                                ServiceAuthSigningKey.expires_at > now,
                            ),
                        )
                    )
                    .order_by(ServiceAuthSigningKey.created_at.desc())
                )
            ).all()
        return {"keys": [public_key_jwk(row.public_key_pem, row.kid) for row in rows]}

    async def authenticate_client(self, client_id: str, secret: str, audience: str) -> str | None:
        now = datetime.now(UTC)
        digest = hashlib.sha256(secret.encode()).hexdigest()
        async with self.session_factory() as session, session.begin():
            client = await session.get(ServiceAuthClient, client_id)
            if client is None or not client.is_active:
                return None
            credentials = (
                await session.scalars(
                    select(ServiceAuthClientCredential).where(
                        ServiceAuthClientCredential.client_id == client_id,
                        ServiceAuthClientCredential.is_active.is_(True),
                        or_(
                            ServiceAuthClientCredential.expires_at.is_(None),
                            ServiceAuthClientCredential.expires_at > now,
                        ),
                    )
                )
            ).all()
            credential = next(
                (item for item in credentials if hmac.compare_digest(digest, item.secret_sha256)),
                None,
            )
            grant = await session.get(ServiceAuthGrant, (client_id, audience))
            if credential is None or grant is None:
                return None
            credential.last_used_at = now
            client.last_authenticated_at = now
            return grant.scopes

    async def create_user_session(
        self,
        admin_id: int,
        *,
        lifetime: timedelta,
        session: AsyncSession | None = None,
    ) -> ServiceAuthSession:
        now = datetime.now(UTC)
        row = ServiceAuthSession(
            sid=str(uuid.uuid4()),
            admin_id=admin_id,
            jti=str(uuid.uuid4()),
            expires_at=now + lifetime,
            created_at=now,
        )
        if session is not None:
            session.add(row)
            await session.flush()
            return row
        async with self.session_factory() as owned_session, owned_session.begin():
            owned_session.add(row)
        return row

    async def validate_user_session(
        self,
        *,
        sid: str,
        jti: str,
        admin_id: int,
        redis: Any | None = None,
    ) -> Admin | None:
        if redis is not None:
            try:
                cached = await redis.get(f"{SESSION_CACHE_PREFIX}{sid}")
                if cached == "revoked":
                    return None
            except Exception:  # noqa: BLE001 - PostgreSQL is the session authority
                # Redis only accelerates revocation checks. Falling back to the
                # durable PostgreSQL rows is especially important for logout:
                # an unavailable cache must not prevent recording a revocation.
                logger.warning(
                    "Authentication session cache unavailable; validating %s in PostgreSQL",
                    sid,
                    exc_info=True,
                )
        now = datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            row = await session.scalar(
                select(ServiceAuthSession).where(
                    ServiceAuthSession.sid == sid,
                    ServiceAuthSession.jti == jti,
                    ServiceAuthSession.admin_id == admin_id,
                    ServiceAuthSession.revoked_at.is_(None),
                    ServiceAuthSession.expires_at > now,
                )
            )
            if row is None or await session.get(ServiceAuthRevocation, jti) is not None:
                return None
            admin = await session.get(Admin, admin_id)
            if admin is None or not admin.is_active:
                return None
            row.last_seen_at = now
            return admin

    async def revoke_session(
        self,
        sid: str,
        *,
        reason: str,
        redis: Any | None = None,
    ) -> bool:
        now = datetime.now(UTC)
        expires_at: datetime | None = None
        async with self.session_factory() as session, session.begin():
            row = await session.get(ServiceAuthSession, sid, with_for_update=True)
            if row is None:
                return False
            expires_at = row.expires_at
            if row.revoked_at is None:
                row.revoked_at = now
                row.revoke_reason = reason
                statement = postgres_insert(ServiceAuthRevocation).values(
                    jti=row.jti,
                    sid=row.sid,
                    expires_at=row.expires_at,
                    revoked_at=now,
                    reason=reason,
                )
                await session.execute(statement.on_conflict_do_nothing(index_elements=["jti"]))
        if redis is not None and expires_at is not None:
            ttl = max(1, int((expires_at - now).total_seconds()))
            await redis.set(f"{SESSION_CACHE_PREFIX}{sid}", "revoked", ex=ttl)
        return True

    async def revoke_admin_sessions(
        self,
        admin_id: int,
        *,
        reason: str,
        redis: Any | None = None,
    ) -> list[str]:
        async with self.session_factory() as owned_session, owned_session.begin():
            revoked = await self.revoke_admin_sessions_in_transaction(
                owned_session, admin_id, reason=reason
            )
        await self.cache_revoked_sessions(revoked, redis=redis)
        return [sid for sid, _expires_at in revoked]

    async def revoke_admin_sessions_in_transaction(
        self,
        session: AsyncSession,
        admin_id: int,
        *,
        reason: str,
    ) -> list[tuple[str, datetime]]:
        now = datetime.now(UTC)
        revoked: list[tuple[str, datetime]] = []
        rows = (
            await session.scalars(
                select(ServiceAuthSession)
                .where(
                    ServiceAuthSession.admin_id == admin_id,
                    ServiceAuthSession.revoked_at.is_(None),
                    ServiceAuthSession.expires_at > now,
                )
                .with_for_update()
            )
        ).all()
        for row in rows:
            row.revoked_at = now
            row.revoke_reason = reason
            revoked.append((row.sid, row.expires_at))
            statement = postgres_insert(ServiceAuthRevocation).values(
                jti=row.jti,
                sid=row.sid,
                expires_at=row.expires_at,
                revoked_at=now,
                reason=reason,
            )
            await session.execute(statement.on_conflict_do_nothing(index_elements=["jti"]))
        return revoked

    async def cache_revoked_sessions(
        self,
        revoked: list[tuple[str, datetime]],
        *,
        redis: Any | None,
    ) -> None:
        if redis is None:
            return
        now = datetime.now(UTC)
        for sid, expires_at in revoked:
            ttl = max(1, int((expires_at - now).total_seconds()))
            await redis.set(f"{SESSION_CACHE_PREFIX}{sid}", "revoked", ex=ttl)

    async def audit(
        self,
        event_type: str,
        *,
        actor_type: str,
        success: bool,
        actor_id: str | None = None,
        subject: str | None = None,
        audience: str | None = None,
        sid: str | None = None,
        jti: str | None = None,
        remote_address: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        async with self.session_factory() as session, session.begin():
            session.add(
                ServiceAuthAudit(
                    event_type=event_type,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    subject=subject,
                    audience=audience,
                    sid=sid,
                    jti=jti,
                    success=success,
                    remote_address=remote_address,
                    details=details,
                    created_at=datetime.now(UTC),
                )
            )

    async def decode_and_validate_user_token(
        self, token: str, *, redis: Any | None = None
    ) -> tuple[dict[str, Any], Admin] | None:
        try:
            header = jwt.get_unverified_header(token)
            kid = str(header["kid"])
        except (jwt.InvalidTokenError, KeyError, TypeError):
            return None
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            key = await session.get(ServiceAuthSigningKey, kid)
            if key is None or key.status == "revoked":
                return None
            if key.expires_at is not None and key.expires_at <= now:
                return None
        try:
            claims = jwt.decode(
                token,
                key.public_key_pem,
                algorithms=[ALGORITHM],
                audience=USER_AUDIENCE,
                issuer=ISSUER,
                options={
                    "require": [
                        "sub",
                        "uid",
                        "sid",
                        "jti",
                        "kid",
                        "aud",
                        "iss",
                        "iat",
                        "nbf",
                        "exp",
                        "type",
                        "scope",
                    ]
                },
            )
            if (
                claims["type"] != "user"
                or claims["kid"] != kid
                or "admin" not in str(claims["scope"]).split()
            ):
                return None
            admin = await self.validate_user_session(
                sid=str(claims["sid"]),
                jti=str(claims["jti"]),
                admin_id=int(claims["uid"]),
                redis=redis,
            )
        except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
            return None
        return (claims, admin) if admin is not None else None

    async def cleanup_expired(self) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            await session.execute(
                update(ServiceAuthSigningKey)
                .where(
                    ServiceAuthSigningKey.status == "retired",
                    ServiceAuthSigningKey.expires_at <= now,
                )
                .values(status="revoked")
            )
            # Sessions and deny-list entries have no value after their JWTs
            # expire. Bound these high-churn tables while retaining audit rows.
            await session.execute(
                delete(ServiceAuthSession).where(ServiceAuthSession.expires_at <= now)
            )
            await session.execute(
                delete(ServiceAuthRevocation).where(ServiceAuthRevocation.expires_at <= now)
            )
