import asyncio
import logging
from pathlib import Path

from app.xhs_worker import (
    RELEASE_HEARTBEAT_SCRIPT,
    XiaohongshuWorker,
    _capture_cli_stream,
    _cgroup_memory_snapshot,
    _cli_executable,
    _oom_kill_count,
    _parse_cli_stage_line,
    _strip_cli_stage_lines,
)


class FakeRedis:
    def __init__(self) -> None:
        self.eval_args: tuple[object, ...] | None = None

    async def eval(self, *args: object) -> int:
        self.eval_args = args
        return 1


async def test_worker_only_releases_its_own_heartbeat() -> None:
    redis = FakeRedis()
    worker = object.__new__(XiaohongshuWorker)
    worker.redis = redis  # type: ignore[assignment]
    worker.worker_id = "current-worker"

    await worker._release_heartbeat()

    assert redis.eval_args == (
        RELEASE_HEARTBEAT_SCRIPT,
        1,
        "xsentinel:xhs-worker:heartbeat",
        "current-worker",
    )
    assert "heartbeat['worker_id'] == ARGV[1]" in RELEASE_HEARTBEAT_SCRIPT


async def test_dependency_wait_stops_during_retry() -> None:
    class UnavailableRedis:
        async def ping(self) -> None:
            raise ConnectionError("redis starting")

    worker = object.__new__(XiaohongshuWorker)
    worker.redis = UnavailableRedis()  # type: ignore[assignment]
    worker.stop_event = asyncio.Event()
    worker.stop_event.set()

    assert await worker._wait_for_dependencies(attempts=2) is False


def test_cgroup_memory_snapshot(tmp_path: Path) -> None:
    (tmp_path / "memory.current").write_text("1048576\n", encoding="ascii")
    (tmp_path / "memory.peak").write_text("2097152\n", encoding="ascii")
    (tmp_path / "memory.max").write_text("max\n", encoding="ascii")
    (tmp_path / "memory.events").write_text(
        "low 0\nhigh 0\nmax 3\noom 2\noom_kill 1\n",
        encoding="ascii",
    )

    snapshot = _cgroup_memory_snapshot(tmp_path)

    assert snapshot["current_bytes"] == 1048576
    assert snapshot["peak_bytes"] == 2097152
    assert snapshot["limit_bytes"] is None
    assert _oom_kill_count(snapshot) == 1


def test_post_uses_compatibility_cli_only() -> None:
    assert _cli_executable(("post", "title"))[-2:] == ("-m", "app.xhs_cli_compat")
    assert _cli_executable(("login", "--cookie", "value")) == ("xhs",)


def test_parse_and_strip_cli_stage_lines() -> None:
    stage_line = (
        'XHS_STAGE {"level":"INFO","stage":"browser_ready",'
        '"message":"虚拟浏览器启动成功"}'
    )

    assert _parse_cli_stage_line(stage_line) == {
        "level": "INFO",
        "stage": "browser_ready",
        "message": "虚拟浏览器启动成功",
    }
    assert _parse_cli_stage_line("ordinary output") is None
    assert _strip_cli_stage_lines(f"{stage_line}\nreal error") == "real error"


async def test_capture_cli_stream_relays_stage_immediately(caplog) -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(
        'XHS_STAGE {"level":"INFO","stage":"page_ready",'
        '"message":"小红书图文发布页面进入成功"}\n'.encode()
    )
    reader.feed_eof()
    chunks: list[bytes] = []

    with caplog.at_level(logging.INFO):
        await _capture_cli_stream(
            reader,
            chunks,
            command="post",
            stream_name="stderr",
            admin_id=7,
        )

    record = next(
        item for item in caplog.records if item.message == "小红书图文发布页面进入成功"
    )
    assert record.stage == "page_ready"
    assert record.admin_id == 7
    assert b"XHS_STAGE" in b"".join(chunks)
