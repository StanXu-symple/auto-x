from app.services.system_health import collect_system_metrics


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


async def test_system_metrics_degrade_without_crashing() -> None:
    result = await collect_system_metrics(BrokenSession(), BrokenRedis())  # type: ignore[arg-type]
    assert result["database"]["status"] == "unhealthy"
    assert result["redis"]["status"] == "unhealthy"
    assert result["worker"]["status"] == "unknown"
    assert result["ai_worker"]["status"] == "unknown"
    assert result["memory"]["total_bytes"] > 0
