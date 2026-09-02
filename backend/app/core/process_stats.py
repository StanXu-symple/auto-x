import os
from pathlib import Path

import psutil


def _memory_limit_bytes() -> int:
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = Path(path).read_text(encoding="ascii").strip()
            if raw != "max":
                limit = int(raw)
                if 0 < limit < (1 << 60):
                    return limit
        except (OSError, ValueError):
            continue
    return int(psutil.virtual_memory().total)


class ProcessStatsSampler:
    def __init__(self) -> None:
        self.process = psutil.Process(os.getpid())
        self.memory_total_bytes = _memory_limit_bytes()
        self.process.cpu_percent(interval=None)

    def snapshot(self) -> dict[str, int | float]:
        with self.process.oneshot():
            rss_bytes = self.process.memory_info().rss
            return {
                "pid": self.process.pid,
                "cpu_percent": round(self.process.cpu_percent(interval=None), 2),
                "rss_bytes": rss_bytes,
                "memory_total_bytes": self.memory_total_bytes,
                "memory_percent": round((rss_bytes / self.memory_total_bytes) * 100, 2),
            }
