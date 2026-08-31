import httpx
from starlette.requests import Request

from app.api.errors import APIError
from app.api.routes.auth import (
    _check_login_limit,
    _clear_login_limit,
    _login_rate_key,
    _record_login_failure,
    settings,
)
from app.db.session import get_db
from app.main import app


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def ttl(self, key: str):
        return self.ttls.get(key, -2)

    async def eval(self, _script: str, _keys: int, key: str, window: int):
        self.values[key] = self.values.get(key, 0) + 1
        self.ttls.setdefault(key, int(window))
        return [self.values[key], self.ttls[key]]

    async def delete(self, key: str):
        self.values.pop(key, None)
        self.ttls.pop(key, None)


def make_request(ip: str = "203.0.113.9") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [],
            "client": (ip, 12345),
            "server": ("test", 80),
            "scheme": "http",
        }
    )


async def test_login_failures_are_stably_rate_limited_and_success_can_clear() -> None:
    redis = FakeRedis()
    key = _login_rate_key(make_request(), "Admin")
    for _ in range(settings.login_rate_limit_attempts - 1):
        await _record_login_failure(redis, key)  # type: ignore[arg-type]

    try:
        await _record_login_failure(redis, key)  # type: ignore[arg-type]
    except APIError as exc:
        assert exc.status_code == 429
        assert exc.code == "login_rate_limited"
        assert exc.headers == {"Retry-After": str(settings.login_rate_limit_window_seconds)}
    else:
        raise AssertionError("the configured failure threshold must return 429")

    try:
        await _check_login_limit(redis, key)  # type: ignore[arg-type]
    except APIError as exc:
        assert exc.status_code == 429
        assert exc.headers and int(exc.headers["Retry-After"]) > 0
    else:
        raise AssertionError("subsequent attempts must stay rate limited")

    await _clear_login_limit(redis, key)  # type: ignore[arg-type]
    await _check_login_limit(redis, key)  # type: ignore[arg-type]


def test_login_rate_limit_key_is_scoped_by_ip_and_normalized_username() -> None:
    request = make_request()
    assert _login_rate_key(request, " Admin ") == _login_rate_key(request, "admin")
    assert _login_rate_key(request, "admin") != _login_rate_key(
        make_request("203.0.113.10"), "admin"
    )


async def test_login_endpoint_returns_stable_429_shape() -> None:
    redis = FakeRedis()

    class FakeDb:
        async def scalar(self, _statement):
            return None

    async def override_db():
        yield FakeDb()

    previous_redis = getattr(app.state, "redis", None)
    app.state.redis = redis
    app.dependency_overrides[get_db] = override_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            responses = [
                await client.post(
                    "/api/v1/auth/login",
                    json={"username": "admin", "password": "wrong-password"},
                )
                for _ in range(settings.login_rate_limit_attempts)
            ]
    finally:
        app.dependency_overrides.pop(get_db, None)
        if previous_redis is None:
            del app.state.redis
        else:
            app.state.redis = previous_redis

    assert all(response.status_code == 401 for response in responses[:-1])
    limited = responses[-1]
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "login_rate_limited"
    assert int(limited.headers["retry-after"]) > 0
