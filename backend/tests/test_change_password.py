import json

import httpx

from app.core.config import Settings
from app.services.auth_center import AuthCenterClient


def auth_settings() -> Settings:
    return Settings(
        _env_file=None,
        service_auth_url="http://auth-center:9100",
        monitor_center_url="http://monitor-center:9102",
    )


async def test_password_change_is_delegated_to_auth_center() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["authorization"] = request.headers.get("authorization")
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"message": "Password updated successfully"})

    remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AuthCenterClient(auth_settings(), http=remote)
    try:
        response = await client.change_password(
            "user-token",
            {
                "current_password": "existing-password",
                "new_password": "new-secure-password",
            },
        )
    finally:
        await remote.aclose()

    assert response == {"message": "Password updated successfully"}
    assert seen == {
        "path": "/v1/admin/password",
        "authorization": "Bearer user-token",
        "payload": {
            "current_password": "existing-password",
            "new_password": "new-secure-password",
        },
    }


async def test_logout_revokes_the_remote_session() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"message": "Logged out successfully"})

    remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AuthCenterClient(auth_settings(), http=remote)
    try:
        response = await client.logout("user-token")
    finally:
        await remote.aclose()

    assert response == {"message": "Logged out successfully"}
    assert seen == {
        "method": "POST",
        "path": "/v1/admin/logout",
        "authorization": "Bearer user-token",
    }
