from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.control_plane.config import Topology
from app.control_plane.host_resources import HostCollector
from app.control_plane.monitor import MonitorCollector


def test_host_metrics_use_host_files_and_cpu_deltas(tmp_path):
    (tmp_path / 'stat').write_text('cpu 100 0 100 800 0 0 0 0 20 0\n')
    (tmp_path / 'meminfo').write_text('MemTotal: 1000 kB\nMemAvailable: 400 kB\n')
    (tmp_path / 'uptime').write_text('86401.0 90000.0\n')
    (tmp_path / 'loadavg').write_text('0.10 0.20 0.30 1/100 123\n')
    collector = HostCollector(str(tmp_path), str(tmp_path))
    first = collector.collect()
    assert first['cpu_percent'] is None
    assert first['memory']['used_bytes'] == 600 * 1024
    assert first['memory']['percent'] == 60
    assert first['uptime_seconds'] == 86401
    assert first['disk']['total_bytes'] > 0
    (tmp_path / 'stat').write_text('cpu 120 0 130 850 0 0 0 0 30 0\n')
    assert collector.collect()['cpu_percent'] == 50
    (tmp_path / 'meminfo').unlink()
    failed = collector.collect()
    assert failed['status'] == 'unknown'
    assert 'memory' not in failed


@pytest.mark.asyncio
async def test_center_keeps_failed_nodes_and_recovers_discovery(monkeypatch):
    monkeypatch.setenv('NACOS_ADVERTISE_IP', '192.0.2.1')
    topology = Topology(nodes={n: {'agent_service_name': n} for n in ['a', 'b', 'c']}, services=[])
    now = datetime.now(UTC).isoformat()
    seen = []
    async def respond(request):
        seen.append(str(request.url))
        host = request.url.host
        node = 'a' if host == 'monitor-agent' else 'b'
        return httpx.Response(200, json={
            'node': node, 'sampled_at': now, 'instances': [],
            'host': {'status': 'healthy', 'sampled_at': now, 'cpu_percent': 12.5},
        })
    async def discover(name):
        if name == 'c':
            raise RuntimeError('No healthy instance')
        ip = '192.0.2.1' if name == 'a' else '192.0.2.2'
        return SimpleNamespace(ip=ip, url=f'http://{ip}:9101')
    tokens = SimpleNamespace(token=AsyncMock(return_value='test'), nacos=SimpleNamespace(discover=discover))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        center = MonitorCollector(topology, http, tokens)
        result = await center.collect()
        assert list(result['hosts']) == ['a', 'b', 'c']
        assert result['hosts']['a']['cpu_percent'] == 12.5
        assert result['hosts']['b']['status'] == 'healthy'
        assert result['hosts']['c']['status'] == 'unknown'
        assert len(seen) == 2  # One request per reachable node, including host metrics.
        assert seen[0].startswith('http://monitor-agent:9101')
        again = await center.collect()
        assert again['hosts']['a']['status'] == 'healthy'


@pytest.mark.asyncio
async def test_stale_snapshot_discards_host_values():
    topology = Topology(nodes={'a': {}}, services=[])
    stale = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    async def respond(request):
        return httpx.Response(200, json={'node': 'a', 'sampled_at': stale, 'instances': [],
                                       'host': {'cpu_percent': 99, 'sampled_at': stale}})
    tokens = SimpleNamespace(token=AsyncMock(return_value='test'), nacos=SimpleNamespace(
        discover=AsyncMock(return_value=SimpleNamespace(ip='192.0.2.3', url='http://agent'))))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        result = await MonitorCollector(topology, http, tokens).collect()
        assert result['hosts']['a']['status'] == 'unknown'
        assert 'cpu_percent' not in result['hosts']['a']


@pytest.mark.asyncio
async def test_http_xhs_service_publishes_and_stops_redis_heartbeat(monkeypatch, tmp_path):
    import asyncio
    from app.core.config import Settings
    from app.xhs_service import create_app
    from app.control_plane.nacos import NacosClient
    import app.xhs_worker as worker_module

    emitted = asyncio.Event()
    stopped = asyncio.Event()
    async def heartbeat():
        try:
            emitted.set()
            await asyncio.Event().wait()
        finally:
            stopped.set()
    worker = SimpleNamespace(
        _wait_for_dependencies=AsyncMock(), _heartbeat_loop=heartbeat,
        stop_event=asyncio.Event(), browser_pool=SimpleNamespace(close=AsyncMock()),
        redis=SimpleNamespace(aclose=AsyncMock()),
    )
    monkeypatch.setattr(worker_module, 'UPLOAD_DIR', tmp_path)
    monkeypatch.setattr(NacosClient, 'register', AsyncMock())
    monkeypatch.setattr(NacosClient, 'deregister', AsyncMock())
    monkeypatch.setattr(NacosClient, 'beat', AsyncMock())
    settings = Settings(nacos_server_addr='', nacos_config_required=False,
                        xhs_service_advertise_ip='127.0.0.1')
    app = create_app(settings=settings, worker_factory=lambda _: worker)
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(emitted.wait(), timeout=1)
        assert not stopped.is_set()
    assert stopped.is_set()
    worker.redis.aclose.assert_awaited_once()
