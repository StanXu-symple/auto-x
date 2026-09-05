from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path

LOG_SYSTEMS = {
    "backend": "Backend",
    "worker": "轮询 Worker",
    "ai-worker": "AI Worker",
    "qq-worker": "QQ Worker",
    "xhs-worker": "小红书 Worker",
}
LOG_DIR = Path(os.getenv("LOG_DIR", "/var/log/xsentinel"))


def log_path(system: str) -> Path:
    if system not in LOG_SYSTEMS:
        raise ValueError("Unsupported log system")
    return LOG_DIR / f"{system}.log"


def read_tail(path: Path, limit: int) -> list[str]:
    lines, _, _ = read_tail_state(path, limit)
    return lines


def read_tail_state(path: Path, limit: int) -> tuple[list[str], int | None, int]:
    if not path.is_file():
        return [], None, 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        lines = list(deque((line.rstrip("\n") for line in handle), maxlen=limit))
        stat = os.fstat(handle.fileno())
        return lines, stat.st_ino, handle.tell()


def sse_event(event: str, payload: object) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def stream_log(system: str, *, tail: int = 200) -> AsyncIterator[str]:
    path = log_path(system)
    initial, inode, offset = await asyncio.to_thread(read_tail_state, path, tail)
    yield sse_event("ready", {"system": system, "lines": initial})

    idle_ticks = 0
    while True:
        await asyncio.sleep(0.5)
        try:
            handle = path.open(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            inode = None
            offset = 0
            continue
        with handle:
            stat = os.fstat(handle.fileno())
            if inode != stat.st_ino or stat.st_size < offset:
                inode = stat.st_ino
                offset = 0
            if stat.st_size > offset:
                handle.seek(offset)
                lines = [line.rstrip("\n") for line in handle]
                offset = handle.tell()
            else:
                lines = []
        if lines:
            for line in lines:
                yield sse_event("log", {"line": line})
            idle_ticks = 0
        else:
            idle_ticks += 1
            if idle_ticks >= 30:
                yield ": keepalive\n\n"
                idle_ticks = 0
