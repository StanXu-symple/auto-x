from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.control_plane.auth_store as auth_store_module
from app.api.routes.auth import _forwarded_for
from app.control_plane.auth import _ip_rate_key, _rate_key, _record_attempt, _remote_address
from app.control_plane.auth_store import (
    ALGORITHM,
    ISSUER,
    LEGACY_CLIENTS_BOOTSTRAP_ID,
    LEGACY_PRIVATE_KEY_RETIRED_MARKER,
    USER_AUDIENCE,
    AuthStore,
    KeyEncryptionKeyMismatchError,
    decrypt_private_key,
    encrypt_private_key,
    generate_signing_key,
    public_key_jwk,
    resolve_key_encryption_secret,
)
from app.models.service_auth import (
    ServiceAuthBootstrapState,
    ServiceAuthClient,
    ServiceAuthClientCredential,
    ServiceAuthGrant,
    ServiceAuthRevocation,
    ServiceAuthSession,
    ServiceAuthSigningKey,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def eval(self, _script: str, _key_count: int, key: str, window: int):
        self.values[key] = self.values.get(key, 0) + 1
        self.ttls.setdefault(key, int(window))
        return [self.values[key], self.ttls[key]]


class _BootstrapState:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.markers: dict[tuple[str, str], ServiceAuthBootstrapState] = {}
        self.clients: set[str] = set()
        self.credential_rows = 0
        self.grant_rows = 0


class _BootstrapTransaction:
    def __init__(self, session: _BootstrapSession) -> None:
        self.session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, _exc, _traceback) -> None:
        if exc_type is None and self.session.pending_marker is not None:
            marker = self.session.pending_marker
            self.session.state.markers[(marker.bootstrap_id, marker.source_sha256)] = marker
        if self.session.has_lock:
            self.session.state.lock.release()


class _PostgresDialect:
    name = "postgresql"


class _PostgresBind:
    dialect = _PostgresDialect()


class _BootstrapSession:
    def __init__(self, state: _BootstrapState) -> None:
        self.state = state
        self.pending_marker: ServiceAuthBootstrapState | None = None
        self.pending_clients: set[str] = set()
        self.has_lock = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def begin(self) -> _BootstrapTransaction:
        return _BootstrapTransaction(self)

    def get_bind(self) -> _PostgresBind:
        return _PostgresBind()

    async def execute(self, _statement: Any, parameters: dict[str, int] | None = None):
        if parameters and "lock_id" in parameters:
            await self.state.lock.acquire()
            self.has_lock = True
        else:
            table_name = getattr(getattr(_statement, "table", None), "name", "")
            if table_name == "service_auth_client_credentials":
                self.state.credential_rows += 1
            elif table_name == "service_auth_grants":
                self.state.grant_rows += 1

    async def get(self, model, key, *, with_for_update: bool = False):
        assert with_for_update is True
        assert self.has_lock
        if model is ServiceAuthBootstrapState:
            assert isinstance(key, tuple)
            assert key[0] == LEGACY_CLIENTS_BOOTSTRAP_ID
            return self.state.markers.get(key)
        assert model is ServiceAuthClient
        return ServiceAuthClient(client_id=key) if key in self.state.clients else None

    def add(self, row: Any) -> None:
        if isinstance(row, ServiceAuthBootstrapState):
            self.pending_marker = row
        else:
            assert isinstance(row, ServiceAuthClient)
            self.pending_clients.add(row.client_id)

    async def flush(self) -> None:
        self.state.clients.update(self.pending_clients)


class _BootstrapSessionFactory:
    def __init__(self, state: _BootstrapState) -> None:
        self.state = state

    def __call__(self) -> _BootstrapSession:
        return _BootstrapSession(self.state)


class _RotationSession:
    def __init__(self, active: ServiceAuthSigningKey) -> None:
        self.active = active
        self.added: list[ServiceAuthSigningKey] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def begin(self):
        return self

    def get_bind(self):
        return type("Bind", (), {"dialect": type("Dialect", (), {"name": "sqlite"})()})()

    async def scalar(self, _statement):
        return self.active

    def add(self, row: ServiceAuthSigningKey) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        return None


def _request(peer: str, *, forwarded_for: str = "") -> Request:
    headers = []
    if forwarded_for:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/admin/login",
            "headers": headers,
            "client": (peer, 12345),
            "server": ("test", 80),
            "scheme": "http",
        }
    )


def test_signing_key_is_encrypted_and_jwk_verifies_kid_token() -> None:
    kid, private_key, public_key = generate_signing_key()
    master = "a-shared-key-encryption-secret-with-32-bytes"
    ciphertext = encrypt_private_key(private_key, kid, master)

    assert private_key not in ciphertext
    assert decrypt_private_key(ciphertext, kid, master) == private_key
    with pytest.raises(KeyEncryptionKeyMismatchError):
        decrypt_private_key(ciphertext, kid, "another-shared-secret-that-is-long-enough")

    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "admin",
            "uid": 1,
            "sid": str(uuid.uuid4()),
            "jti": str(uuid.uuid4()),
            "kid": kid,
            "aud": USER_AUDIENCE,
            "iss": ISSUER,
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=5),
            "type": "user",
            "scope": "admin",
        },
        private_key,
        algorithm=ALGORITHM,
        headers={"kid": kid},
    )
    claims = jwt.decode(
        token,
        jwt.PyJWK.from_dict(public_key_jwk(public_key, kid)).key,
        algorithms=[ALGORITHM],
        audience=USER_AUDIENCE,
        issuer=ISSUER,
    )
    assert jwt.get_unverified_header(token)["kid"] == kid
    assert claims["kid"] == kid
    assert claims["type"] == "user"


def test_signing_key_overlap_never_underflows_live_token_lifetime() -> None:
    minimum = timedelta(days=7, minutes=10)
    store = AuthStore(
        None,  # type: ignore[arg-type]
        master_secret="a-shared-key-encryption-secret-with-32-bytes",
        overlap_days=1,
        minimum_overlap=minimum,
    )

    assert store.overlap == minimum


async def test_rotation_rejects_wrong_kek_before_retiring_active_key() -> None:
    kid, private_key, public_key = generate_signing_key()
    correct_secret = "correct-cluster-key-encryption-secret-at-least-32-bytes"
    active = ServiceAuthSigningKey(
        kid=kid,
        algorithm=ALGORITHM,
        private_key_ciphertext=encrypt_private_key(private_key, kid, correct_secret),
        public_key_pem=public_key,
        status="active",
        not_before=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    session = _RotationSession(active)
    store = AuthStore(
        lambda: session,  # type: ignore[arg-type]
        master_secret="wrong-cluster-key-encryption-secret-at-least-32-bytes",
    )

    with pytest.raises(KeyEncryptionKeyMismatchError):
        await store.ensure_active_signing_key(force_rotate=True)

    assert active.status == "active"
    assert session.added == []


async def test_initialize_validates_signing_key_before_importing_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    signing = type("Signing", (), {"kid": "cluster-key"})()
    store = AuthStore(
        None,  # type: ignore[arg-type]
        master_secret="a-shared-key-encryption-secret-with-32-bytes",
    )

    async def ensure():
        events.append("signing")
        return signing

    async def bootstrap():
        events.append("clients")

    def retire(_kid: str):
        events.append("retire")

    monkeypatch.setattr(store, "ensure_active_signing_key", ensure)
    monkeypatch.setattr(store, "_bootstrap_legacy_clients", bootstrap)
    monkeypatch.setattr(store, "_retire_legacy_private_key", retire)

    await store.initialize()

    assert events == ["signing", "clients", "retire"]


def test_production_requires_a_dedicated_non_placeholder_kek() -> None:
    legacy = "legacy-application-secret-that-is-at-least-32-bytes"

    with pytest.raises(RuntimeError, match="is required in production"):
        resolve_key_encryption_secret("production", "", legacy_x_token_secret=legacy)
    with pytest.raises(RuntimeError, match="placeholder"):
        resolve_key_encryption_secret(
            "production",
            "replace-with-a-real-auth-center-key-at-least-32-bytes",
        )
    with pytest.raises(RuntimeError, match="independent from X_TOKEN_ENCRYPTION_KEY"):
        resolve_key_encryption_secret(
            "production",
            legacy,
            legacy_x_token_secret=legacy,
        )

    dedicated = "dedicated-service-auth-kek-that-is-at-least-32-bytes"
    assert (
        resolve_key_encryption_secret(
            "production",
            dedicated,
            legacy_x_token_secret=legacy,
        )
        == dedicated
    )


def test_development_retains_explicit_legacy_kek_compatibility() -> None:
    legacy = "legacy-development-secret-that-is-at-least-32-bytes"
    assert (
        resolve_key_encryption_secret(
            "development",
            "",
            legacy_x_token_secret=legacy,
        )
        == legacy
    )


async def test_legacy_clients_bootstrap_is_cluster_wide_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clients = tmp_path / "clients.json"
    source_a = """{
          "backend": {
            "secret_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "grants": {"xhs-worker": "xhs:execute"}
          }
        }"""
    source_b = """{
          "backend": {
            "secret_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "grants": {"xhs-worker": "must:not-restore"}
          }
        }"""
    clients.write_text(source_a)
    state = _BootstrapState()
    factory = _BootstrapSessionFactory(state)
    stores = [
        AuthStore(
            factory,  # type: ignore[arg-type]
            master_secret="a-shared-key-encryption-secret-with-32-bytes",
            legacy_clients_file=str(clients),
        )
        for _ in range(2)
    ]

    await asyncio.gather(*(store._bootstrap_legacy_clients() for store in stores))

    source_a_sha256 = hashlib.sha256(source_a.encode()).hexdigest()
    assert state.credential_rows == 1
    assert state.grant_rows == 1
    assert (LEGACY_CLIENTS_BOOTSTRAP_ID, source_a_sha256) in state.markers

    clients.write_text(source_b)
    await stores[0]._bootstrap_legacy_clients()
    source_b_sha256 = hashlib.sha256(source_b.encode()).hexdigest()
    assert state.credential_rows == 2
    assert state.grant_rows == 1
    assert (LEGACY_CLIENTS_BOOTSTRAP_ID, source_b_sha256) in state.markers

    # Once each source is marked complete, deliberately removed credentials
    # and grants stay removed and neither source is parsed again.
    state.credential_rows = 0
    state.grant_rows = 0

    def must_not_parse(_source: bytes):
        raise AssertionError("a completed legacy source must not be parsed again")

    monkeypatch.setattr(auth_store_module.json, "loads", must_not_parse)
    clients.write_text(source_a)
    await stores[0]._bootstrap_legacy_clients()
    clients.write_text(source_b)
    await stores[0]._bootstrap_legacy_clients()
    assert state.credential_rows == 0
    assert state.grant_rows == 0


def test_imported_legacy_private_key_is_erased_when_unlink_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kid, private_key, _public_key = generate_signing_key()
    legacy_key = tmp_path / "private.pem"
    legacy_key.write_text(private_key)
    store = AuthStore(
        None,  # type: ignore[arg-type]
        master_secret="a-shared-key-encryption-secret-with-32-bytes",
        legacy_private_key_file=str(legacy_key),
    )

    def bind_mount_unlink(_path: Path) -> None:
        raise OSError("bind mount cannot be unlinked")

    monkeypatch.setattr(Path, "unlink", bind_mount_unlink)
    store._retire_legacy_private_key(kid)

    assert legacy_key.read_text() == LEGACY_PRIVATE_KEY_RETIRED_MARKER
    assert private_key not in legacy_key.read_text()


def test_mismatched_legacy_private_key_is_removed_as_obsolete(tmp_path: Path) -> None:
    _kid, private_key, _public_key = generate_signing_key()
    legacy_key = tmp_path / "private.pem"
    legacy_key.write_text(private_key)
    store = AuthStore(
        None,  # type: ignore[arg-type]
        master_secret="a-shared-key-encryption-secret-with-32-bytes",
        legacy_private_key_file=str(legacy_key),
    )

    store._retire_legacy_private_key("a-different-postgresql-kid")

    assert not legacy_key.exists()


async def test_auth_rate_limit_is_shared_through_atomic_redis_counter() -> None:
    redis = FakeRedis()
    await _record_attempt(redis, "auth:test", 2, 60)  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as exc_info:
        await _record_attempt(redis, "auth:test", 2, 60)  # type: ignore[arg-type]
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "60"}


def test_forwarded_address_is_only_trusted_from_private_proxy() -> None:
    assert _remote_address(_request("172.19.0.3", forwarded_for="198.51.100.8")) == ("198.51.100.8")
    assert _remote_address(_request("8.8.8.8", forwarded_for="198.51.100.8")) == "8.8.8.8"
    assert _remote_address(_request("172.19.0.3", forwarded_for="not-an-ip")) == "172.19.0.3"
    assert (
        _remote_address(_request("172.19.0.3", forwarded_for="198.51.100.8, 203.0.113.9"))
        == "172.19.0.3"
    )


def test_backend_rejects_forwarded_chains_and_public_peer_spoofing() -> None:
    assert _forwarded_for(_request("172.19.0.4", forwarded_for="198.51.100.8")) == ("198.51.100.8")
    assert (
        _forwarded_for(_request("172.19.0.4", forwarded_for="1.1.1.1, 198.51.100.8"))
        == "172.19.0.4"
    )
    assert _forwarded_for(_request("8.8.8.8", forwarded_for="1.1.1.1")) == "8.8.8.8"


def test_admin_login_has_an_ip_bucket_independent_of_username() -> None:
    first = _request("172.19.0.3", forwarded_for="198.51.100.8")
    second = _request("172.19.0.3", forwarded_for="198.51.100.8")
    other_ip = _request("172.19.0.3", forwarded_for="198.51.100.9")

    assert _rate_key("admin", first, "alice") != _rate_key("admin", second, "bob")
    assert _ip_rate_key("admin", first) == _ip_rate_key("admin", second)
    assert _ip_rate_key("admin", first) != _ip_rate_key("admin", other_ip)


async def test_ip_bucket_limits_requests_across_different_usernames() -> None:
    redis = FakeRedis()
    alice_request = _request("172.19.0.3", forwarded_for="198.51.100.8")
    bob_request = _request("172.19.0.3", forwarded_for="198.51.100.8")

    await _record_attempt(
        redis,
        _ip_rate_key("admin", alice_request),
        2,
        60,  # type: ignore[arg-type]
    )
    with pytest.raises(HTTPException) as exc_info:
        await _record_attempt(
            redis,
            _ip_rate_key("admin", bob_request),
            2,
            60,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 429


def test_auth_models_form_the_durable_multi_node_contract() -> None:
    assert set(ServiceAuthBootstrapState.__table__.columns.keys()) == {
        "bootstrap_id",
        "source_sha256",
        "completed_by",
        "completed_at",
    }
    assert set(ServiceAuthClientCredential.__table__.columns.keys()) >= {
        "client_id",
        "secret_sha256",
        "is_active",
        "expires_at",
    }
    assert set(ServiceAuthGrant.__table__.primary_key.columns.keys()) == {
        "client_id",
        "audience",
    }
    assert set(ServiceAuthSigningKey.__table__.columns.keys()) >= {
        "kid",
        "private_key_ciphertext",
        "public_key_pem",
        "status",
        "expires_at",
    }
    assert set(ServiceAuthSession.__table__.columns.keys()) >= {
        "sid",
        "admin_id",
        "jti",
        "revoked_at",
        "expires_at",
    }
    assert set(ServiceAuthRevocation.__table__.columns.keys()) >= {
        "jti",
        "sid",
        "expires_at",
    }
    assert not any(
        index.name == "ix_service_auth_sessions_jti"
        for index in ServiceAuthSession.__table__.indexes
    )
