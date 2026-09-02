from app.core.process_stats import ProcessStatsSampler


def test_process_stats_snapshot_has_cpu_and_memory_capacity() -> None:
    snapshot = ProcessStatsSampler().snapshot()

    assert snapshot["pid"] > 0
    assert snapshot["cpu_percent"] >= 0
    assert snapshot["rss_bytes"] > 0
    assert snapshot["memory_total_bytes"] >= snapshot["rss_bytes"]
    assert 0 <= snapshot["memory_percent"] <= 100
