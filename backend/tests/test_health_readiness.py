from types import SimpleNamespace

from starlette.requests import Request
from starlette.responses import Response

from app.api.routes import health


class BrokenSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement):
        raise RuntimeError("mysql://secret-user:secret-password@private-host/database")


class BrokenRedis:
    async def ping(self):
        raise RuntimeError("redis://:secret@private-cache/0")


async def test_public_readiness_does_not_leak_connection_errors(monkeypatch) -> None:
    monkeypatch.setattr(health, "AsyncSessionFactory", lambda: BrokenSession())
    app = SimpleNamespace(state=SimpleNamespace(redis=BrokenRedis()))
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/health/ready",
            "headers": [],
            "app": app,
        }
    )
    response = Response()
    result = await health.readiness(request, response)
    payload = result.model_dump()
    assert response.status_code == 503
    assert payload["checks"] == {
        "database": {"status": "unhealthy"},
        "redis": {"status": "unhealthy"},
    }
    assert "secret" not in str(payload)
