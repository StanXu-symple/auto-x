import json

import httpx

from app.core.config import Settings
from app.main import app
from app.services.auth_center import AuthCenterClient


def auth_settings() -> Settings:
    return Settings(
        _env_file=None,
        service_auth_url="http://auth-center:9100",
        monitor_center_url="http://monitor-center:9102",
    )


async def test_login_is_proxied_without_backend_password_verification() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.content)
        seen["forwarded_for"] = request.headers.get("x-forwarded-for")
        return httpx.Response(
            200,
            json={
                "access_token": "auth-center-signed-token",
                "token_type": "bearer",
                "expires_in": 3600,
                "user": {"id": 7, "username": "admin", "is_active": True},
            },
        )

    remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    auth_center = AuthCenterClient(auth_settings(), http=remote)
    previous = getattr(app.state, "auth_center", None)
    app.state.auth_center = auth_center
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "remote-only-secret"},
                headers={"X-Forwarded-For": "198.51.100.7"},
            )
    finally:
        await remote.aclose()
        if previous is None:
            del app.state.auth_center
        else:
            app.state.auth_center = previous

    assert response.status_code == 200
    assert response.json()["access_token"] == "auth-center-signed-token"
    assert seen["path"] == "/v1/admin/login"
    assert seen["payload"] == {"username": "admin", "password": "remote-only-secret"}
    assert str(seen["forwarded_for"]).startswith("198.51.100.7")


async def test_login_preserves_auth_center_rate_limit_contract() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"detail": "Too many failed login attempts"},
            headers={"Retry-After": "57"},
        )

    remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    auth_center = AuthCenterClient(auth_settings(), http=remote)
    previous = getattr(app.state, "auth_center", None)
    app.state.auth_center = auth_center
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "wrong-password"},
            )
    finally:
        await remote.aclose()
        if previous is None:
            del app.state.auth_center
        else:
            app.state.auth_center = previous

    assert response.status_code == 429
    assert response.headers["retry-after"] == "57"
    assert response.json()["error"] == {
        "code": "invalid_credentials",
        "message": "Too many failed login attempts",
        "details": None,
        "request_id": response.headers["x-request-id"],
    }
