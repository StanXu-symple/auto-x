"""Docker resource normalization shared by all service types (cgroup v1/v2)."""


def docker_resources(stats: dict, previous: dict | None = None) -> dict:
    cpu = stats.get("cpu_stats", {})
    before = previous or stats.get("precpu_stats", {})
    usage = cpu.get("cpu_usage", {})
    cpu_delta = usage.get("total_usage", 0) - before.get("cpu_usage", {}).get("total_usage", 0)
    system_delta = cpu.get("system_cpu_usage", 0) - before.get("system_cpu_usage", 0)
    cores = cpu.get("online_cpus") or len(usage.get("percpu_usage", []))
    percent = None
    if before.get("system_cpu_usage") and system_delta > 0 and cpu_delta >= 0 and cores:
        # 100% = one logical core, matching Docker stats and worker process CPU.
        percent = round(cpu_delta / system_delta * cores * 100, 2)
    memory = stats.get("memory_stats", {})
    usage_bytes = memory.get("usage")
    details = memory.get("stats", {})
    inactive = details.get("total_inactive_file", details.get("inactive_file", 0))
    used = max(0, usage_bytes - min(inactive, usage_bytes)) if usage_bytes is not None else None
    total = memory.get("limit") or None
    return {
        "cpu_percent": percent,
        "memory_used_bytes": used,
        "memory_total_bytes": total,
        "memory_percent": round(used / total * 100, 2) if used is not None and total else None,
        "resource_source": "docker",
        "memory_kind": "working_set",
    }


def unavailable(note: str) -> dict:
    return {
        "cpu_percent": None,
        "memory_used_bytes": None,
        "rss_bytes": None,
        "memory_total_bytes": None,
        "memory_percent": None,
        "resource_source": "docker",
        "resource_note": note,
    }
