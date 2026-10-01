#!/usr/bin/env python3
"""Seed the X Sentinel runtime configuration into Nacos Config.

The installer calls this script after creating ``.env``.  Existing Nacos
values are authoritative; values present only in the local environment are
added so upgrades can introduce new settings without overwriting operator
changes made in Nacos.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlsplit
from urllib.request import Request, urlopen

EXCLUDED_KEYS = {
    "NACOS_SERVER_ADDR",
    "NACOS_NAMESPACE",
    "NACOS_GROUP",
    "NACOS_USERNAME",
    "NACOS_PASSWORD",
    "NACOS_ADVERTISE_IP",
    "NACOS_CONFIG_ENABLED",
    "NACOS_CONFIG_DATA_ID",
    "NACOS_CONFIG_GROUP",
    "NACOS_CONFIG_FORMAT",
    "NACOS_CONFIG_TIMEOUT_SECONDS",
    "NACOS_CONFIG_REQUIRED",
    # Per-process/deployment identity stays local; these values differ between
    # backend, workers and control-plane instances and cannot be shared safely.
    "SERVICE_AUTH_PRIVATE_KEY_FILE",
    "SERVICE_AUTH_CLIENTS_FILE",
    "SERVICE_TOPOLOGY_FILE",
    "SERVICE_CLIENT_SECRET_FILE",
    "RUNTIME_LOGS_CLIENT_SECRET_FILE",
    "SERVICE_AUTH_PUBLIC_KEY_FILE",
    "MONITOR_NODE_ID",
    "DOCKER_SOCKET",
    "NACOS_SERVICE_NAME",
    "NACOS_SERVICE_PORT",
    "WORKER_METRICS_PORT",
    "AI_WORKER_METRICS_PORT",
    "QQ_WORKER_PORT",
    "QQ_WORKER_METRICS_PORT",
    "XHS_WORKER_METRICS_PORT",
    "XHS_TRANSPORT",
    "XHS_SERVICE_NAME",
    "XHS_SERVICE_PORT",
    "XHS_SERVICE_ADVERTISE_IP",
    "XHS_SERVICE_ADVERTISE_PORT",
    "IMAGE_TAG",
    "BACKEND_IMAGE",
    "XHS_WORKER_IMAGE",
    "CAMOUFOX_WORKER_IMAGE",
    "CAMOUFOX_WORKER_BIND_IP",
    "CAMOUFOX_WORKER_HOST_PORT",
    "CAMOUFOX_SERVICE_ADVERTISE_IP",
    "CAMOUFOX_SERVICE_ADVERTISE_PORT",
    "FRONTEND_IMAGE",
    "APP_BIND_IP",
    "APP_PORT",
    "DOCKER_SOCKET_GID",
    "CONTROL_PLANE_DIR",
    "NETWORK_GROUPS",
    "CONTROL_NETWORK_NAME",
    "CONTROL_NETWORK_EXTERNAL",
    "APP_NETWORK_NAME",
    "APP_NETWORK_EXTERNAL",
    "DATA_NETWORK_NAME",
    "DATA_NETWORK_EXTERNAL",
    "MONITORING_NETWORK_NAME",
    "MONITORING_NETWORK_EXTERNAL",
    "BACKEND_BIND_IP",
    "BACKEND_HOST_PORT",
    "POSTGRES_BIND_IP",
    "POSTGRES_HOST_PORT",
    "REDIS_BIND_IP",
    "REDIS_HOST_PORT",
    "WORKER_BIND_IP",
    "WORKER_HOST_PORT",
    "AI_WORKER_BIND_IP",
    "AI_WORKER_HOST_PORT",
    "QQ_WORKER_BIND_IP",
    "QQ_WORKER_HOST_PORT",
    "XHS_WORKER_BIND_IP",
    "XHS_WORKER_HOST_PORT",
    "AUTH_CENTER_BIND_IP",
    "AUTH_CENTER_PORT",
    "MONITOR_AGENT_BIND_IP",
    "MONITOR_AGENT_PORT",
    "MONITOR_CENTER_BIND_IP",
    "MONITOR_CENTER_BIND",
    "MONITOR_CENTER_PORT",
    "PROMETHEUS_BIND_IP",
    "PROMETHEUS_PORT",
    "GRAFANA_BIND_IP",
    "GRAFANA_PORT",
    "GRAFANA_ROOT_URL",
    "BACKUP_DIR",
    "SERVICE_NAME",
    "LOG_DIR",
    "HOME",
    "ARTICLE_UPLOAD_DIR",
    "TWEET_SCREENSHOT_DIR",
    "TWEET_SCREENSHOT_VOLUME",
    "TWEET_SCREENSHOT_CLIENT_SECRET_FILE",
}

# Publish only settings that the application or control plane actually knows
# how to consume.  Bootstrap values needed to reach Nacos remain local; every
# other application setting, including credentials, is a Nacos-managed value.
RUNTIME_CONFIG_KEYS = {
    "ADMIN_USERNAME", "ADMIN_PASSWORD", "GRAFANA_ADMIN_USER", "GRAFANA_ADMIN_PASSWORD",
    "POSTGRES_EXPORTER_PASSWORD", "OPENAI_API_KEY", "CODEX_BRIDGE_API_KEY", "CODEX_BRIDGE_TOKEN",
    "SERVICE_AUTH_KEY_ENCRYPTION_KEY",
    "APP_NAME",
    "ENVIRONMENT", "DEBUG", "STARTUP_STRICT", "AUTO_CREATE_TABLES",
    "API_PREFIX",
    "LOG_LEVEL",
    "APP_TIMEZONE",
    "POSTGRES_DSN",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DATABASE",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_POOL_SIZE",
    "POSTGRES_MAX_OVERFLOW",
    "POSTGRES_POOL_RECYCLE_SECONDS",
    "REDIS_URL",
    "REDIS_HOST",
    "REDIS_PORT",
    "REDIS_DB",
    "REDIS_PASSWORD",
    "REDIS_SOCKET_TIMEOUT_SECONDS",
    "REDIS_MAXMEMORY",
    "MONITOR_REQUEST_TIMEOUT_SECONDS",
    "MONITOR_STALE_SECONDS",
    "SERVICE_AUTH_RATE_LIMIT",
    "SERVICE_AUTH_RATE_WINDOW_SECONDS",
    "SERVICE_AUTH_TOKEN_LIFETIME_SECONDS",
    "SERVICE_AUTH_KEY_ROTATION_DAYS",
    "SERVICE_AUTH_KEY_OVERLAP_DAYS",
    "JWT_SECRET_KEY",
    "JWT_ALGORITHM",
    "JWT_EXPIRE_MINUTES",
    "X_TOKEN_ENCRYPTION_KEY",
    "X_TOKEN_CACHE_TTL_SECONDS",
    "X_API_BASE_URL",
    "X_REQUEST_TIMEOUT_SECONDS",
    "X_MAX_PAGES_PER_POLL",
    "X_PAGE_SIZE",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "WORKER_SCAN_INTERVAL_SECONDS",
    "WORKER_MAX_CONCURRENCY",
    "WORKER_BATCH_SIZE",
    "WORKER_LOCK_TTL_SECONDS",
    "WORKER_HEARTBEAT_TTL_SECONDS",
    "PAGINATION_RESUME_DELAY_SECONDS",
    "X_AUTH_GATE_SECONDS",
    "LOGIN_RATE_LIMIT_ATTEMPTS",
    "LOGIN_RATE_LIMIT_WINDOW_SECONDS",
    "OPENAI_BASE_URL",
    "CODEX_BRIDGE_URL",
    "AI_ALLOWED_PROVIDER_HOSTS",
    "AI_WORKER_SCAN_INTERVAL_SECONDS",
    "AI_WORKER_MAX_CONCURRENCY",
    "AI_WORKER_BATCH_SIZE",
    "AI_WORKER_LOCK_TTL_SECONDS",
    "AI_WORKER_HEARTBEAT_TTL_SECONDS",
    "QQ_AUTH_URL",
    "QQ_API_BASE_URL",
    "QQ_REQUEST_TIMEOUT_SECONDS",
    "QQ_WORKER_SCAN_INTERVAL_SECONDS",
    "QQ_WORKER_MAX_CONCURRENCY",
    "QQ_WORKER_BATCH_SIZE",
    "QQ_WORKER_SEND_INTERVAL_SECONDS",
    "QQ_WORKER_MAX_ATTEMPTS",
    "QQ_WORKER_LOCK_TTL_SECONDS",
    "QQ_WORKER_HEARTBEAT_TTL_SECONDS",
    "XHS_JOB_TIMEOUT_SECONDS",
    "XHS_JOB_RESULT_TTL_SECONDS",
    "XHS_BROWSER_POOL_SIZE",
    "CAMOUFOX_SERVICE_NAME",
    "CAMOUFOX_BROWSER_POOL_SIZE",
    "CAMOUFOX_MAX_CONCURRENCY",
    "CAMOUFOX_JOB_TIMEOUT_SECONDS",
    "CAMOUFOX_JOB_RESULT_TTL_SECONDS",
    "TWEET_SCREENSHOT_ENABLED",
    "TWEET_SCREENSHOT_MAX_ATTEMPTS",
    "TWEET_SCREENSHOT_RETRY_SECONDS",
    "TWEET_SCREENSHOT_SCAN_INTERVAL_SECONDS",
    "XHS_BROWSER_MAX_CONCURRENCY",
    "XHS_WORKER_HEARTBEAT_TTL_SECONDS",
    "CORS_ORIGINS",
    "SERVICE_AUTH_URL",
    "MONITOR_CENTER_URL",
    "AUTO_X_DATA_ADDRESS",
}

# ``--set`` remains narrow because it is an optional operator escape hatch;
# normal installation seeds all application values from the local generated
# defaults and publishes them to Nacos automatically.
EXPLICIT_OVERRIDE_KEYS = {
    "POSTGRES_HOST",
    "REDIS_HOST",
    "CORS_ORIGINS",
}

CONTROL_PLANE_KEYS = {
    "SERVICE_AUTH_PRIVATE_KEY_PEM",
    "SERVICE_AUTH_PUBLIC_KEY_PEM",
    "SERVICE_AUTH_CLIENTS_JSON",
    "SERVICE_CLIENT_BACKEND_SECRET",
    "SERVICE_CLIENT_MONITOR_SECRET",
    "SERVICE_CLIENT_AGENT_SECRET",
    "SERVICE_CLIENT_XHS_WORKER_SECRET",
    "SERVICE_CLIENT_SCREENSHOT_WORKER_SECRET",
    "SERVICE_CLIENT_RUNTIME_LOGS_SECRET",
}
RUNTIME_CONFIG_KEYS.update(CONTROL_PLANE_KEYS)

# Compose must know values before it can create containers.  The installer
# writes the complete effective Nacos document to the local dotenv bootstrap
# cache; application Settings still load the same document from Nacos first.
# Control-plane files are materialized separately because PEM/JSON payloads
# are mounted as files by the services rather than interpolated by Compose.
BOOTSTRAP_CACHE_KEYS = (RUNTIME_CONFIG_KEYS - CONTROL_PLANE_KEYS) | {
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DATABASE",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "REDIS_HOST",
    "REDIS_PORT",
    "REDIS_DB",
    "REDIS_PASSWORD",
    "REDIS_MAXMEMORY",
}


def parse_env(path: Path, *, include_excluded: bool = False) -> dict[str, object]:
    values: dict[str, object] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = canonical_key(key)
        if not key or (key in EXCLUDED_KEYS and not include_excluded):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == "'":
            # Docker Compose treats single-quoted dotenv values literally.
            # Decode only the two escapes that its dotenv syntax permits so
            # credentials written by ``write_bootstrap_cache`` round-trip.
            raw = value[1:-1]
            decoded: list[str] = []
            index = 0
            while index < len(raw):
                if raw[index] == "\\" and index + 1 < len(raw) and raw[index + 1] in "\\'":
                    index += 1
                decoded.append(raw[index])
                index += 1
            value = "".join(decoded)
        elif len(value) >= 2 and value[0] == value[-1] == '"':
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = value[1:-1]
        # Keep JSON arrays/objects as native values; Pydantic can parse scalar
        # strings and numeric strings itself, and preserving them avoids
        # accidentally converting passwords such as "00123".
        if value[:1] in "[{":
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        values[key] = value
    return values


def _dotenv_value(value: object) -> str:
    """Serialize a value safely for a Docker Compose dotenv file."""

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = str(value)
    if any(character in text for character in "\x00\r\n"):
        raise ValueError("Nacos 数据服务引导值不能包含换行符或 NUL")
    # Keep common host/port/password values readable, but quote whitespace,
    # comment characters and shell metacharacters so a remote secret cannot
    # corrupt subsequent dotenv parsing.
    if text and all(char.isalnum() or char in "._-/:@+%" for char in text):
        return text
    # Single quotes suppress Compose's ``$VAR`` interpolation.  Escape only
    # backslash and quote according to the Compose dotenv grammar, and mirror
    # that decoding in ``parse_env`` above.
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _dotenv_key(line: str) -> str | None:
    stripped = line.strip()
    if stripped.startswith("export "):
        stripped = stripped[7:].lstrip()
    if "=" not in stripped or stripped.startswith("#"):
        return None
    key = canonical_key(stripped.split("=", 1)[0])
    return key or None


def write_bootstrap_cache(path: Path, values: Mapping[str, object], *, keys: set[str] | None = None) -> int:
    """Atomically update data-service values in ``path``.

    Only keys in :data:`BOOTSTRAP_CACHE_KEYS` are considered.  Existing
    comments and unrelated deployment settings remain untouched. The resulting
    file is owner-only (``0600`` when writable, ``0400`` for an already
    read-only source) because it contains credentials.
    """

    updates = {
        key: values[key]
        for key in (BOOTSTRAP_CACHE_KEYS if keys is None else keys)
        if key in values and values[key] is not None
    }
    if not updates:
        return 0
    # The cache contains database/Redis credentials.  Installer-created files
    # are already 0600, but ``make nacos-config`` can also be run against a
    # copied .env.example (usually 0644).  Never widen or preserve group/world
    # access while replacing the file.
    original_mode = path.stat().st_mode & 0o777
    # Preserve whether the owner could write the source while dropping every
    # group/world bit.  In particular, do not silently turn a deliberately
    # read-only 0400 file into a writable 0600 file.
    owner_mode = original_mode & 0o600
    secure_mode = 0o600 if owner_mode & 0o200 else 0o400
    lines = path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        key = _dotenv_key(line)
        if key in updates:
            output.append(f"{key}={_dotenv_value(updates[key])}")
            seen.add(key)
        else:
            output.append(line)
    for key in sorted(set(updates) - seen):
        output.append(f"{key}={_dotenv_value(updates[key])}")

    payload = "\n".join(output) + "\n"
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as temporary:
        temporary.write(payload)
        temporary_path = Path(temporary.name)
    try:
        os.chmod(temporary_path, secure_mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return len(updates)


def ensure_camoufox_defaults(values: dict) -> None:
    """Add new browser settings during upgrades without replacing Nacos values."""
    defaults = {
        "CAMOUFOX_SERVICE_NAME": "xsentinel-camoufox-worker",
        "CAMOUFOX_BROWSER_POOL_SIZE": 1,
        "CAMOUFOX_MAX_CONCURRENCY": 1,
        "CAMOUFOX_JOB_TIMEOUT_SECONDS": 290,
        "CAMOUFOX_JOB_RESULT_TTL_SECONDS": 600,
    }
    for key, value in defaults.items():
        values.setdefault(key, value)


def ensure_camoufox_caller(values: dict) -> None:
    """Seed only the new XHS identity; preserve existing authority/grant choices."""
    import hashlib
    import secrets

    raw = values.get("SERVICE_AUTH_CLIENTS_JSON")
    if not raw:
        return
    clients = json.loads(str(raw))
    secret = str(values.get("SERVICE_CLIENT_XHS_WORKER_SECRET") or "")
    if "xhs-worker" not in clients:
        secret = secret or secrets.token_hex(32)
        clients["xhs-worker"] = {
            "secret_sha256": hashlib.sha256(secret.encode()).hexdigest(),
            "grants": {"camoufox-worker": "browser:execute"},
        }
        values["SERVICE_AUTH_CLIENTS_JSON"] = json.dumps(clients, ensure_ascii=False)
        values["SERVICE_CLIENT_XHS_WORKER_SECRET"] = secret
    elif not secret or clients["xhs-worker"]["secret_sha256"] != hashlib.sha256(secret.encode()).hexdigest():
        raise RuntimeError("Nacos 中 xhs-worker 服务凭据缺失或不匹配，请恢复原服务密钥")


def ensure_screenshot_defaults(values: dict) -> None:
    """Seed screenshot tuning without changing existing operator choices."""
    defaults = {
        "TWEET_SCREENSHOT_ENABLED": True,
        "TWEET_SCREENSHOT_MAX_ATTEMPTS": 3,
        "TWEET_SCREENSHOT_RETRY_SECONDS": 30,
        "TWEET_SCREENSHOT_SCAN_INTERVAL_SECONDS": 5,
    }
    for key, value in defaults.items():
        values.setdefault(key, value)


def ensure_screenshot_caller(values: dict) -> None:
    """Seed a new caller; never restore a revoked grant on an existing one."""
    import hashlib
    import secrets

    raw = values.get("SERVICE_AUTH_CLIENTS_JSON")
    if not raw:
        return
    clients = json.loads(str(raw))
    secret = str(values.get("SERVICE_CLIENT_SCREENSHOT_WORKER_SECRET") or "")
    if "screenshot-worker" not in clients:
        secret = secret or secrets.token_hex(32)
        clients["screenshot-worker"] = {
            "secret_sha256": hashlib.sha256(secret.encode()).hexdigest(),
            "grants": {"camoufox-worker": "browser:execute"},
        }
        values["SERVICE_AUTH_CLIENTS_JSON"] = json.dumps(clients, ensure_ascii=False)
        values["SERVICE_CLIENT_SCREENSHOT_WORKER_SECRET"] = secret
    elif not secret or clients["screenshot-worker"]["secret_sha256"] != hashlib.sha256(secret.encode()).hexdigest():
        raise RuntimeError("Nacos 中 screenshot-worker 服务凭据缺失或不匹配，请恢复原服务密钥")


def ensure_runtime_logs_caller(values: dict) -> None:
    """Seed a read-only caller without changing existing identities or revocations."""
    import hashlib
    import secrets

    raw = values.get("SERVICE_AUTH_CLIENTS_JSON")
    if not raw:
        return
    clients = json.loads(str(raw))
    secret = str(values.get("SERVICE_CLIENT_RUNTIME_LOGS_SECRET") or "")
    if "runtime-logs" not in clients:
        secret = secret or secrets.token_hex(32)
        clients["runtime-logs"] = {
            "secret_sha256": hashlib.sha256(secret.encode()).hexdigest(),
            "grants": {"xhs-worker": "logs:read", "camoufox-worker": "logs:read"},
        }
        values["SERVICE_AUTH_CLIENTS_JSON"] = json.dumps(clients, ensure_ascii=False)
        values["SERVICE_CLIENT_RUNTIME_LOGS_SECRET"] = secret
    elif not secret or clients["runtime-logs"]["secret_sha256"] != hashlib.sha256(secret.encode()).hexdigest():
        raise RuntimeError("Nacos 中 runtime-logs 服务凭据缺失或不匹配，请恢复原服务密钥")


def read_control_plane_values(path: Path) -> dict[str, str]:
    names = {
        "private.pem": "SERVICE_AUTH_PRIVATE_KEY_PEM",
        "public.pem": "SERVICE_AUTH_PUBLIC_KEY_PEM",
        "clients.json": "SERVICE_AUTH_CLIENTS_JSON",
        "backend.secret": "SERVICE_CLIENT_BACKEND_SECRET",
        "monitor.secret": "SERVICE_CLIENT_MONITOR_SECRET",
        "agent.secret": "SERVICE_CLIENT_AGENT_SECRET",
        "xhs-worker.secret": "SERVICE_CLIENT_XHS_WORKER_SECRET",
        "screenshot-worker.secret": "SERVICE_CLIENT_SCREENSHOT_WORKER_SECRET",
        "runtime-logs.secret": "SERVICE_CLIENT_RUNTIME_LOGS_SECRET",
    }
    values: dict[str, str] = {}
    for filename, key in names.items():
        candidate = path / filename
        if not candidate.is_file():
            continue
        value = candidate.read_text(encoding="utf-8").strip()
        if value and (filename not in {"private.pem", "public.pem"} or "BEGIN " in value):
            values[key] = value
    return values


def write_control_plane_values(path: Path, values: Mapping[str, object]) -> int:
    names = {
        "SERVICE_AUTH_PRIVATE_KEY_PEM": "private.pem",
        "SERVICE_AUTH_PUBLIC_KEY_PEM": "public.pem",
        "SERVICE_AUTH_CLIENTS_JSON": "clients.json",
        "SERVICE_CLIENT_BACKEND_SECRET": "backend.secret",
        "SERVICE_CLIENT_MONITOR_SECRET": "monitor.secret",
        "SERVICE_CLIENT_AGENT_SECRET": "agent.secret",
        "SERVICE_CLIENT_XHS_WORKER_SECRET": "xhs-worker.secret",
        "SERVICE_CLIENT_SCREENSHOT_WORKER_SECRET": "screenshot-worker.secret",
        "SERVICE_CLIENT_RUNTIME_LOGS_SECRET": "runtime-logs.secret",
    }
    written = 0
    path.mkdir(parents=True, exist_ok=True)
    for key, filename in names.items():
        value = values.get(key)
        if value in (None, ""):
            continue
        target = path / filename
        target.write_text(str(value).rstrip() + "\n", encoding="utf-8")
        os.chmod(target, 0o600)
        written += 1
    return written


def canonical_key(value: object) -> str:
    key = str(value).strip().replace("-", "_").replace(".", "_").upper()
    if key == "POSTGRESQL":
        return "POSTGRES"
    if key.startswith("POSTGRESQL_"):
        return "POSTGRES_" + key[len("POSTGRESQL_") :]
    if key == "POSTGRES_DB":
        return "POSTGRES_DATABASE"
    if key == "TZ":
        return "APP_TIMEZONE"
    return key


def parse_explicit_overrides(raw_values: list[str] | None) -> dict[str, object]:
    """Parse the limited ``--set KEY=VALUE`` operator overrides.

    The command-line form is deliberately not a general dotenv editor.  An
    allow-list prevents accidentally putting credentials, Nacos bootstrap
    values, or process-local deployment settings into a shared config item.
    ``CORS_ORIGINS`` accepts a JSON array (the native Nacos representation) or
    a comma-separated list for shell-friendly invocations.
    """

    overrides: dict[str, object] = {}
    for raw in raw_values or []:
        if "=" not in raw:
            raise RuntimeError("--set 必须使用 KEY=VALUE 格式")
        raw_key, raw_value = raw.split("=", 1)
        key = canonical_key(raw_key)
        value = raw_value.strip()
        if key not in EXPLICIT_OVERRIDE_KEYS:
            raise RuntimeError(
                f"--set 不允许覆盖 {key or raw_key!r}；仅支持 "
                "POSTGRES_HOST、REDIS_HOST、CORS_ORIGINS"
            )
        if not value or any(character in value for character in "\x00\r\n"):
            raise RuntimeError(f"--set {key} 的值不能为空或包含换行符")
        if key == "CORS_ORIGINS":
            try:
                parsed: object = json.loads(value)
            except json.JSONDecodeError:
                # A value that looks like JSON should fail loudly rather than
                # silently becoming one malformed origin.  Plain comma
                # separated values remain convenient for shell callers.
                if value[:1] in "[{":
                    raise RuntimeError(
                        "--set CORS_ORIGINS 的 JSON 值格式无效"
                    ) from None
                parsed = [item.strip() for item in value.split(",") if item.strip()]
            if isinstance(parsed, str):
                parsed = [parsed]
            if not isinstance(parsed, list) or not parsed or not all(
                isinstance(item, str) and item.strip() for item in parsed
            ):
                raise RuntimeError(
                    "--set CORS_ORIGINS 必须是非空 JSON 字符串数组或逗号分隔列表"
                )
            overrides[key] = [item.strip() for item in parsed]
        else:
            overrides[key] = value
    return overrides


def positive_timeout(value: object, *, default: float = 5.0) -> float:
    """Return a positive request timeout with a concise operator error."""

    raw = default if value in (None, "") else value
    try:
        timeout = float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("NACOS_CONFIG_TIMEOUT_SECONDS 必须是正数") from exc
    if not 0 < timeout <= 30:
        raise RuntimeError("NACOS_CONFIG_TIMEOUT_SECONDS 必须是 0 到 30 秒之间的正数")
    return timeout


def validate_production_config(values: Mapping[str, object]) -> None:
    """Reject a Nacos document that cannot satisfy production Settings."""

    def text(key: str) -> str:
        value = values.get(key, "")
        return str(value).strip() if value is not None else ""

    def placeholder(value: str) -> bool:
        return value.lower().startswith(
            ("change-me-", "development-only-", "replace-with-")
        )

    errors: list[str] = []
    jwt_secret = text("JWT_SECRET_KEY")
    if len(jwt_secret) < 32 or placeholder(jwt_secret):
        errors.append("JWT_SECRET_KEY 必须是至少 32 字符的非占位值")

    encryption_key = text("X_TOKEN_ENCRYPTION_KEY")
    if len(encryption_key) < 32 or placeholder(encryption_key):
        errors.append("X_TOKEN_ENCRYPTION_KEY 必须是至少 32 字符的非占位值")

    postgres_password = text("POSTGRES_PASSWORD")
    postgres_dsn = text("POSTGRES_DSN")
    if postgres_dsn:
        postgres_password = unquote(urlsplit(postgres_dsn).password or "")
    if not postgres_password or postgres_password == "xsentinel" or placeholder(postgres_password):
        errors.append("PostgreSQL 密码必须是非空、非占位值")

    redis_password = text("REDIS_PASSWORD")
    redis_url = text("REDIS_URL")
    if redis_url:
        redis_password = unquote(urlsplit(redis_url).password or "")
    if redis_password and placeholder(redis_password):
        errors.append("REDIS_PASSWORD 不能使用占位值")

    cors_origins = values.get("CORS_ORIGINS")
    if cors_origins in (None, "", [], ()):
        errors.append("CORS_ORIGINS 不能为空")

    if errors:
        raise RuntimeError("生产 Nacos Config 校验失败: " + "; ".join(errors))


def flatten(value: Mapping[str, object], prefix: str = "") -> dict[str, object]:
    result: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        key = canonical_key(raw_key)
        # Keep the shell/bootstrap representation aligned with Settings, which
        # accepts both ``postgres`` and the common ``postgresql`` spelling.
        # Without this normalization a hand-written nested ``postgresql``
        # document would be flattened to keys that the application ignores.
        if not prefix and key == "POSTGRESQL":
            key = "POSTGRES"
        # Canonicalize once more after joining the prefix so aliases such as
        # {"postgres": {"db": "sentinel"}} become POSTGRES_DATABASE rather
        # than an otherwise valid-looking key that the allow-list would drop.
        full = canonical_key(f"{prefix}_{key}" if prefix else key)
        if isinstance(raw_value, Mapping):
            result.update(flatten(raw_value, full))
        else:
            result[full] = raw_value
    return result


def request_json(
    method: str,
    url: str,
    *,
    params: Mapping[str, str] | None = None,
    form: Mapping[str, str] | None = None,
    timeout: float,
) -> tuple[int, str]:
    if params:
        url = f"{url}?{urlencode(params)}"
    body = urlencode(form).encode() if form is not None else None
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except URLError as exc:
        raise RuntimeError(f"无法连接 Nacos: {exc.reason}") from exc


def load_remote(
    server: str,
    *,
    namespace: str,
    group: str,
    data_id: str,
    username: str,
    password: str,
    timeout: float,
) -> tuple[dict[str, object], str]:
    token = ""
    if username:
        status, body = request_json(
            "POST",
            f"{server}/nacos/v1/auth/login",
            form={"username": username, "password": password},
            timeout=timeout,
        )
        if status >= 400:
            raise RuntimeError(f"Nacos 登录失败（HTTP {status}）")
        try:
            token = str(json.loads(body)["accessToken"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Nacos 登录响应无有效 accessToken") from exc
    params = {"dataId": data_id, "group": group}
    if namespace and namespace.lower() != "public":
        params["tenant"] = namespace
    if token:
        params["accessToken"] = token
    status, body = request_json(
        "GET", f"{server}/nacos/v1/cs/configs", params=params, timeout=timeout
    )
    if status == 404 or not body.strip():
        return {}, token
    if status >= 400:
        raise RuntimeError(f"读取 Nacos Config 失败（HTTP {status}）")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Nacos Config 不是有效 JSON") from exc
    if not isinstance(payload, Mapping):
        raise TypeError("Nacos Config 必须是 JSON 对象")
    flattened = flatten(payload)
    return {
        key: value
        for key, value in flattened.items()
        if key in RUNTIME_CONFIG_KEYS and key not in EXCLUDED_KEYS
    }, token


def load_json_document(
    server: str, *, namespace: str, group: str, data_id: str, token: str, timeout: float
) -> dict | None:
    params = {"dataId": data_id, "group": group}
    if namespace and namespace.lower() != "public":
        params["tenant"] = namespace
    if token:
        params["accessToken"] = token
    status, body = request_json(
        "GET", f"{server}/nacos/v1/cs/configs", params=params, timeout=timeout
    )
    if status == 404 or not body.strip():
        return None
    if status >= 400:
        raise RuntimeError(f"读取 Nacos Config {data_id} 失败（HTTP {status}）")
    try:
        document = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Nacos Config {data_id} 不是有效 JSON") from exc
    if not isinstance(document, dict):
        raise RuntimeError(f"Nacos Config {data_id} 必须是 JSON 对象")
    return document


def validate_monitor_topology(document: dict) -> None:
    nodes = document.get("nodes")
    services = document.get("services")
    if not isinstance(nodes, dict) or not nodes or not isinstance(services, list):
        raise RuntimeError("监控拓扑必须包含非空 nodes 对象和 services 数组")
    ids = set()
    selectors = set()
    for service in services:
        if not isinstance(service, dict) or not all(
            isinstance(service.get(key), str) and service[key]
            for key in ("id", "name", "component", "node", "project", "container_service")
        ):
            raise RuntimeError("监控拓扑中的服务字段不完整")
        if service["node"] not in nodes or service["id"] in ids:
            raise RuntimeError("监控拓扑服务节点不存在或 id 重复")
        selector = (service["node"], service["project"], service["container_service"])
        if selector in selectors:
            raise RuntimeError("监控拓扑容器选择器重复")
        ids.add(service["id"])
        selectors.add(selector)


def monitor_seed_topology(node_id: str, control_dir: Path, seed_path: Path | None = None) -> dict:
    root = Path(__file__).resolve().parents[1] / "microservices"
    topology_path = seed_path or (
        root / "services.tc-dual.json"
        if node_id in {"hn-1", "tc-2"}
        else control_dir / "services.json"
    )
    topology = json.loads(topology_path.read_text(encoding="utf-8"))
    validate_monitor_topology(topology)
    return topology


def sync_monitor_documents(
    server: str, *, namespace: str, group: str, token: str, timeout: float,
    local_env: Mapping[str, str], control_dir: Path, seed_node_id: str,
    seed_topology_path: Path | None = None,
    selected_services: str = "",
) -> None:
    topology_id = str(local_env.get("NACOS_MONITOR_TOPOLOGY_DATA_ID") or "x-sentinel-monitor-topology.json")
    nodes_id = str(local_env.get("NACOS_MONITOR_NODES_DATA_ID") or "x-sentinel-monitor-nodes.json")
    address = str(local_env.get("NACOS_ADVERTISE_IP", "")).strip()
    if not address:
        raise RuntimeError("无法管理监控节点映射：NACOS_ADVERTISE_IP 为空")
    topology = load_json_document(server, namespace=namespace, group=group,
                                  data_id=topology_id, token=token, timeout=timeout)
    if topology is None:
        seed_node_id = seed_node_id or "local"
        topology = monitor_seed_topology(seed_node_id, control_dir, seed_topology_path)
        if seed_node_id not in topology["nodes"]:
            raise RuntimeError(
                f"节点 {seed_node_id} 不在初始监控拓扑中；请用 KJ_AUTO_X_TOPOLOGY_FILE 指定包含该节点的文件"
            )
        publish(server, namespace=namespace, group=group, data_id=topology_id,
                content=topology, token=token, timeout=timeout)
    validate_monitor_topology(topology)
    nodes = load_json_document(server, namespace=namespace, group=group,
                               data_id=nodes_id, token=token, timeout=timeout)
    if nodes is None:
        nodes = {"nodes": {}}
    entries = nodes.get("nodes")
    if not isinstance(entries, dict):
        raise RuntimeError(f"Nacos Config {nodes_id} 缺少 nodes 对象")
    if seed_node_id:
        if seed_node_id not in topology["nodes"]:
            raise RuntimeError(f"节点 {seed_node_id} 不在 Nacos 监控拓扑中")
        occupied = [name for name, item in entries.items() if name != seed_node_id
                    and isinstance(item, dict) and item.get("advertise_ip") == address]
        if occupied:
            raise RuntimeError(f"地址 {address} 已绑定监控节点 {occupied[0]}")
        if seed_node_id in entries and entries[seed_node_id] != {"advertise_ip": address}:
            raise RuntimeError(
                f"Nacos 节点 {seed_node_id} 已绑定其他地址；请先在 {nodes_id} 中更新"
            )
        if seed_node_id not in entries:
            entries[seed_node_id] = {"advertise_ip": address}
            publish(server, namespace=namespace, group=group, data_id=nodes_id,
                    content=nodes, token=token, timeout=timeout)
    matches = [name for name, item in entries.items() if isinstance(item, dict)
               and item.get("advertise_ip") == address]
    if len(matches) != 1 or matches[0] not in topology["nodes"]:
        raise RuntimeError(f"Nacos 监控节点映射未将 {address!r} 唯一绑定到拓扑节点；首次安装需指定 KJ_AUTO_X_MONITOR_NODE_ID")
    # Existing cluster topology is authoritative. Add only the new browser
    # owner when this installer run actually deploys it on the resolved node.
    node_id = matches[0]
    browser_selected = "camoufox-worker" in {
        service.strip() for service in selected_services.split(",")
    }
    browser_monitored = any(
        service["node"] == node_id
        and service["project"] == "x-sentinel"
        and service["container_service"] == "camoufox-worker"
        for service in topology["services"]
    )
    if browser_selected and not browser_monitored:
        service_id = (
            "camoufox-worker" if node_id == "local"
            else f"{node_id.replace('-', '')}-camoufox-worker"
        )
        if any(service["id"] == service_id for service in topology["services"]):
            raise RuntimeError(f"Nacos 监控拓扑服务 id {service_id} 已被其他实例占用")
        port = int(local_env.get("CAMOUFOX_WORKER_HOST_PORT") or 8007)
        if not 1 <= port <= 65535:
            raise RuntimeError("CAMOUFOX_WORKER_HOST_PORT 必须是有效 TCP 端口")
        topology["services"].append({
            "id": service_id, "name": "Camoufox Worker", "component": "camoufox_worker",
            "node": node_id, "project": "x-sentinel", "container_service": "camoufox-worker",
            "port": port,
        })
        validate_monitor_topology(topology)
        publish(server, namespace=namespace, group=group, data_id=topology_id,
                content=topology, token=token, timeout=timeout)
    print(f"Nacos 监控配置已同步: {topology_id}, {nodes_id}（本机节点 {matches[0]}）")


def publish(
    server: str,
    *,
    namespace: str,
    group: str,
    data_id: str,
    content: Mapping[str, object],
    token: str,
    timeout: float,
) -> None:
    form = {
        "dataId": data_id,
        "group": group,
        "content": json.dumps(content, ensure_ascii=False, indent=2),
        "type": "json",
    }
    if namespace and namespace.lower() != "public":
        form["tenant"] = namespace
    if token:
        form["accessToken"] = token
    status, body = request_json(
        "POST", f"{server}/nacos/v1/cs/configs", form=form, timeout=timeout
    )
    if status >= 400 or body.strip().lower() != "true":
        raise RuntimeError(f"发布 Nacos Config 失败（HTTP {status}）")


def resolve_data_endpoints(merged: dict, local: Mapping[str, str]) -> None:
    """Publish owner endpoints; consumers must never seed Docker-only names."""
    managed = str(local.get("AUTO_X_MANAGE_DATA", "")).lower()
    if managed not in {"true", "false"}:
        return  # Legacy non-installer callers retain their existing contract.
    if managed == "true":
        address = local.get("NACOS_ADVERTISE_IP", "")
        if not address:
            raise RuntimeError("数据节点缺少 NACOS_ADVERTISE_IP")
        previous = merged.get("AUTO_X_DATA_ADDRESS")
        for prefix, alias, port in (("POSTGRES", "postgres", "5432"), ("REDIS", "redis", "6379")):
            host = merged.get(f"{prefix}_HOST")
            if host not in (None, "", alias, previous, address):
                raise RuntimeError(f"Nacos 已存在另一数据节点 {prefix}_HOST={host}，拒绝覆盖")
            merged[f"{prefix}_HOST"] = address
            merged[f"{prefix}_PORT"] = local.get(f"{prefix}_HOST_PORT", port)
        merged["AUTO_X_DATA_ADDRESS"] = address
        merged.update(POSTGRES_DSN="", REDIS_URL="")
    else:
        for prefix, alias in (("POSTGRES", "postgres"), ("REDIS", "redis")):
            if merged.get(f"{prefix}_HOST") in (None, "", alias, "localhost", "127.0.0.1"):
                raise RuntimeError(f"Nacos 缺少可跨节点访问的 {prefix}_HOST，请先安装数据节点")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--server")
    parser.add_argument("--namespace")
    parser.add_argument("--group")
    parser.add_argument("--data-id")
    parser.add_argument("--control-plane-dir", type=Path)
    parser.add_argument("--sync-monitor", action="store_true")
    parser.add_argument("--monitor-node-id", default="")
    parser.add_argument("--monitor-topology-file", type=Path)
    parser.add_argument("--monitor-services", default="",
                        help="selected node services; add missing Camoufox monitoring on upgrade")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--timeout", type=float)
    parser.add_argument(
        "--if-required",
        action="store_true",
        help="skip synchronization unless NACOS_CONFIG_REQUIRED=true",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="validate the effective document as production configuration before publishing",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify Nacos connectivity, authentication, and config readability without publishing",
    )
    parser.add_argument(
        "--set",
        dest="explicit_overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "explicitly override one non-secret runtime coordinate; repeatable "
            "(POSTGRES_HOST, REDIS_HOST, or CORS_ORIGINS)"
        ),
    )
    # Enabled by default: a local Compose database must use the same
    # PostgreSQL/Redis coordinates that Nacos made authoritative.  The
    # explicit opt-out is useful for operators who run only external data
    # services and do not want the installer to touch their dotenv file.
    parser.add_argument(
        "--write-bootstrap",
        dest="write_bootstrap",
        action="store_true",
        default=True,
        help="write authoritative PostgreSQL/Redis coordinates to the local dotenv cache (default)",
    )
    parser.add_argument(
        "--no-write-bootstrap",
        dest="write_bootstrap",
        action="store_false",
        help="do not update PostgreSQL/Redis values in the local dotenv cache",
    )
    args = parser.parse_args()
    # Parse before contacting Nacos so malformed or unsafe operator input fails
    # deterministically.  ``--check`` still returns before this mapping can be
    # published or written to the local bootstrap cache.
    explicit_overrides = parse_explicit_overrides(args.explicit_overrides)
    local_env = parse_env(args.env_file, include_excluded=True)
    if args.if_required and str(local_env.get("NACOS_CONFIG_REQUIRED", "")).strip().lower() != "true":
        print("Nacos Config 未设为 required，跳过自动同步")
        return 0
    # Read bootstrap credentials from the protected dotenv file by default so
    # they do not appear in the process list. CLI values remain useful for
    # automation that injects secrets through a wrapper.
    server = str(args.server or local_env.get("NACOS_SERVER_ADDR", "")).strip().rstrip("/")
    namespace = str(args.namespace or local_env.get("NACOS_NAMESPACE") or "public").strip()
    group = (
        args.group
        or local_env.get("NACOS_CONFIG_GROUP")
        or local_env.get("NACOS_GROUP")
        or "X_SENTINEL"
    )
    data_id = str(
        args.data_id or local_env.get("NACOS_CONFIG_DATA_ID") or "x-sentinel-config.json"
    ).strip()
    username = str(
        args.username if args.username is not None else local_env.get("NACOS_USERNAME", "")
    )
    password = str(
        args.password if args.password is not None else local_env.get("NACOS_PASSWORD", "")
    )
    timeout = positive_timeout(
        args.timeout
        if args.timeout is not None
        else local_env.get("NACOS_CONFIG_TIMEOUT_SECONDS")
    )
    if not server:
        raise RuntimeError("NACOS_SERVER_ADDR 未配置")
    if server.lower().endswith("/nacos"):
        server = server[:-6].rstrip("/")
    remote, token = load_remote(
        server,
        namespace=namespace,
        group=group,
        data_id=data_id,
        username=username,
        password=password,
        timeout=timeout,
    )
    if args.check:
        print(f"Nacos 连接及认证验证成功: {data_id}")
        return 0
    # Older installer versions could accidentally publish bootstrap or
    # per-container keys.  Remove those keys from the effective document on
    # the next sync so the Config item remains a runtime-only contract.
    remote = {key: value for key, value in remote.items() if key not in EXCLUDED_KEYS}
    local = {
        key: value
        for key, value in parse_env(args.env_file).items()
        if key in RUNTIME_CONFIG_KEYS and key not in EXCLUDED_KEYS
    }
    if args.control_plane_dir:
        local.update(read_control_plane_values(args.control_plane_dir))
    # Existing Nacos values win; local values only seed missing keys.
    merged = dict(local)
    merged.update(remote)
    ensure_camoufox_defaults(merged)
    ensure_camoufox_caller(merged)
    ensure_screenshot_defaults(merged)
    ensure_screenshot_caller(merged)
    ensure_runtime_logs_caller(merged)
    # Nacos remains authoritative by default.  An explicit command-line
    # override is the only supported way to supersede an existing remote value,
    # and it is applied before production validation and publication.
    merged.update(explicit_overrides)
    resolve_data_endpoints(merged, local_env)
    if args.production or str(local_env.get("ENVIRONMENT", "")).strip().lower() == "production":
        validate_production_config(merged)
    if args.sync_monitor:
        if not args.control_plane_dir:
            raise RuntimeError("同步监控配置需要 --control-plane-dir")
        sync_monitor_documents(
            server, namespace=namespace, group=group, token=token, timeout=timeout,
            local_env=local_env, control_dir=args.control_plane_dir,
            seed_node_id=args.monitor_node_id,
            seed_topology_path=args.monitor_topology_file,
            selected_services=args.monitor_services,
        )
    publish(
        server,
        namespace=namespace,
        group=group,
        data_id=data_id,
        content=merged,
        token=token,
        timeout=timeout,
    )
    # Use the effective document rather than ``remote`` alone.  This also
    # normalizes newly seeded local values and leaves the cache correct when
    # Nacos returned an empty document on first install.
    cache = dict(merged)
    if str(local_env.get("AUTO_X_MANAGE_DATA", "")).lower() == "true":
        cache.update(POSTGRES_HOST="postgres", POSTGRES_PORT="5432",
                     REDIS_HOST="redis", REDIS_PORT="6379", POSTGRES_DSN="", REDIS_URL="")
    cached = write_bootstrap_cache(args.env_file, cache) if args.write_bootstrap else 0
    control_plane = (
        write_control_plane_values(args.control_plane_dir, merged)
        if args.control_plane_dir
        else 0
    )
    seeded = len(set(local) - set(remote))
    suffix = f"，已回写 {cached} 项本地数据服务引导值" if args.write_bootstrap else ""
    control_suffix = f"，控制面文件 {control_plane} 项" if args.control_plane_dir else ""
    print(f"Nacos Config 已同步: {data_id}（本地补充 {seeded} 项{suffix}{control_suffix}）")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
