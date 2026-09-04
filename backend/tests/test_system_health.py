import json

from app.services.system_health import CPU_SAMPLER, CumulativeCPUSampler, collect_system_metrics


class BrokenSession:
    async def execute(self, *_args, **_kwargs):
        raise RuntimeError("database unavailable")

    async def rollback(self):
        return None


class BrokenRedis:
    async def ping(self):
        raise RuntimeError("redis unavailable")

    async def get(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")


class HealthySession:
    async def execute(self, statement):
        sql = str(statement)
        if "SHOW GLOBAL STATUS" in sql:
            return [
                ("Cpu_time", "1500"),
                ("Innodb_buffer_pool_bytes_data", "400"),
            ]
        return []

    async def scalar(self, _statement):
        return 1000

    async def rollback(self):
        return None


class HealthyRedis:
    async def ping(self):
        return True

    async def info(self):
        return {
            "used_memory": 250,
            "maxmemory": 1000,
            "used_cpu_sys": 2.0,
            "used_cpu_user": 3.0,
        }

    async def get(self, key: str):
        return json.dumps(
            {
                "worker_id": key,
                "last_heartbeat": "2026-09-03T00:00:00Z",
                "cpu_percent": 12.5,
                "rss_bytes": 256,
                "memory_total_bytes": 1024,
                "memory_percent": 25,
            }
        )

    async def ttl(self, _key: str):
        return 30


async def test_system_metrics_degrade_without_crashing() -> None:
    result = await collect_system_metrics(BrokenSession(), BrokenRedis())  # type: ignore[arg-type]
    assert result["database"]["status"] == "unhealthy"
    assert result["redis"]["status"] == "unhealthy"
    assert result["worker"]["status"] == "unknown"
    assert result["ai_worker"]["status"] == "unknown"
    assert result["qq_worker"]["status"] == "unknown"
    assert result["memory"]["total_bytes"] > 0


def test_cumulative_cpu_sampler_uses_elapsed_wall_time() -> None:
    sampler = CumulativeCPUSampler()
    assert sampler.sample("service", 10, now=100) is None
    assert sampler.sample("service", 10.5, now=102) == 25
    assert sampler.sample("service", 1, now=103) is None


async def test_system_metrics_include_service_and_worker_resources() -> None:
    CPU_SAMPLER.samples.clear()
    result = await collect_system_metrics(HealthySession(), HealthyRedis())  # type: ignore[arg-type]

    assert result["database"]["status"] == "healthy"
    assert result["database"]["memory_percent"] == 40
    assert result["database"]["cpu_percent"] is None
    assert result["redis"]["memory_percent"] == 25
    assert result["redis"]["cpu_percent"] is None
    assert result["worker"]["cpu_percent"] == 12.5
    assert result["ai_worker"]["memory_percent"] == 25
    assert result["qq_worker"]["status"] == "online"
