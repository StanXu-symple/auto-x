"""Host measurements from read-only host mounts, independent of container limits."""
import os
from datetime import UTC, datetime
from pathlib import Path


class HostCollector:
    def __init__(self, proc: str = "/host/proc", root: str = "/host/root"):
        self.proc = Path(proc)
        self.root = root
        self.previous: tuple[int, int] | None = None

    def collect(self) -> dict:
        result = {"status": "unknown", "sampled_at": datetime.now(UTC).isoformat()}
        try:
            values = [int(v) for v in (self.proc / "stat").read_text().splitlines()[0].split()[1:]]
            # guest/guest_nice are already counted in user/nice.
            total, idle = sum(values[:8]), values[3] + values[4]
            cpu = None
            if self.previous:
                dt, di = total - self.previous[0], idle - self.previous[1]
                if dt > 0 and 0 <= di <= dt:
                    cpu = round((dt - di) / dt * 100, 2)
            self.previous = total, idle
            mem = {}
            for line in (self.proc / "meminfo").read_text().splitlines():
                key, value = line.split(":", 1)
                mem[key] = int(value.split()[0]) * 1024
            available = mem["MemAvailable"]
            used = mem["MemTotal"] - available
            disk = os.statvfs(self.root)
            disk_total = disk.f_blocks * disk.f_frsize
            disk_used = (disk.f_blocks - disk.f_bfree) * disk.f_frsize
            disk_free = disk.f_bavail * disk.f_frsize
            result.update(
                status="healthy", cpu_percent=cpu,
                memory={"total_bytes": mem["MemTotal"], "used_bytes": used,
                        "available_bytes": available, "percent": round(used / mem["MemTotal"] * 100, 2)},
                disk={"total_bytes": disk_total, "used_bytes": disk_used,
                      "free_bytes": disk_free, "percent": round(disk_used / (disk_used + disk_free) * 100, 2) if disk_used + disk_free else 0},
                uptime_seconds=float((self.proc / "uptime").read_text().split()[0]),
                load_average=[float(x) for x in (self.proc / "loadavg").read_text().split()[:3]],
            )
        except (OSError, ValueError, KeyError, IndexError):
            result.update(status="unknown", error="宿主机资源采集失败，请检查只读挂载")
        return result
