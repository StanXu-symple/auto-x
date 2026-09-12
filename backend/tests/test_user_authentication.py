from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.api.deps import authenticate_admin
from app.api.errors import APIError
from app.control_plane.security import ServiceVerifier
from app.core.config import Settings
from app.models.admin import Admin
from app.services.auth_center import (
    ADMIN_AUDIENCE,
    AUTH_ISSUER,
    AuthCenterClient,
    AuthCenterRejected,
    AuthCenterUnavailable,
)


def auth_settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        nacos_config_enabled=False,
        nacos_server_addr="",
        service_auth_url="http://auth-center:9100",
        monitor_center_url="http://monitor-center:9102",
    )


def signing_material(kid: str):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return private_key, jwk


def user_token(private_key, kid: str, **overrides) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": "admin",
        "uid": 7,
        "sid": "12345678-1234-4234-9234-123456789abc",
        "jti": "abcdefab-1234-4234-9234-123456789abc",
        "kid": kid,
        "aud": ADMIN_AUDIENCE,
        "iss": AUTH_ISSUER,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
        "type": "user",
        "scope": "admin",
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def service_token(private_key, kid: str, **overrides) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": "monitor",
        "jti": "fedcbafe-1234-4234-9234-123456789abc",
        "kid": kid,
        "aud": "agent:local",
        "iss": AUTH_ISSUER,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=2),
        "type": "service",
        "scope": "resources:read",
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


async def test_user_token_is_verified_from_cached_jwks() -> None:
    private_key, public_jwk = signing_material("key-one")
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        assert request.url.path == "/.well-known/jwks.json"
        requests += 1
        return httpx.Response(200, json={"keys": [public_jwk]})

    remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AuthCenterClient(auth_settings(), http=remote)
    token = user_token(private_key, "key-one")
    try:
        first = await client.verify_user_token(token)
        second = await client.verify_user_token(token)
    finally:
        await remote.aclose()

    assert first == second
    assert first["uid"] == 7
    assert requests == 1


async def test_jwks_is_refreshed_when_auth_center_rotates_to_a_new_kid() -> None:
    first_private, first_public = signing_material("key-one")
    second_private, second_public = signing_material("key-two")
    active_keys = [first_public]
    requests = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"keys": active_keys})

    remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AuthCenterClient(auth_settings(), http=remote)
    try:
        await client.verify_user_token(user_token(first_private, "key-one"))
        active_keys[:] = [first_public, second_public]
        claims = await client.verify_user_token(user_token(second_private, "key-two"))
    finally:
        await remote.aclose()

    assert claims["uid"] == 7
    assert requests == 2


async def test_user_jwks_cache_honors_max_age_and_sixty_second_cap() -> None:
    _private_key, public_jwk = signing_material("key-one")
    cache_control = "public, max-age=7"

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"keys": [public_jwk]},
            headers={"Cache-Control": cache_control},
        )

    remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AuthCenterClient(auth_settings(), http=remote, jwks_ttl_seconds=300)
    try:
        await client._refresh_jwks()
        first_ttl = client._keys_expire_at - time.monotonic()
        client._keys_expire_at = 0
        cache_control = "public, max-age=600"
        await client._refresh_jwks()
        capped_ttl = client._keys_expire_at - time.monotonic()
    finally:
        await remote.aclose()

    assert 6.5 <= first_ttl <= 7
    assert 59.5 <= capped_ttl <= 60


async def test_user_unknown_kids_are_refresh_cooled_but_cache_expiry_finds_new_key() -> None:
    first_private, first_public = signing_material("key-one")
    second_private, second_public = signing_material("key-two")
    fake_private, _fake_public = signing_material("fake-one")
    other_fake_private, _other_fake_public = signing_material("fake-two")
    active_keys = [first_public]
    requests = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            200,
            json={"keys": active_keys},
            headers={"Cache-Control": "max-age=60"},
        )

    remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AuthCenterClient(auth_settings(), http=remote)
    try:
        await client.verify_user_token(user_token(first_private, "key-one"))
        with pytest.raises(jwt.InvalidTokenError, match="Unknown signing key"):
            await client.verify_user_token(user_token(fake_private, "fake-one"))
        with pytest.raises(jwt.InvalidTokenError, match="Unknown signing key"):
            await client.verify_user_token(user_token(other_fake_private, "fake-two"))
        assert requests == 2

        active_keys.append(second_public)
        client._keys_expire_at = 0
        claims = await client.verify_user_token(user_token(second_private, "key-two"))
    finally:
        await remote.aclose()

    assert claims["kid"] == "key-two"
    assert requests == 3


async def test_user_invalid_signature_for_known_kid_does_not_refresh_jwks() -> None:
    private_key, public_jwk = signing_material("key-one")
    attacker_key, _attacker_jwk = signing_material("attacker")
    requests = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"keys": [public_jwk]})

    remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AuthCenterClient(auth_settings(), http=remote)
    try:
        await client.verify_user_token(user_token(private_key, "key-one"))
        with pytest.raises(jwt.InvalidSignatureError):
            await client.verify_user_token(user_token(attacker_key, "key-one"))
    finally:
        await remote.aclose()

    assert requests == 1


async def test_user_unknown_kid_refresh_failure_starts_cooldown() -> None:
    private_key, public_jwk = signing_material("key-one")
    first_fake, _first_fake_jwk = signing_material("fake-one")
    second_fake, _second_fake_jwk = signing_material("fake-two")
    fail = False
    requests = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if fail:
            return httpx.Response(503, json={"detail": "unavailable"})
        return httpx.Response(200, json={"keys": [public_jwk]})

    remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AuthCenterClient(auth_settings(), http=remote)
    try:
        await client.verify_user_token(user_token(private_key, "key-one"))
        fail = True
        with pytest.raises(AuthCenterUnavailable):
            await client.verify_user_token(user_token(first_fake, "fake-one"))
        with pytest.raises(jwt.InvalidTokenError, match="Unknown signing key"):
            await client.verify_user_token(user_token(second_fake, "fake-two"))
    finally:
        await remote.aclose()

    assert requests == 2


async def test_auth_center_maps_5xx_to_unavailable_and_4xx_to_rejected() -> None:
    status = 503

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": "failure"})

    remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AuthCenterClient(auth_settings(), http=remote)
    try:
        with pytest.raises(AuthCenterUnavailable):
            await client.me("token")
        status = 401
        with pytest.raises(AuthCenterRejected) as exc_info:
            await client.me("token")
    finally:
        await remote.aclose()

    assert exc_info.value.status_code == 401


async def test_service_verifier_uses_jwks_and_rejects_user_tokens() -> None:
    private_key, public_jwk = signing_material("key-one")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/.well-known/jwks.json"
        return httpx.Response(200, json={"keys": [public_jwk]})

    remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = ServiceVerifier(
        None,
        "agent:local",
        "resources:read",
        http=remote,
        auth_center_url="http://auth-center:9100",
    )
    try:
        claims = await verifier._decode(service_token(private_key, "key-one"))
        assert claims["type"] == "service"
        with pytest.raises(jwt.InvalidTokenError):
            await verifier._decode(
                user_token(
                    private_key,
                    "key-one",
                    aud="agent:local",
                    scope="resources:read",
                )
            )
    finally:
        await remote.aclose()


async def test_service_jwks_cache_and_unknown_kid_refresh_are_bounded() -> None:
    first_private, first_public = signing_material("key-one")
    second_private, second_public = signing_material("key-two")
    fake_private, _fake_public = signing_material("fake-one")
    other_fake_private, _other_fake_public = signing_material("fake-two")
    active_keys = [first_public]
    cache_control = "max-age=4"
    requests = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            200,
            json={"keys": active_keys},
            headers={"Cache-Control": cache_control},
        )

    remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = ServiceVerifier(
        None,
        "agent:local",
        "resources:read",
        http=remote,
        auth_center_url="http://auth-center:9100",
        jwks_ttl_seconds=300,
    )
    try:
        await verifier._decode(service_token(first_private, "key-one"))
        cache_ttl = verifier.keys_expire_at - time.monotonic()
        with pytest.raises(jwt.InvalidTokenError, match="Unknown signing key"):
            await verifier._decode(service_token(fake_private, "fake-one"))
        with pytest.raises(jwt.InvalidTokenError, match="Unknown signing key"):
            await verifier._decode(service_token(other_fake_private, "fake-two"))
        assert requests == 2

        active_keys.append(second_public)
        cache_control = "max-age=600"
        verifier.keys_expire_at = 0
        claims = await verifier._decode(service_token(second_private, "key-two"))
        capped_ttl = verifier.keys_expire_at - time.monotonic()
    finally:
        await remote.aclose()

    assert 3.5 <= cache_ttl <= 4
    assert claims["kid"] == "key-two"
    assert 59.5 <= capped_ttl <= 60
    assert requests == 3


async def test_service_invalid_signature_for_known_kid_does_not_refresh_jwks() -> None:
    private_key, public_jwk = signing_material("key-one")
    attacker_key, _attacker_jwk = signing_material("attacker")
    requests = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"keys": [public_jwk]})

    remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = ServiceVerifier(
        None,
        "agent:local",
        "resources:read",
        http=remote,
        auth_center_url="http://auth-center:9100",
    )
    try:
        await verifier._decode(service_token(private_key, "key-one"))
        with pytest.raises(jwt.InvalidSignatureError):
            await verifier._decode(service_token(attacker_key, "key-one"))
    finally:
        await remote.aclose()

    assert requests == 1


async def test_service_unknown_kid_refresh_failure_starts_cooldown() -> None:
    private_key, public_jwk = signing_material("key-one")
    first_fake, _first_fake_jwk = signing_material("fake-one")
    second_fake, _second_fake_jwk = signing_material("fake-two")
    fail = False
    requests = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if fail:
            return httpx.Response(503, json={"detail": "unavailable"})
        return httpx.Response(200, json={"keys": [public_jwk]})

    remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = ServiceVerifier(
        None,
        "agent:local",
        "resources:read",
        http=remote,
        auth_center_url="http://auth-center:9100",
    )
    try:
        await verifier._decode(service_token(private_key, "key-one"))
        fail = True
        with pytest.raises(httpx.HTTPStatusError):
            await verifier._decode(service_token(first_fake, "fake-one"))
        with pytest.raises(jwt.InvalidTokenError, match="Unknown signing key"):
            await verifier._decode(service_token(second_fake, "fake-two"))
    finally:
        await remote.aclose()

    assert requests == 2


async def test_unknown_kid_negative_caches_are_bounded() -> None:
    remote = httpx.AsyncClient()
    client = AuthCenterClient(auth_settings(), http=remote)
    verifier = ServiceVerifier(None, "audience", "scope")
    expires_at = time.monotonic() + 60
    client._keys_expire_at = expires_at
    verifier.keys_expire_at = expires_at

    try:
        for index in range(300):
            client._remember_unknown_kid(f"user-{index}")
            verifier._remember_unknown_kid(f"service-{index}")
    finally:
        await remote.aclose()

    assert len(client._unknown_kids) == 256
    assert "user-0" not in client._unknown_kids
    assert "user-299" in client._unknown_kids
    assert len(verifier.unknown_kids) == 256
    assert "service-0" not in verifier.unknown_kids
    assert "service-299" in verifier.unknown_kids


@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"type": "service"},
        {"aud": "monitor"},
        {"scope": "resources:read"},
    ],
)
async def test_service_wrong_audience_and_wrong_scope_tokens_are_rejected(
    claim_overrides: dict,
) -> None:
    private_key, public_jwk = signing_material("key-one")

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [public_jwk]})

    remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AuthCenterClient(auth_settings(), http=remote)
    try:
        with pytest.raises(jwt.InvalidTokenError):
            await client.verify_user_token(
                user_token(private_key, "key-one", **claim_overrides)
            )
    finally:
        await remote.aclose()


class FakeVerifier:
    def __init__(self, claims: dict) -> None:
        self.claims = claims

    async def verify_user_token(self, _token: str) -> dict:
        return self.claims


class FakeDb:
    def __init__(self, *responses) -> None:
        self.responses = list(responses)

    async def scalar(self, _statement):
        return self.responses.pop(0)


async def test_current_admin_requires_shared_active_session_and_admin() -> None:
    claims = {
        "sub": "admin",
        "uid": 7,
        "sid": "12345678-1234-4234-9234-123456789abc",
        "jti": "abcdefab-1234-4234-9234-123456789abc",
    }
    session = SimpleNamespace(sid=claims["sid"])
    admin = Admin(id=7, username="admin", password_hash="unused", is_active=True)
    db = FakeDb(session, None, admin)

    result = await authenticate_admin(
        db,  # type: ignore[arg-type]
        "user-token",
        FakeVerifier(claims),  # type: ignore[arg-type]
    )

    assert result is admin


async def test_current_admin_rejects_a_revoked_jti() -> None:
    claims = {
        "sub": "admin",
        "uid": 7,
        "sid": "12345678-1234-4234-9234-123456789abc",
        "jti": "abcdefab-1234-4234-9234-123456789abc",
    }
    db = FakeDb(SimpleNamespace(sid=claims["sid"]), claims["jti"])

    with pytest.raises(APIError) as exc_info:
        await authenticate_admin(
            db,  # type: ignore[arg-type]
            "user-token",
            FakeVerifier(claims),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "invalid_token"
