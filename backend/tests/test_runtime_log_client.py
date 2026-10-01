import asyncio
import json
import time
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app.control_plane.nacos import Instance
from app.control_plane.security import ISSUER, ServiceVerifier
from app.core.config import Settings
from app.services import browser_job_api, runtime_log_client, runtime_logs


def config(tmp_path):
    secret = tmp_path / "runtime-logs.secret"
    secret.write_text("a" * 64)
    return Settings(
        _env_file=None,
        nacos_config_enabled=False,
        nacos_server_addr="http://nacos:8848",
        xhs_transport="http",
        runtime_logs_client_secret_file=str(secret),
    )


def mock_remote(monkeypatch, handler):
    discover = AsyncMock(return_value=Instance("192.0.2.7", 8007, "browser"))
    token = AsyncMock(return_value="read-only-token")
    monkeypatch.setattr(runtime_log_client.NacosClient, "discover", discover)
    monkeypatch.setattr(runtime_log_client.TokenClient, "token", token)
    constructor = httpx.AsyncClient
    monkeypatch.setattr(
        runtime_log_client.httpx,
        "AsyncClient",
        lambda **kwargs: constructor(
            **kwargs,
            transport=httpx.MockTransport(handler),
        ),
    )
    return discover, token


@pytest.mark.parametrize("system", ["xhs-worker", "camoufox-worker"])
async def test_remote_tail_and_live_logs_ignore_stale_local_file(tmp_path, monkeypatch, system):
    monkeypatch.setattr(runtime_logs, "LOG_DIR", tmp_path)
    (tmp_path / f"{system}.log").write_text("stale local log\n")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
            text=runtime_logs.sse_event("ready", {"system": system, "lines": ["远程历史日志"]})
            + ": keepalive\n\n"
            + runtime_logs.sse_event("log", {"line": "实时追加"}),
        )

    discover, token = mock_remote(monkeypatch, handler)
    events = [
        event async for event in runtime_log_client.system_logs(config(tmp_path), system, tail=7)
    ]
    assert len(events) == 3
    assert "远程历史日志" in events[0]
    assert "stale local" not in "".join(events)
    assert "实时追加" in events[2]
    discover.assert_awaited_once_with("xsentinel-" + system)
    token.assert_awaited_once_with(system)
    assert requests[0].url.path == "/v1/logs/stream"
    assert requests[0].url.params["tail"] == "7"
    assert requests[0].headers["authorization"] == "Bearer read-only-token"


@pytest.mark.parametrize(
    "status,body",
    [
        (401, "secret"),
        (503, "private-error"),
        (200, 'event: ready\ndata: {"system":"wrong","lines":[]}\n\n'),
        (200, "event: ready\ndata: invalid-json\n\n"),
        (200, ""),
    ],
)
async def test_remote_failure_is_explicit_and_does_not_expose_body(
    tmp_path, monkeypatch, status, body
):
    mock_remote(
        monkeypatch,
        lambda r: httpx.Response(status, text=body, headers={"content-type": "text/event-stream"}),
    )
    with pytest.raises(runtime_log_client.RuntimeLogUnavailableError) as error:
        _ = [
            event
            async for event in runtime_log_client.system_logs(config(tmp_path), "camoufox-worker")
        ]
    assert "日志读取失败" in str(error.value)
    assert "secret" not in str(error.value)
    assert "private-error" not in str(error.value)


async def test_cancelled_proxy_closes_remote_response(tmp_path, monkeypatch):
    closed = asyncio.Event()

    class Live(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield runtime_logs.sse_event(
                "ready", {"system": "camoufox-worker", "lines": []}
            ).encode()
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    mock_remote(
        monkeypatch,
        lambda r: httpx.Response(200, stream=Live(), headers={"content-type": "text/event-stream"}),
    )
    source = runtime_log_client.system_logs(config(tmp_path), "camoufox-worker")
    await anext(source)
    pending = asyncio.create_task(anext(source))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await source.aclose()
    assert closed.is_set()


async def test_core_service_still_reads_local_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_logs, "LOG_DIR", tmp_path)
    (tmp_path / "worker.log").write_text("local worker\n")
    source = runtime_log_client.system_logs(config(tmp_path), "worker")
    assert "local worker" in await anext(source)
    await source.aclose()


@pytest.mark.parametrize(
    "browser_service,system", [(False, "xhs-worker"), (True, "camoufox-worker")]
)
async def test_worker_log_api_enforces_read_scope_and_own_file(
    tmp_path, monkeypatch, browser_service, system
):
    monkeypatch.setattr(runtime_logs, "LOG_DIR", tmp_path)
    (tmp_path / f"{system}.log").write_text("worker log\n")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    app = browser_job_api.create_app(config(tmp_path), browser_service=browser_service)
    app.state.log_verifier = ServiceVerifier(key.public_key(), system, "logs:read")
    app.state.verifier = ServiceVerifier(
        key.public_key(), system, "browser:execute" if browser_service else "xhs:execute"
    )

    def token(scope="logs:read", audience=system):
        now = int(time.time())
        return jwt.encode(
            {
                "iss": ISSUER,
                "sub": "runtime-logs",
                "aud": audience,
                "scope": scope,
                "type": "service",
                "kid": "test",
                "jti": "id",
                "iat": now,
                "nbf": now,
                "exp": now + 60,
            },
            key,
            algorithm="RS256",
            headers={"kid": "test"},
        )

    async def finite(system, *, tail):
        yield runtime_logs.sse_event(
            "ready", {"system": system, "lines": ["worker log"][-tail:] if tail else []}
        )

    monkeypatch.setattr(browser_job_api, "stream_log", finite)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://worker"
    ) as http:
        for value in (None, token(scope="browser:execute"), token(audience="other-worker")):
            headers = {"Authorization": f"Bearer {value}"} if value else {}
            assert (await http.get("/v1/logs/stream", headers=headers)).status_code == 401
        headers = {"Authorization": f"Bearer {token()}"}
        assert (await http.post("/v1/jobs", headers=headers, content=b"invalid")).status_code == 401
        response = await http.get("/v1/logs/stream?tail=200", headers=headers)
        assert response.status_code == 200
        assert '"system": "' + system + '"' in response.text
        assert "worker log" in response.text
        for query in ("tail=-1", "tail=1001"):
            assert (await http.get("/v1/logs/stream?" + query, headers=headers)).status_code == 422
        (tmp_path / f"{system}.log").unlink()
        assert (await http.get("/v1/logs/stream", headers=headers)).status_code == 503


async def test_public_stream_checks_remote_before_returning_200(monkeypatch):
    from app.api.routes import system

    async def failed(*args, **kwargs):
        raise runtime_log_client.RuntimeLogUnavailableError("日志读取失败")
        yield  # pragma: no cover

    monkeypatch.setattr(system, "system_logs", failed)
    monkeypatch.setattr(system, "get_settings", lambda: None)
    with pytest.raises(HTTPException) as error:
        await system.system_log_stream(None, "camoufox-worker", 200)
    assert error.value.status_code == 503


async def test_public_stream_reports_failure_after_initial_frame(monkeypatch):
    from app.api.routes import system

    async def interrupted(*args, **kwargs):
        yield runtime_logs.sse_event("ready", {"system": "camoufox-worker", "lines": ["old"]})
        raise runtime_log_client.RuntimeLogUnavailableError("日志连接中断")

    monkeypatch.setattr(system, "system_logs", interrupted)
    monkeypatch.setattr(system, "get_settings", lambda: None)
    response = await system.system_log_stream(None, "camoufox-worker", 200)
    events = [event async for event in response.body_iterator]
    assert events[1].startswith("event: error\n")
    assert json.loads(events[1].splitlines()[1][6:])["message"] == "日志连接中断"
