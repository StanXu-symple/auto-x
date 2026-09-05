import asyncio
import json

import pytest

from app.services import runtime_logs


def test_log_path_only_accepts_known_systems(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_logs, "LOG_DIR", tmp_path)

    assert runtime_logs.log_path("xhs-worker") == tmp_path / "xhs-worker.log"
    with pytest.raises(ValueError, match="Unsupported log system"):
        runtime_logs.log_path("../../etc/passwd")


def test_read_tail_returns_only_requested_lines(tmp_path) -> None:
    path = tmp_path / "worker.log"
    path.write_text("one\ntwo\nthree\n", encoding="utf-8")

    assert runtime_logs.read_tail(path, 2) == ["two", "three"]


async def test_stream_starts_with_recent_log_lines(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_logs, "LOG_DIR", tmp_path)
    (tmp_path / "backend.log").write_text("first\nsecond\n", encoding="utf-8")
    stream = runtime_logs.stream_log("backend", tail=1)

    event = await anext(stream)
    await stream.aclose()

    rows = event.splitlines()
    assert rows[0] == "event: ready"
    assert json.loads(rows[1].removeprefix("data: ")) == {
        "system": "backend",
        "lines": ["second"],
    }


async def test_stream_emits_appended_log_line(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_logs, "LOG_DIR", tmp_path)
    path = tmp_path / "worker.log"
    path.write_text("existing\n", encoding="utf-8")
    stream = runtime_logs.stream_log("worker", tail=0)
    await anext(stream)

    next_event = asyncio.create_task(anext(stream))
    await asyncio.sleep(0.05)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("new line\n")
    event = await asyncio.wait_for(next_event, timeout=1)
    await stream.aclose()

    assert event.startswith("event: log\n")
    assert json.loads(event.splitlines()[1].removeprefix("data: ")) == {"line": "new line"}
