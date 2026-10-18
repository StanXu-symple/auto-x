"""Durable completion tests: browser actions are all fakes, never real posts."""

import asyncio
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.control_plane.nacos import Instance
from app.core.config import Settings
from app.models.browser_job import BrowserJobResult
from app.schemas.xhs_service import XHSJobRequest, XHSJobState
from app.services.browser_job_api import XHSRuntime
from app.services.browser_job_store import BrowserJobStore
from app.services.xhs_client import XHSServiceClient
from app.services.xhs_completion_delivery import XHSCompletionDelivery
from app.services.xhs_jobs import (
    XHSJobFailedError,
    XHSJobNotAcceptedError,
    XHSJobReconciliationError,
    XHSJobTimeoutError,
    XHSWorkerUnavailableError,
    safe_job_error,
)


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


@pytest.fixture
async def store():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(BrowserJobResult.__table__.create)
    result = BrowserJobStore(async_sessionmaker(engine, expire_on_commit=False))
    yield result
    await engine.dispose()


def config(tmp_path):
    secret = tmp_path / "caller.secret"
    secret.write_text("a" * 64)
    return Settings(
        _env_file=None, nacos_config_enabled=False,
        service_client_secret_file=str(secret),
    )


def job(job_id="a" * 32):
    return XHSJobRequest(
        job_id=job_id, admin_id=7,
        payload={"operation": "post", "title": "title", "content": "body", "image_count": 1},
    )


def test_persisted_error_redacts_tokens_and_url_query():
    detail = safe_job_error(
        "HTTP 403 a1=secret-one web_session='secret two' Authorization: Bearer raw-token "
        "https://name:password@example.com/path?access_token=token#fragment"
    )
    for value in ("secret-one", "secret two", "raw-token", "password", "access_token=token"):
        assert value not in detail
    assert "HTTP 403" in detail and "https://example.com/path" in detail


def runtime(tmp_path, store, *, namespace="xhs", execute=None):
    worker = SimpleNamespace(
        redis=MemoryRedis(), active_tasks=0, worker_id="worker-1",
        _execute_job=execute or AsyncMock(return_value={"result": {"success": True}}),
        browser_client=SimpleNamespace(job_status=AsyncMock()),
    )
    return XHSRuntime(worker, config(tmp_path), "192.0.2.1", namespace, store=store)


async def test_lost_http_response_recovers_late_success_without_resubmitting(tmp_path, store):
    rt = runtime(tmp_path, store, execute=AsyncMock(side_effect=XHSJobTimeoutError("timeout")))
    directory = tempfile.TemporaryDirectory(dir=tmp_path)
    image = Path(directory.name) / "image.png"
    image.write_bytes(b"image")
    await rt.submit(job(), directory, [str(image)])
    await asyncio.gather(*list(rt.tasks))
    assert (await store.get("xhs", "a" * 32)).state == "reconciling"
    rt.worker.browser_client.job_status.return_value = XHSJobState(
        job_id="a" * 32, state="succeeded", data={"result": {"note_id": "note-1"}}
    )
    await rt.recover_once()
    assert (await store.get("xhs", "a" * 32)).state == "succeeded"
    rt.worker._execute_job.assert_awaited_once()
    rt.worker.browser_client.job_status.assert_awaited_once_with(job_id="a" * 32, admin_id=7)
    rt.worker.redis.values.clear()
    assert (await rt.job_state("a" * 32)).data["result"]["note_id"] == "note-1"


@pytest.mark.parametrize("namespace", ["xhs", "camoufox"])
async def test_known_failure_is_durable_not_uncertain(tmp_path, store, namespace):
    rt = runtime(
        tmp_path, store, namespace=namespace,
        execute=AsyncMock(side_effect=XHSJobFailedError("图片未上传完成，尚未点击发布")),
    )
    directory = tempfile.TemporaryDirectory(dir=tmp_path)
    image = Path(directory.name) / "image.png"
    image.write_bytes(b"image")
    await rt.submit(job(), directory, [str(image)])
    await asyncio.gather(*list(rt.tasks))
    state = await rt.job_state("a" * 32)
    assert state.state == "failed"
    assert state.error == "图片未上传完成，尚未点击发布"


async def test_durable_deduplication_survives_redis_expiry(tmp_path, store):
    rt = runtime(tmp_path, store)
    for _ in range(2):
        with tempfile.TemporaryDirectory(dir=tmp_path) as name:
            # Keep TemporaryDirectory ownership until the runtime cleans it.
            directory = tempfile.TemporaryDirectory(dir=name)
            image = Path(directory.name) / "image.png"
            image.write_bytes(b"same")
            await rt.submit(job(), directory, [str(image)])
            await asyncio.gather(*list(rt.tasks))
        rt.worker.redis.values.clear()
    rt.worker._execute_job.assert_awaited_once()
    assert (await rt.job_state("a" * 32)).state == "succeeded"


async def test_restarted_browser_marks_expired_job_uncertain_without_publish(tmp_path, store):
    await store.accept("camoufox", "a" * 32, 7, "digest", worker_id="dead-worker")
    rt = runtime(tmp_path, store, namespace="camoufox")
    await rt.recover_once()
    assert (await store.get("camoufox", "a" * 32)).state == "uncertain"
    rt.worker._execute_job.assert_not_awaited()


async def test_xhs_recovers_persisted_success_when_browser_instance_is_down(tmp_path, store):
    await store.accept("xhs", "a" * 32, 7, "xhs-digest")
    await store.accept("camoufox", "a" * 32, 7, "browser-digest")
    await store.finish("camoufox", "a" * 32, "succeeded", data={"note_id": "saved"})
    rt = runtime(tmp_path, store)
    await rt.recover_once()
    assert (await store.get("xhs", "a" * 32)).data == {"note_id": "saved"}
    rt.worker.browser_client.job_status.assert_not_awaited()


async def test_completion_delivery_retries_after_backend_outage(tmp_path, store):
    await store.accept("xhs", "a" * 32, 7, "digest")
    await store.finish("xhs", "a" * 32, "succeeded", data={"result": {"note_id": "note"}})
    delivery = XHSCompletionDelivery(config(tmp_path), store)
    delivery.nacos.discover = AsyncMock(return_value=Instance("192.0.2.2", 8200, "backend"))
    delivery.tokens.token = AsyncMock(return_value="auth-token")
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer auth-token"
        assert request.url.path == "/api/v1/internal/xhs/jobs/" + "a" * 32 + "/completion"
        assert request.content == ('{"job_id":"' + "a" * 32 + '"}').encode()
        return httpx.Response(503 if len(calls) == 1 else 200, json={"ok": True})

    await delivery.http.aclose()
    delivery.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    await delivery.deliver_once()
    row = await store.get("xhs", "a" * 32)
    assert row.state == "succeeded" and row.callback_delivered_at is None
    async with store.factory() as session, session.begin():
        row = await session.get(BrowserJobResult, ("xhs", "a" * 32))
        row.next_callback_at = datetime.now(UTC) - timedelta(seconds=1)
    await delivery.deliver_once()
    assert (await store.get("xhs", "a" * 32)).callback_delivered_at is not None
    await delivery.deliver_once()
    assert len(calls) == 2
    await delivery.close()


async def test_client_accept_only_does_not_poll_and_query_is_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_UPLOAD_DIR", str(tmp_path))
    picture = tmp_path / "picture.png"
    picture.write_bytes(b"image")
    client = XHSServiceClient(config(tmp_path))
    client.nacos.discover_all = AsyncMock(return_value=[Instance("192.0.2.7", 8006, "xhs")])
    client.tokens.token = AsyncMock(return_value="auth-token")
    calls = []

    def respond(request):
        calls.append(request.method)
        return httpx.Response(202 if request.method == "POST" else 200, json={
            "job_id": "a" * 32, "state": "running" if request.method == "POST" else "succeeded"
        })

    await client.http.aclose()
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    accepted = await client.submit(
        operation="post", admin_id=7, job_id="a" * 32, timeout_seconds=10,
        payload={"title": "title", "content": "body", "images": [str(picture)]},
        wait_for_completion=False,
    )
    assert accepted["state"] == "running" and calls == ["POST"]
    assert (await client.job_status(job_id="a" * 32, admin_id=7)).state == "succeeded"
    assert calls == ["POST", "GET"]
    await client.aclose()


async def test_legacy_worker_failure_is_not_assumed_safe_to_republish(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_UPLOAD_DIR", str(tmp_path))
    picture = tmp_path / "picture.png"
    picture.write_bytes(b"image")
    client = XHSServiceClient(config(tmp_path))
    client.nacos.discover_all = AsyncMock(return_value=[Instance("192.0.2.7", 8006, "xhs")])
    client.tokens.token = AsyncMock(return_value="auth-token")
    await client.http.aclose()
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
        202, json={"job_id": "a" * 32, "state": "failed", "error": "browser interrupted"}
    )))
    with pytest.raises(XHSJobReconciliationError):
        await client.submit(
            operation="post", admin_id=7, job_id="a" * 32, timeout_seconds=10,
            payload={"title": "title", "content": "body", "images": [str(picture)]},
        )
    await client.aclose()


@pytest.mark.parametrize("stage", [
    "discovery", "authentication", "file_validation", "connect", "connect_timeout"
])
async def test_failure_before_remote_acceptance_can_be_retried(tmp_path, monkeypatch, stage):
    monkeypatch.setenv("XHS_UPLOAD_DIR", str(tmp_path))
    picture = tmp_path / "picture.png"
    picture.write_bytes(b"image")
    client = XHSServiceClient(config(tmp_path))
    client.nacos.discover_all = AsyncMock(return_value=[Instance("192.0.2.7", 8006, "xhs")])
    client.tokens.token = AsyncMock(return_value="token")
    post = AsyncMock(side_effect=httpx.ConnectError("connection unavailable"))
    client.http.post = post
    if stage == "discovery":
        client.nacos.discover_all.side_effect = RuntimeError("no healthy worker")
    elif stage == "authentication":
        client.tokens.token.side_effect = RuntimeError("auth unavailable")
    elif stage == "file_validation":
        picture.unlink()
    elif stage == "connect_timeout":
        post.side_effect = httpx.ConnectTimeout("connection timed out")
    with pytest.raises(XHSJobNotAcceptedError):
        await client.submit(
            operation="post", admin_id=7, job_id="a" * 32, timeout_seconds=10,
            payload={"title": "title", "content": "body", "images": [str(picture)]},
        )
    assert post.await_count == (1 if stage in {"connect", "connect_timeout"} else 0)
    await client.aclose()


@pytest.mark.parametrize("status", [401, 403, 413, 422])
async def test_explicit_pre_acceptance_http_rejection_is_safe(tmp_path, monkeypatch, status):
    monkeypatch.setenv("XHS_UPLOAD_DIR", str(tmp_path))
    picture = tmp_path / "picture.png"
    picture.write_bytes(b"image")
    client = XHSServiceClient(config(tmp_path))
    client.nacos.discover_all = AsyncMock(return_value=[Instance("192.0.2.7", 8006, "xhs")])
    client.tokens.token = AsyncMock(return_value="token")
    await client.http.aclose()
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(status, json={"detail": "rejected"})
    ))
    with pytest.raises(XHSJobNotAcceptedError):
        await client.submit(
            operation="post", admin_id=7, job_id="a" * 32, timeout_seconds=10,
            payload={"title": "title", "content": "body", "images": [str(picture)]},
        )
    await client.aclose()


@pytest.mark.parametrize("failure", ["response_lost", "internal_error", "poll_connect"])
async def test_failure_after_post_may_have_been_accepted_is_not_retryable(
    tmp_path, monkeypatch, failure
):
    monkeypatch.setenv("XHS_UPLOAD_DIR", str(tmp_path))
    picture = tmp_path / "picture.png"
    picture.write_bytes(b"image")
    client = XHSServiceClient(config(tmp_path))
    client.nacos.discover_all = AsyncMock(return_value=[Instance("192.0.2.7", 8006, "xhs")])
    client.tokens.token = AsyncMock(return_value="token")

    def respond(request):
        if failure == "response_lost":
            raise httpx.ReadTimeout("response lost")
        if failure == "internal_error":
            return httpx.Response(500)
        if request.method == "POST":
            return httpx.Response(202, json={"job_id": "a" * 32, "state": "running"})
        raise httpx.ConnectError("poll could not connect")

    await client.http.aclose()
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    with pytest.raises((XHSJobTimeoutError, XHSWorkerUnavailableError)):
        await client.submit(
            operation="post", admin_id=7, job_id="a" * 32, timeout_seconds=10,
            payload={"title": "title", "content": "body", "images": [str(picture)]},
        )
    await client.aclose()


async def test_lifespan_stops_callback_and_recovery_loops_before_closing_clients(
    tmp_path, monkeypatch
):
    import app.xhs_worker as worker_module
    from app.control_plane.nacos import NacosClient
    from app.xhs_service import create_app

    recovery_entered, callback_entered = asyncio.Event(), asyncio.Event()
    recovery_stopped, callback_stopped = asyncio.Event(), asyncio.Event()

    async def recover(_self):
        recovery_entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            recovery_stopped.set()

    async def deliver(_self):
        callback_entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            callback_stopped.set()

    original_close = XHSCompletionDelivery.close

    async def close_delivery(self):
        assert callback_stopped.is_set()
        await original_close(self)

    monkeypatch.setattr(worker_module, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(NacosClient, "register", AsyncMock())
    monkeypatch.setattr(NacosClient, "deregister", AsyncMock())
    monkeypatch.setattr(NacosClient, "beat", AsyncMock())
    monkeypatch.setattr(XHSRuntime, "recover_once", recover)
    monkeypatch.setattr(XHSCompletionDelivery, "deliver_once", deliver)
    monkeypatch.setattr(XHSCompletionDelivery, "close", close_delivery)
    worker = SimpleNamespace(
        _wait_for_dependencies=AsyncMock(), _heartbeat_loop=AsyncMock(),
        stop_event=asyncio.Event(), close=AsyncMock(), redis=SimpleNamespace(aclose=AsyncMock()),
    )
    app = create_app(
        config(tmp_path).model_copy(update={"xhs_service_advertise_ip": "127.0.0.1"}),
        worker_factory=lambda _: worker,
    )
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(recovery_entered.wait(), timeout=1)
        # The blocked recovery call does not prevent completion delivery.
        await asyncio.wait_for(callback_entered.wait(), timeout=1)
    assert recovery_stopped.is_set() and callback_stopped.is_set()
    assert app.state.completion_delivery.http.is_closed
    worker.close.assert_awaited_once()
