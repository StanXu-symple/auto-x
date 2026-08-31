import httpx
import pytest

from app.main import app


@pytest.mark.asyncio
async def test_liveness_and_openapi_contract() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/api/v1/health/live")
        openapi = await client.get("/openapi.json")

    assert health.status_code == 200
    assert health.json()["service"] == "X Sentinel"
    paths = openapi.json()["paths"]
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/dashboard/summary" in paths
    assert "/api/v1/monitored-users/{user_id}/poll" in paths
    assert "/api/v1/system/metrics" in paths
    assert "/api/v1/ai/settings" in paths
    assert "/api/v1/ai/skills" in paths
    assert "/api/v1/ai/jobs" in paths
    assert "/api/v1/tweets/{tweet_id}/generate" in paths

    # Compatibility aliases intentionally stay out of OpenAPI but remain routable.
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        alias = await client.get("/api/v1/accounts")
    assert alias.status_code == 401


@pytest.mark.asyncio
async def test_protected_endpoint_requires_bearer_token() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/monitored-users")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"
    assert response.headers["x-request-id"]
