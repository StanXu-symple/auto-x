from __future__ import annotations

import asyncio

import httpx
import pytest

from app.control_plane.nacos import (
    NacosClient,
    NacosServiceRegistration,
    heartbeat_loop,
)


@pytest.mark.asyncio
async def test_beat_reregisters_missing_instance_with_metadata() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "PUT":
            return httpx.Response(200, json={"code": 20404})
        return httpx.Response(200, text="true")

    metadata = {"component": "worker", "version": "test"}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        nacos = NacosClient(http, "http://nacos:8848", "public", "X_SENTINEL")
        await nacos.beat("xsentinel-worker", "10.0.0.2", 8201, metadata)

    assert [request.method for request in requests] == ["PUT", "POST"]
    assert requests[1].url.params["serviceName"] == "xsentinel-worker"
    assert requests[1].url.params["port"] == "8201"
    assert requests[1].url.params["metadata"] == ('{"component": "worker", "version": "test"}')


@pytest.mark.asyncio
async def test_heartbeat_reconnect_registers_before_next_beat() -> None:
    calls: list[str] = []
    registered = asyncio.Event()

    class FakeNacos:
        async def beat(self, *_args, **_kwargs) -> None:
            calls.append("beat")
            raise httpx.ConnectError("temporary outage")

        async def register(self, *_args, **_kwargs) -> None:
            calls.append("register")
            registered.set()

    task = asyncio.create_task(
        heartbeat_loop(
            FakeNacos(),  # type: ignore[arg-type]
            "xsentinel-ai-worker",
            "10.0.0.3",
            8202,
            interval=0,
        )
    )
    await asyncio.wait_for(registered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls[:2] == ["beat", "register"]


@pytest.mark.asyncio
async def test_registration_deregisters_during_graceful_shutdown() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="true")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    nacos = NacosClient(http, "http://nacos:8848", "public", "X_SENTINEL")
    registration = NacosServiceRegistration(
        http,
        nacos,
        "xsentinel-qq-worker",
        "10.0.0.4",
        8203,
    )

    await registration.start()
    await registration.aclose()

    assert [request.method for request in requests] == ["POST", "DELETE"]
    assert requests[-1].url.params["serviceName"] == "xsentinel-qq-worker"
    assert requests[-1].url.params["port"] == "8203"
    assert http.is_closed


@pytest.mark.asyncio
async def test_unready_service_is_removed_then_reregistered_after_recovery() -> None:
    calls: list[str] = []
    recovered = asyncio.Event()
    readiness = iter((False, True))

    class FakeNacos:
        async def beat(self, *_args, **_kwargs) -> None:
            calls.append("beat")

        async def register(self, *_args, **_kwargs) -> None:
            calls.append("register")
            recovered.set()

        async def deregister(self, *_args, **_kwargs) -> None:
            calls.append("deregister")

    async def ready() -> bool:
        return next(readiness, True)

    task = asyncio.create_task(
        heartbeat_loop(
            FakeNacos(),  # type: ignore[arg-type]
            "xsentinel-auth-center",
            "10.0.0.5",
            9100,
            interval=0,
            readiness=ready,
        )
    )
    await asyncio.wait_for(recovered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls[:2] == ["deregister", "register"]


@pytest.mark.asyncio
async def test_unready_service_retries_failed_deregistration() -> None:
    calls: list[str] = []
    removed = asyncio.Event()

    class FakeNacos:
        async def beat(self, *_args, **_kwargs) -> None:
            calls.append("beat")

        async def register(self, *_args, **_kwargs) -> None:
            calls.append("register")

        async def deregister(self, *_args, **_kwargs) -> None:
            calls.append("deregister")
            if calls.count("deregister") == 1:
                raise httpx.ConnectError("temporary Nacos outage")
            removed.set()

    async def unready() -> bool:
        return False

    task = asyncio.create_task(
        heartbeat_loop(
            FakeNacos(),  # type: ignore[arg-type]
            "xsentinel-auth-center",
            "10.0.0.5",
            9100,
            interval=0,
            readiness=unready,
        )
    )
    await asyncio.wait_for(removed.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls[:2] == ["deregister", "deregister"]
