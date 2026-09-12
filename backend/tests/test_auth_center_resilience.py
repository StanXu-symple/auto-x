from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx

import app.control_plane.auth as auth_module
from app.control_plane.auth_store import AuthStore
from app.models.admin import Admin
from app.models.service_auth import ServiceAuthRevocation


def test_frontend_nginx_replaces_untrusted_forwarded_for_chain() -> None:
    config = (Path(__file__).resolve().parents[2] / "frontend" / "nginx.conf").read_text(
        encoding="utf-8"
    )

    assert "$proxy_add_x_forwarded_for" not in config
    assert config.count("proxy_set_header X-Forwarded-For $remote_addr;") == 5


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False


class _Session:
    def __init__(self, admin) -> None:
        self.admin = admin

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False

    def begin(self):
        return _Transaction()

    async def get(self, _model, _identity, **_kwargs):
        return self.admin


class _ValidationSession(_Session):
    def __init__(self, admin, auth_session) -> None:
        super().__init__(admin)
        self.auth_session = auth_session

    async def scalar(self, _statement):
        return self.auth_session

    async def get(self, model, _identity, **_kwargs):
        if model is ServiceAuthRevocation:
            return None
        if model is Admin:
            return self.admin
        raise AssertionError(f"unexpected model: {model}")


class _UnavailableRedis:
    async def get(self, _key: str):
        raise ConnectionError("redis unavailable before PostgreSQL validation")


class _StoreWithBrokenPostCommitDependencies:
    def __init__(self) -> None:
        self.revoked_session = False
        self.revoked_all = False
        self.cache_attempts = 0
        self.audit_attempts = 0

    async def revoke_session(self, _sid: str, *, reason: str, redis=None) -> bool:
        assert reason == "logout"
        assert redis is None
        self.revoked_session = True
        return True

    async def revoke_admin_sessions_in_transaction(self, _session, _admin_id: int, *, reason: str):
        assert reason == "password_changed"
        self.revoked_all = True
        return [("session-id", datetime.now(UTC) + timedelta(minutes=5))]

    async def cache_revoked_sessions(self, _revoked, *, redis) -> None:
        assert redis is not None
        self.cache_attempts += 1
        raise ConnectionError("redis unavailable after PostgreSQL commit")

    async def audit(self, *_args, **_kwargs) -> None:
        self.audit_attempts += 1
        raise RuntimeError("audit insert unavailable after PostgreSQL commit")


async def test_session_validation_falls_back_to_postgres_when_redis_is_unavailable() -> None:
    now = datetime.now(UTC)
    admin = SimpleNamespace(id=7, username="admin", is_active=True)
    auth_session = SimpleNamespace(
        sid="session-id",
        jti="token-id",
        admin_id=7,
        revoked_at=None,
        expires_at=now + timedelta(minutes=5),
        last_seen_at=None,
    )
    store = AuthStore(
        lambda: _ValidationSession(admin, auth_session),  # type: ignore[arg-type]
        master_secret="a-shared-key-encryption-secret-with-32-bytes",
    )

    result = await store.validate_user_session(
        sid="session-id",
        jti="token-id",
        admin_id=7,
        redis=_UnavailableRedis(),
    )

    assert result is admin
    assert auth_session.last_seen_at is not None


async def test_logout_and_password_change_survive_post_commit_cache_and_audit_failures(
    monkeypatch,
) -> None:
    store = _StoreWithBrokenPostCommitDependencies()
    admin = SimpleNamespace(id=7, username="admin", is_active=True, password_hash="old-hash")
    claims = {
        "sid": "session-id",
        "jti": "token-id",
        "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
    }

    async def current_admin(_request):
        return claims, admin

    monkeypatch.setattr(auth_module, "_current_admin", current_admin)
    monkeypatch.setattr(auth_module, "AsyncSessionFactory", lambda: _Session(admin))
    monkeypatch.setattr(auth_module, "verify_password", lambda _plain, _digest: True)
    monkeypatch.setattr(auth_module, "hash_password", lambda _password: "new-hash")

    missing = object()
    previous_store = getattr(auth_module.app.state, "store", missing)
    previous_redis = getattr(auth_module.app.state, "redis", missing)
    auth_module.app.state.store = store
    auth_module.app.state.redis = object()
    try:
        transport = httpx.ASGITransport(app=auth_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            logout = await client.post("/v1/admin/logout")
            password = await client.patch(
                "/v1/admin/password",
                json={
                    "current_password": "existing-password",
                    "new_password": "new-secure-password",
                },
            )
    finally:
        if previous_store is missing:
            del auth_module.app.state.store
        else:
            auth_module.app.state.store = previous_store
        if previous_redis is missing:
            del auth_module.app.state.redis
        else:
            auth_module.app.state.redis = previous_redis

    assert logout.status_code == 200
    assert password.status_code == 200
    assert store.revoked_session is True
    assert store.revoked_all is True
    assert store.cache_attempts == 2
    assert store.audit_attempts == 2
    assert admin.password_hash == "new-hash"
