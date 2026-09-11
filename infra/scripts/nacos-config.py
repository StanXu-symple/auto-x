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
    "ENVIRONMENT",
    "DEBUG",
    "STARTUP_STRICT",
    "AUTO_CREATE_TABLES",
    "SERVICE_AUTH_URL",
    "MONITOR_CENTER_URL",
    "SERVICE_AUTH_PRIVATE_KEY_FILE",
    "SERVICE_AUTH_CLIENTS_FILE",
    "SERVICE_TOPOLOGY_FILE",
    "SERVICE_CLIENT_SECRET_FILE",
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
    "GRAFANA_ADMIN_PASSWORD",
    "BACKUP_DIR",
    "SERVICE_NAME",
    "LOG_DIR",
    "HOME",
    "ARTICLE_UPLOAD_DIR",
    # Credentials used by the application itself are intentionally kept local,
    # except JWT/X-token keys which are shared runtime settings requested for
    # central management. Database and Redis credentials are also shared.
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "POSTGRES_EXPORTER_PASSWORD",
    "OPENAI_API_KEY",
    "CODEX_BRIDGE_API_KEY",
    "CODEX_BRIDGE_TOKEN",
}

# Publish only settings that the application or control plane actually knows
# how to consume.  An allow-list is safer than relying solely on
# ``EXCLUDED_KEYS``: old installations may still have MYSQL_* values or
# operator-specific secrets in their dotenv file, and those must never be
# copied into a shared Nacos document just because they are unknown to the
# current release.
RUNTIME_CONFIG_KEYS = {
    "APP_NAME",
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
    "SERVICE_AUTH_TOKEN_LIFETIME_SECONDS",
    "MONITOR_INTERVAL_SECONDS",
    "MONITOR_TIMEOUT_SECONDS",
    "MONITOR_CONCURRENCY",
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
    "XHS_BROWSER_MAX_CONCURRENCY",
    "XHS_WORKER_HEARTBEAT_TTL_SECONDS",
    "CORS_ORIGINS",
}

# Compose must know these values before it can create a local PostgreSQL or
# Redis container.  When an existing Nacos document is authoritative, the
# installer writes only these data-service coordinates back to its local dotenv
# file as a bootstrap cache.  Application processes still read the authoritative
# values from Nacos at startup.
BOOTSTRAP_CACHE_KEYS = {
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


def write_bootstrap_cache(path: Path, values: Mapping[str, object]) -> int:
    """Atomically update data-service values in ``path``.

    Only keys in :data:`BOOTSTRAP_CACHE_KEYS` are considered.  Existing
    comments and unrelated deployment settings remain untouched. The resulting
    file is owner-only (``0600`` when writable, ``0400`` for an already
    read-only source) because it contains credentials.
    """

    updates = {
        key: values[key]
        for key in BOOTSTRAP_CACHE_KEYS
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--server")
    parser.add_argument("--namespace")
    parser.add_argument("--group")
    parser.add_argument("--data-id")
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
    # Existing Nacos values win; local values only seed missing keys.
    merged = dict(local)
    merged.update(remote)
    if args.production or str(local_env.get("ENVIRONMENT", "")).strip().lower() == "production":
        validate_production_config(merged)
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
    cached = write_bootstrap_cache(args.env_file, merged) if args.write_bootstrap else 0
    seeded = len(set(local) - set(remote))
    suffix = f"，已回写 {cached} 项本地数据服务引导值" if args.write_bootstrap else ""
    print(f"Nacos Config 已同步: {data_id}（本地补充 {seeded} 项{suffix}）")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
