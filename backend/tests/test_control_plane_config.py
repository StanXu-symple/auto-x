from __future__ import annotations

import pytest

from app.control_plane.config import (
    Topology,
    load_runtime_config,
    load_monitor_topology,
    load_monitor_node_id,
)


@pytest.mark.asyncio
async def test_control_plane_reads_shared_auth_tuning_and_filters_local_identity(monkeypatch) -> None:
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


@pytest.mark.asyncio
async def test_monitor_uses_separate_nacos_topology_and_address_mapping(monkeypatch) -> None:
    documents = {
        "x-sentinel-monitor-topology.json": {
            "interval_seconds": 10, "stale_seconds": 45,
            "timeout_seconds": 3, "concurrency": 8,
            "nodes": {"tc-1": {"agent_service_name": "xsentinel-monitor-agent-tc-1"}},
            "services": [{
                "id": "tc1-agent", "name": "Agent", "component": "monitor",
                "node": "tc-1", "project": "x-sentinel",
                "container_service": "monitor-agent", "port": 9101,
            }],
        },
        "x-sentinel-monitor-nodes.json": {
            "nodes": {"tc-1": {"advertise_ip": "203.0.113.10"}}
        },
    }

    class FakeNacos:
        async def get_json_config(self, data_id: str, *, group: str):
            assert group == "X_SENTINEL"
            return documents.get(data_id)

    monkeypatch.setenv("NACOS_CONFIG_ENABLED", "true")
    monkeypatch.setenv("NACOS_CONFIG_REQUIRED", "true")
    monkeypatch.setenv("NACOS_ADVERTISE_IP", "203.0.113.10")
    topology = await load_monitor_topology(FakeNacos())
    assert topology.services[0].id == "tc1-agent"
    assert await load_monitor_node_id(FakeNacos(), topology) == "tc-1"

    documents["x-sentinel-monitor-nodes.json"]["nodes"]["tc-1"]["advertise_ip"] = "203.0.113.11"
    with pytest.raises(RuntimeError, match="Unable to resolve monitor node"):
        await load_monitor_node_id(FakeNacos(), topology)


@pytest.mark.asyncio
async def test_required_monitor_topology_has_no_silent_local_fallback(monkeypatch) -> None:
    class MissingNacos:
        async def get_json_config(self, *_args, **_kwargs):
            return None

    monkeypatch.setenv("NACOS_CONFIG_ENABLED", "true")
    monkeypatch.setenv("NACOS_CONFIG_REQUIRED", "true")
    with pytest.raises(RuntimeError, match="Unable to load monitor topology"):
        await load_monitor_topology(MissingNacos())
