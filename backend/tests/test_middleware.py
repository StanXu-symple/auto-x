import httpx
from fastapi import FastAPI

from app.main import request_context_and_metrics
from app.services.metrics import HTTP_REQUESTS


async def test_middleware_records_500_and_attaches_request_id() -> None:
    test_app = FastAPI()
    test_app.middleware("http")(request_context_and_metrics)

    @test_app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    transport = httpx.ASGITransport(app=test_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom", headers={"X-Request-ID": "known-request"})

    assert response.status_code == 500
    assert response.headers["x-request-id"] == "known-request"
    assert response.json()["error"]["code"] == "internal_error"


async def test_unmatched_routes_use_bounded_prometheus_label() -> None:
    test_app = FastAPI()
    test_app.middleware("http")(request_context_and_metrics)
    metric = HTTP_REQUESTS.labels(method="GET", path="__unmatched__", status="404")
    before = metric._value.get()

    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/random-user-controlled-path-123")

    assert response.status_code == 404
    assert metric._value.get() == before + 1
