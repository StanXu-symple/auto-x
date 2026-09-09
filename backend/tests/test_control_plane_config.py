from __future__ import annotations

import pytest

from app.control_plane.config import (
    Topology,
    apply_runtime_topology,
    load_runtime_config,
)


@pytest.mark.asyncio
async def test_control_plane_reads_shared_tuning_and_filters_local_identity(monkeypatch) -> None:
    class FakeNacos:
        async def get_json_config(self, data_id: str, *, group: str):
            assert data_id == "runtime.json"
            assert group == "CONFIG_GROUP"
            return {
                "monitor": {
                    "interval_seconds": 7,
                    "stale_seconds": 30,
                    "timeout_seconds": 2,
                    "concurrency": 12,
                },
                "service_auth": {"rate_limit": 240, "token_lifetime_seconds": 180},
                "nacos_password": "must-not-be-returned",
                "nacos_service_name": "must-stay-local",
                "service_topology_file": "/remote/path",
            }

    monkeypatch.setenv("NACOS_CONFIG_DATA_ID", "runtime.json")
    monkeypatch.setenv("NACOS_CONFIG_GROUP", "CONFIG_GROUP")
    runtime = await load_runtime_config(FakeNacos())

    assert runtime["monitor_interval_seconds"] == 7
    assert runtime["monitor_concurrency"] == 12
    assert runtime["service_auth_rate_limit"] == 240
    assert runtime["service_auth_token_lifetime_seconds"] == 180
    assert "nacos_password" not in runtime
    assert "nacos_service_name" not in runtime
    assert "service_topology_file" not in runtime


@pytest.mark.asyncio
async def test_control_plane_config_required_controls_failure(monkeypatch) -> None:
    class FailingNacos:
        async def get_json_config(self, *_args, **_kwargs):
            raise OSError("unavailable")

    monkeypatch.setenv("NACOS_CONFIG_REQUIRED", "false")
    assert await load_runtime_config(FailingNacos()) == {}

    monkeypatch.setenv("NACOS_CONFIG_REQUIRED", "true")
    with pytest.raises(RuntimeError, match="Unable to load Nacos Config"):
        await load_runtime_config(FailingNacos())
    with pytest.raises(RuntimeError, match="NACOS_SERVER_ADDR"):
        await load_runtime_config(None)


def test_remote_monitor_tuning_preserves_static_topology() -> None:
    topology = Topology(
        nodes={"local": {}},
        services=[],
        interval_seconds=10,
        stale_seconds=45,
        timeout_seconds=3,
        concurrency=8,
    )
    updated = apply_runtime_topology(
        topology,
        {
            "monitor_interval_seconds": "6",
            "monitor_stale_seconds": "24",
            "monitor_timeout_seconds": "1.5",
            "monitor_concurrency": "16",
        },
    )

    assert updated.nodes == topology.nodes
    assert updated.services == topology.services
    assert updated.interval_seconds == 6
    assert updated.stale_seconds == 24
    assert updated.timeout_seconds == 1.5
    assert updated.concurrency == 16


def test_invalid_remote_monitor_combination_uses_local_topology() -> None:
    topology = Topology(nodes={"local": {}}, services=[])
    updated = apply_runtime_topology(
        topology,
        {"monitor_interval_seconds": 30, "monitor_stale_seconds": 20},
    )
    assert updated == topology
