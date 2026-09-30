"""Exercise HTTP/auth/file-transfer boundaries without publishing to Xiaohongshu."""

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.camoufox_service import create_app
from app.control_plane.nacos import Instance, NacosClient
from app.control_plane.security import ISSUER, ServiceVerifier
from app.core.config import Settings
from app.schemas.camoufox import BrowserJobRequest
from app.services.browser_job_api import XHSRuntime
from app.services.camoufox_client import CamoufoxServiceClient
from app.services.xhs_jobs import XHSWorkerUnavailableError


class MemoryRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script, count, key, expected, *args):
        if self.values.get(key) == expected:
            self.values.pop(key, None)
            return 1
        return 0

    async def ping(self):
        return True

    async def aclose(self):
        pass


def settings(tmp_path):
    secret = tmp_path / "caller.secret"
    secret.write_text("a" * 64)
    return Settings(
        _env_file=None,
        nacos_config_enabled=False,
        service_client_secret_file=str(secret),
        camoufox_service_advertise_ip="192.0.2.7",
        camoufox_service_advertise_port=18007,
    )


def auth():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = ServiceVerifier(key.public_key(), "camoufox-worker", "browser:execute")

    def token(audience="camoufox-worker", scope="browser:execute"):
        now = int(time.time())
        return jwt.encode(
            {
                "iss": ISSUER,
                "sub": "xhs-worker",
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

    return verifier, token


def post_job(job_id="a" * 32):
    return {
        "job_id": job_id,
        "admin_id": 7,
        "payload": {
            "operation": "post",
            "title": "title",
            "content": "body",
            "image_count": 1,
            "encrypted_a1": "cipher-a1",
            "encrypted_web_session": "cipher-session",
            "cookie_version": 3,
        },
    }


async def test_browser_api_auth_upload_idempotency_and_result(tmp_path, monkeypatch):
    config = settings(tmp_path)
    monkeypatch.setenv("XHS_UPLOAD_DIR", str(tmp_path))
    gate = asyncio.Event()
    calls = []

    async def execute(job):
        calls.append(job)
        assert (
            await asyncio.to_thread(Path(job["payload"]["images"][0]).read_bytes) == b"png-content"
        )
        await gate.wait()
        return {"result": {"success": True}}

    worker = SimpleNamespace(
        redis=MemoryRedis(),
        active_tasks=0,
        _execute_job=execute,
        worker_id="test",
        browser_status=AsyncMock(return_value={"installed": True}),
    )
    app = create_app(config)
    app.state.runtime = XHSRuntime(worker, config, "192.0.2.7", "camoufox")
    app.state.upload_root = tmp_path
    verifier, token = auth()
    app.state.verifier = verifier
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://browser"
    ) as client:
        for invalid in (None, token("xhs-worker"), token(scope="xhs:execute")):
            headers = {"Authorization": f"Bearer {invalid}"} if invalid else {}
            response = await client.post("/v1/jobs", headers=headers, content=b"not-multipart")
            assert response.status_code == 401
        headers = {"Authorization": f"Bearer {token()}"}

        async def submit(job):
            return await client.post(
                "/v1/jobs",
                headers=headers,
                data={"job": json.dumps(job)},
                files={"images": ("a.png", b"png-content", "image/png")},
            )

        first = await submit(post_job())
        assert first.status_code == 202
        again = await submit(post_job())
        assert again.json()["state"] == "running"
        conflict = post_job()
        conflict["payload"]["title"] = "different"
        assert (await submit(conflict)).status_code == 409
        assert (await submit(post_job("b" * 32))).status_code == 429
        assert len(calls) == 1
        assert worker.redis.values.get("xsentinel:camoufox:http:active:7")
        gate.set()
        await asyncio.gather(*list(app.state.runtime.tasks))
        result = await client.get("/v1/jobs/" + "a" * 32, headers=headers)
        assert result.json()["state"] == "succeeded"
        assert len(calls) == 1
        assert not list(tmp_path.glob(".rpc-*"))
        assert "xsentinel:camoufox:http:active:7" not in worker.redis.values
        # Missing credentials / extra operations cannot reach the SDK.
        invalid = post_job("c" * 32)
        del invalid["payload"]["encrypted_a1"]
        assert (await submit(invalid)).status_code == 422
        invalid["payload"]["operation"] = "evaluate"
        assert (await submit(invalid)).status_code == 422


async def test_camoufox_lifespan_registers_own_identity_and_cleans_up(tmp_path, monkeypatch):
    config = settings(tmp_path)
    import app.xhs_worker as xhs

    monkeypatch.setattr(xhs, "UPLOAD_DIR", tmp_path)
    register = AsyncMock()
    deregister = AsyncMock()
    monkeypatch.setattr(NacosClient, "register", register)
    monkeypatch.setattr(NacosClient, "deregister", deregister)
    monkeypatch.setattr(NacosClient, "beat", AsyncMock())
    worker = SimpleNamespace(
        _wait_for_dependencies=AsyncMock(),
        _heartbeat_loop=AsyncMock(),
        stop_event=asyncio.Event(),
        close=AsyncMock(),
        redis=SimpleNamespace(aclose=AsyncMock()),
    )
    app = create_app(config, worker_factory=lambda s: worker)
    async with app.router.lifespan_context(app):
        assert app.state.runtime.namespace == "camoufox"
        assert app.state.verifier.audience == "camoufox-worker"
        assert app.state.verifier.scope == "browser:execute"
        assert app.state.runtime.settings.xhs_job_timeout_seconds == 290
        assert register.call_args.args[:3] == ("xsentinel-camoufox-worker", "192.0.2.7", 18007)
    worker.close.assert_awaited_once()
    worker.redis.aclose.assert_awaited_once()
    deregister.assert_awaited_once()


async def test_client_uses_xhs_identity_and_sends_image_bytes_without_retry(tmp_path, monkeypatch):
    config = settings(tmp_path)
    monkeypatch.setenv("XHS_UPLOAD_DIR", str(tmp_path))
    picture = tmp_path / "picture.png"
    picture.write_bytes(b"png-content")
    client = CamoufoxServiceClient(config)
    assert client.tokens.client_id == "xhs-worker"
    assert client.request_model is BrowserJobRequest
    client.nacos.discover_all = AsyncMock(return_value=[Instance("192.0.2.7", 8007, "browser")])
    client.tokens.token = AsyncMock(return_value="auth-token")
    requests = []

    async def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer auth-token"
        assert b"png-content" in await request.aread()
        assert str(picture).encode() not in request.content
        return httpx.Response(
            202, json={"job_id": "a" * 32, "state": "succeeded", "data": {"done": True}}
        )

    await client.http.aclose()
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    payload = {**post_job()["payload"], "images": [str(picture)]}
    payload.pop("operation")
    payload.pop("image_count")
    result = await client.submit(
        operation="post", admin_id=7, payload=payload, timeout_seconds=10, job_id="a" * 32
    )
    assert result == {"done": True}
    client.nacos.discover_all.assert_awaited_once_with("xsentinel-camoufox-worker")
    client.tokens.token.assert_awaited_once_with("camoufox-worker")
    assert len(requests) == 1
    await client.http.aclose()

    def unavailable(request):
        requests.append(request)
        raise httpx.ConnectError("unavailable")

    client.http = httpx.AsyncClient(transport=httpx.MockTransport(unavailable))
    with pytest.raises(XHSWorkerUnavailableError):
        await client.submit(
            operation="post", admin_id=7, payload=payload, timeout_seconds=10, job_id="b" * 32
        )
    assert len(requests) == 2
    await client.aclose()


async def test_verification_is_mirrored_and_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_UPLOAD_DIR", str(tmp_path))
    client = CamoufoxServiceClient(settings(tmp_path))
    client.tokens.token = AsyncMock(return_value="token")
    import base64

    png = b"\x89PNG\r\n\x1a\nimage"
    results = [
        {
            "required": True,
            "version": "1",
            "image": "data:image/png;base64," + base64.b64encode(png).decode(),
        },
        {"required": False},
    ]
    await client.http.aclose()
    client.http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=results.pop(0)))
    )
    target = Instance("browser", 8007, "browser")
    await client.poll_verification(target, 7)
    assert (tmp_path / ".verification/7.png").read_bytes() == png
    await client.poll_verification(target, 7)
    assert not (tmp_path / ".verification/7.png").exists()
    await client.aclose()


async def test_xhs_gateway_delegates_login_without_local_browser(tmp_path):
    from app.xhs_worker import XiaohongshuWorker

    worker = object.__new__(XiaohongshuWorker)
    worker.settings = settings(tmp_path)
    worker.browser_client = SimpleNamespace(submit=AsyncMock(return_value={"ok": True}))
    result = await worker._execute_job(
        {
            "job_id": "a" * 32,
            "operation": "login",
            "admin_id": 7,
            "payload": {"encrypted_a1": "cipher", "encrypted_web_session": "cipher"},
        }
    )
    assert result == {"ok": True}
    assert worker.browser_client.submit.await_args.kwargs["job_id"] == "a" * 32
    assert not hasattr(worker, "browser_pool")
    assert not hasattr(worker, "_run_cli")
