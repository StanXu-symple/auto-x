import asyncio
import base64
import hashlib
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import pytest
from test_camoufox_service import MemoryRedis, auth, settings

from app.camoufox_executor import CamoufoxExecutor
from app.camoufox_service import create_app
from app.control_plane.nacos import Instance
from app.schemas.camoufox import BrowserJobRequest
from app.services.browser_job_api import XHSRuntime
from app.services.browser_screenshot_artifacts import expire_artifacts, store_artifact
from app.services.tweet_screenshot_client import TweetScreenshotClient

PNG = b"\x89PNG\r\n\x1a\ntransport-fixture"


def capture_result():
    return {
        "tweet_id": "123",
        "username": "openai",
        "canonical_url": "https://x.com/openai/status/123",
        "width": 600,
        "height": 300,
        "captured_at": "2026-10-01T00:00:00Z",
        "png_base64": base64.b64encode(PNG).decode(),
        "sha256": hashlib.sha256(PNG).hexdigest(),
    }


def screenshot_job():
    return {
        "job_id": "b" * 32,
        "admin_id": 2147483647,
        "payload": {
            "operation": "x_screenshot", "tweet_id": "123",
            "username": "openai", "expected_text": "Hello", "expected_media_count": 0,
        },
    }


async def test_screenshot_api_transfers_authenticated_png_and_metadata_only(tmp_path):
    config = settings(tmp_path)
    worker = SimpleNamespace(
        redis=MemoryRedis(), active_tasks=0,
        _execute_job=AsyncMock(return_value=capture_result()),
    )
    app = create_app(config)
    app.state.runtime = XHSRuntime(worker, config, "browser", "camoufox", tmp_path / "png")
    app.state.upload_root = tmp_path
    app.state.verifier, token = auth()
    headers = {"Authorization": f"Bearer {token()}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://browser"
    ) as client:
        assert (await client.get("/v1/jobs/" + "b" * 32 + "/screenshot")).status_code == 401
        accepted = await client.post(
            "/v1/jobs", data={"job": json.dumps(screenshot_job())}, headers=headers
        )
        assert accepted.status_code == 202
        await asyncio.gather(*list(app.state.runtime.tasks))
        state = await client.get("/v1/jobs/" + "b" * 32, headers=headers)
        assert state.json()["state"] == "succeeded"
        assert "png_base64" not in state.json()["data"]
        assert state.json()["data"]["size_bytes"] == len(PNG)
        image = await client.get("/v1/jobs/" + "b" * 32 + "/screenshot", headers=headers)
        assert image.content == PNG
        assert image.headers["content-type"] == "image/png"
        assert "no-store" in image.headers["cache-control"]
        replay = await client.post(
            "/v1/jobs", data={"job": json.dumps(screenshot_job())}, headers=headers
        )
        assert replay.json()["state"] == "succeeded"
        worker._execute_job.assert_awaited_once()
        # No arbitrary URL/path or code may pass the public screenshot contract.
        invalid = screenshot_job()
        invalid["payload"]["url"] = "http://localhost/private"
        assert (await client.post(
            "/v1/jobs", data={"job": json.dumps(invalid)}, headers=headers
        )).status_code == 422
        (tmp_path / "png" / ("b" * 32 + ".png")).unlink()
        assert (await client.get(
            "/v1/jobs/" + "b" * 32 + "/screenshot", headers=headers
        )).status_code == 404


async def test_executor_capture_does_not_decrypt_xhs_cookies():
    executor = object.__new__(CamoufoxExecutor)
    executor.browser_pool = SimpleNamespace(capture_tweet=AsyncMock(return_value=capture_result()))
    # No settings or credentials exist on this fixture: screenshot must branch first.
    job = screenshot_job()
    job["operation"] = job["payload"].pop("operation")
    assert (await executor.execute(job))["tweet_id"] == "123"
    executor.browser_pool.capture_tweet.assert_awaited_once_with(
        tweet_id="123", username="openai", expected_text="Hello", expected_media_count=0
    )


async def test_client_uses_separate_secret_and_pinned_instance_for_binary(tmp_path, monkeypatch):
    config = settings(tmp_path).model_copy(update={
        "tweet_screenshot_client_secret_file": str(tmp_path / "screenshots.secret"),
    })
    (tmp_path / "screenshots.secret").write_text("b" * 64)
    client = TweetScreenshotClient(config)
    assert client.tokens.client_id == "screenshot-worker"
    assert client.settings.service_client_secret_file == str(tmp_path / "screenshots.secret")
    client.tokens.token = AsyncMock(return_value="service-token")
    client.nacos.discover_all = AsyncMock(return_value=[Instance("browser-a", 8007, "browser")])
    calls = []
    metadata = {key: value for key, value in capture_result().items() if key != "png_base64"}
    metadata.update(screenshot_ready=True, size_bytes=len(PNG))

    def handler(request):
        calls.append(request)
        assert request.url.host == "browser-a"
        assert request.headers["authorization"] == "Bearer service-token"
        if request.method == "POST":
            submitted = json.loads(parse_qs(request.content.decode())["job"][0])
            assert submitted["payload"]["operation"] == "x_screenshot"
            assert b"encrypted_a1" not in request.content
            return httpx.Response(202, json={"job_id": "b" * 32, "state": "running"})
        if request.url.path.endswith("/screenshot"):
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        return httpx.Response(200, json={
            "job_id": "b" * 32, "state": "succeeded", "data": metadata,
        })

    await client.http.aclose()
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    response = await client.capture(
        tweet_id="123", username="openai", expected_text="Hello", job_id="b" * 32
    )
    assert base64.b64decode(response["png_base64"]) == PNG
    assert [request.method for request in calls] == ["POST", "GET", "GET"]
    client.nacos.discover_all.assert_awaited_once()
    await client.aclose()


@pytest.mark.parametrize("failure", ["busy", "hash", "wrong_job"])
async def test_client_rejects_busy_or_corrupt_result_without_resubmitting(tmp_path, failure):
    config = settings(tmp_path)
    client = TweetScreenshotClient(config.model_copy(update={
        "tweet_screenshot_client_secret_file": config.service_client_secret_file,
    }))
    client.tokens.token = AsyncMock(return_value="token")
    client.nacos.discover_all = AsyncMock(return_value=[Instance("browser", 8007, "browser")])
    requests = []
    metadata = {key: value for key, value in capture_result().items() if key != "png_base64"}
    metadata.update(screenshot_ready=True, size_bytes=len(PNG))
    if failure == "hash":
        metadata["sha256"] = "0" * 64

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            if failure == "busy":
                return httpx.Response(429)
            return httpx.Response(202, json={
                "job_id": ("c" if failure == "wrong_job" else "b") * 32,
                "state": "succeeded", "data": metadata,
            })
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    await client.http.aclose()
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises((ValueError, RuntimeError)):
        await client.capture(
            tweet_id="123", username="openai", expected_text="Hello", job_id="b" * 32
        )
    assert sum(request.method == "POST" for request in requests) == 1
    await client.aclose()


def test_artifacts_expire_and_schema_restricts_url_components(tmp_path):
    store_artifact(tmp_path, "a" * 32, capture_result())
    image = tmp_path / ("a" * 32 + ".png")
    os.utime(image, (time.time() - 1000,) * 2)
    expire_artifacts(tmp_path, 600)
    assert not image.exists()
    job = screenshot_job()
    job["payload"]["username"] = "../admin"
    with pytest.raises(ValueError):
        BrowserJobRequest.model_validate(job)
