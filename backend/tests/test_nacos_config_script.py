from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType


def load_script() -> ModuleType:
    script = Path(__file__).parents[2] / "infra" / "scripts" / "nacos-config.py"
    spec = importlib.util.spec_from_file_location("nacos_config_script", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_write_bootstrap_cache_updates_complete_effective_runtime_config(tmp_path: Path) -> None:
    module = load_script()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# deployment identity\n"
        "NACOS_SERVER_ADDR=http://nacos:8848\n"
        "POSTGRES_PASSWORD=old-password\n"
        "REDIS_HOST=redis\n"
        "POSTGRES_DSN=postgresql+asyncpg://old/db\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    count = module.write_bootstrap_cache(
        env_file,
        {
            "POSTGRES_PASSWORD": "remote-password",
            "POSTGRES_HOST": "remote-db",
            "REDIS_HOST": "remote-cache",
            "POSTGRES_DSN": "postgresql+asyncpg://must-not-be-cached/db",
            "REDIS_URL": "redis://must-not-be-cached/0",
            "ADMIN_PASSWORD": "must-not-be-cached",
        },
    )

    assert count == 6
    assert env_file.stat().st_mode & 0o777 == 0o600
    assert env_file.read_text(encoding="utf-8") == (
        "# deployment identity\n"
        "NACOS_SERVER_ADDR=http://nacos:8848\n"
        "POSTGRES_PASSWORD=remote-password\n"
        "REDIS_HOST=remote-cache\n"
        "POSTGRES_DSN=postgresql+asyncpg://must-not-be-cached/db\n"
        "ADMIN_PASSWORD=must-not-be-cached\n"
        "POSTGRES_HOST=remote-db\n"
        "REDIS_URL=redis://must-not-be-cached/0\n"
    )


def test_write_bootstrap_cache_quotes_unsafe_remote_values(tmp_path: Path) -> None:
    module = load_script()
    env_file = tmp_path / ".env"
    env_file.write_text("REDIS_PASSWORD=old\n", encoding="utf-8")

    module.write_bootstrap_cache(env_file, {"REDIS_PASSWORD": "has space#and$dollar"})

    assert env_file.read_text(encoding="utf-8") == "REDIS_PASSWORD='has space#and$dollar'\n"
    assert module.parse_env(env_file)["REDIS_PASSWORD"] == "has space#and$dollar"


def test_control_plane_values_round_trip_without_dotenv_cache(tmp_path: Path) -> None:
    module = load_script()
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    (source / "private.pem").write_text(
        "-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----\n"
    )
    (source / "clients.json").write_text('{"backend": {"secret_sha256": "abc"}}\n')
    (source / "backend.secret").write_text("backend-secret\n")

    values = module.read_control_plane_values(source)
    assert values["SERVICE_AUTH_PRIVATE_KEY_PEM"].startswith("-----BEGIN")
    assert values["SERVICE_CLIENT_BACKEND_SECRET"] == "backend-secret"
    assert module.write_control_plane_values(target, values) == 3
    assert (target / "private.pem").read_text().startswith("-----BEGIN")
    assert (target / "clients.json").read_text().startswith("{\"backend\"")


def test_flatten_normalizes_nested_postgresql_alias() -> None:
    module = load_script()

    assert module.flatten(
        {"postgresql": {"host": "db", "port": 5432, "db": "sentinel"}}
    ) == {
        "POSTGRES_HOST": "db",
        "POSTGRES_PORT": 5432,
        "POSTGRES_DATABASE": "sentinel",
    }


def test_parse_env_normalizes_common_aliases(tmp_path: Path) -> None:
    module = load_script()
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_DB=sentinel\nPOSTGRESQL_HOST=db\nTZ=UTC\n", encoding="utf-8")

    assert module.parse_env(env_file) == {
        "POSTGRES_DATABASE": "sentinel",
        "POSTGRES_HOST": "db",
        "APP_TIMEZONE": "UTC",
    }


def test_positive_timeout_rejects_invalid_values() -> None:
    module = load_script()

    assert module.positive_timeout("2.5") == 2.5
    assert module.positive_timeout("") == 5.0
    for value in ("not-a-number", "0", -1):
        try:
            module.positive_timeout(value)
        except RuntimeError as exc:
            assert "正数" in str(exc)
        else:  # pragma: no cover - defensive assertion
            raise AssertionError(f"timeout {value!r} should be rejected")


def test_monitor_documents_seed_once_and_merge_node_addresses(monkeypatch, tmp_path: Path) -> None:
    module = load_script()
    remote: dict[str, dict] = {}
    published: list[str] = []

    def fake_load(_server, *, data_id, **_kwargs):
        return remote.get(data_id)

    def fake_publish(_server, *, data_id, content, **_kwargs):
        import copy
        remote[data_id] = copy.deepcopy(content)
        published.append(data_id)

    monkeypatch.setattr(module, "load_json_document", fake_load)
    monkeypatch.setattr(module, "publish", fake_publish)
    common = dict(server="http://nacos", namespace="public", group="X_SENTINEL",
                  token="", timeout=3, control_dir=tmp_path)
    module.sync_monitor_documents(**common, local_env={"NACOS_ADVERTISE_IP": "203.0.113.10"},
                                  seed_node_id="tc-2")
    assert remote["x-sentinel-monitor-topology.json"]["services"][0]["node"] == "hn-1"
    assert remote["x-sentinel-monitor-nodes.json"]["nodes"] == {
        "tc-2": {"advertise_ip": "203.0.113.10"}
    }
    module.sync_monitor_documents(**common, local_env={"NACOS_ADVERTISE_IP": "203.0.113.11"},
                                  seed_node_id="hn-1")
    assert set(remote["x-sentinel-monitor-nodes.json"]["nodes"]) == {"hn-1", "tc-2"}
    assert published == ["x-sentinel-monitor-topology.json", "x-sentinel-monitor-nodes.json",
                         "x-sentinel-monitor-nodes.json"]
    module.sync_monitor_documents(**common, local_env={"NACOS_ADVERTISE_IP": "203.0.113.11"},
                                  seed_node_id="")
    assert len(published) == 3


def test_parse_explicit_overrides_accepts_only_non_secret_runtime_keys() -> None:
    module = load_script()

    assert module.parse_explicit_overrides(
        [
            "POSTGRES_HOST=remote-db",
            "REDIS_HOST=remote-cache",
            'CORS_ORIGINS=["https://console.example"]',
        ]
    ) == {
        "POSTGRES_HOST": "remote-db",
        "REDIS_HOST": "remote-cache",
        "CORS_ORIGINS": ["https://console.example"],
    }
    assert module.parse_explicit_overrides(["CORS_ORIGINS=https://a.example, https://b.example"])[
        "CORS_ORIGINS"
    ] == ["https://a.example", "https://b.example"]

    for raw in (
        "POSTGRES_PASSWORD=secret",
        "NACOS_SERVER_ADDR=http://attacker:8848",
        "SERVICE_AUTH_URL=http://auth:9100",
        "UNKNOWN_SETTING=value",
        "POSTGRES_HOST=",
        "CORS_ORIGINS=[",
        "not-a-key-value",
    ):
        try:
            module.parse_explicit_overrides([raw])
        except RuntimeError as exc:
            assert "--set" in str(exc)
        else:  # pragma: no cover - defensive assertion
            raise AssertionError(f"unsafe override {raw!r} should be rejected")


def test_explicit_overrides_win_over_nacos_and_are_bootstrap_cached(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_script()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "NACOS_SERVER_ADDR=http://nacos:8848\n"
        "POSTGRES_HOST=local-db\n"
        "REDIS_HOST=local-cache\n"
        "CORS_ORIGINS=[\"http://localhost:5173\"]\n",
        encoding="utf-8",
    )
    published: dict[str, object] = {}

    def fake_load_remote(*_args, **_kwargs):
        return {
            "POSTGRES_HOST": "nacos-db",
            "REDIS_HOST": "nacos-cache",
            "CORS_ORIGINS": ["https://nacos.example"],
        }, "token"

    def fake_publish(_server, **kwargs):
        published.update(kwargs["content"])

    monkeypatch.setattr(module, "load_remote", fake_load_remote)
    monkeypatch.setattr(module, "publish", fake_publish)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nacos-config.py",
            "--env-file",
            str(env_file),
            "--set",
            "POSTGRES_HOST=cli-db",
            "--set",
            "REDIS_HOST=cli-cache",
            "--set",
            'CORS_ORIGINS=["https://console.example"]',
        ],
    )

    assert module.main() == 0
    assert published["POSTGRES_HOST"] == "cli-db"
    assert published["REDIS_HOST"] == "cli-cache"
    assert published["CORS_ORIGINS"] == ["https://console.example"]
    cached = module.parse_env(env_file, include_excluded=True)
    assert cached["POSTGRES_HOST"] == "cli-db"
    assert cached["REDIS_HOST"] == "cli-cache"


def test_check_with_explicit_override_never_publishes_or_rewrites_env(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    module = load_script()
    env_file = tmp_path / ".env"
    original = (
        "NACOS_SERVER_ADDR=http://nacos:8848\n"
        "NACOS_USERNAME=nacos\n"
        "NACOS_PASSWORD=secret\n"
        "POSTGRES_HOST=local-db\n"
    )
    env_file.write_text(original, encoding="utf-8")

    def fake_load_remote(*_args, **_kwargs):
        return {"POSTGRES_HOST": "nacos-db"}, "token"

    def unexpected_publish(*_args, **_kwargs):
        raise AssertionError("check mode must not publish Nacos config")

    monkeypatch.setattr(module, "load_remote", fake_load_remote)
    monkeypatch.setattr(module, "publish", unexpected_publish)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nacos-config.py",
            "--env-file",
            str(env_file),
            "--check",
            "--set",
            "POSTGRES_HOST=cli-db",
        ],
    )

    assert module.main() == 0
    assert env_file.read_text(encoding="utf-8") == original
    assert "Nacos 连接及认证验证成功" in capsys.readouterr().out


def test_deployment_network_group_is_not_published() -> None:
    module = load_script()
    assert "NETWORK_GROUPS" in module.EXCLUDED_KEYS
    assert "SERVICE_AUTH_KEY_ENCRYPTION_KEY" not in module.EXCLUDED_KEYS


def test_runtime_allow_list_drops_legacy_mysql_and_unknown_keys(tmp_path: Path) -> None:
    module = load_script()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "POSTGRES_HOST=db\nMYSQL_PASSWORD=legacy-secret\nCUSTOM_SECRET=do-not-publish\n",
        encoding="utf-8",
    )

    parsed = module.parse_env(env_file)
    filtered = {
        key: value
        for key, value in parsed.items()
        if key in module.RUNTIME_CONFIG_KEYS and key not in module.EXCLUDED_KEYS
    }

    assert filtered == {"POSTGRES_HOST": "db"}


def test_runtime_allow_list_covers_every_remote_capable_application_setting() -> None:
    from app.core.config import (
        _NACOS_BOOTSTRAP_FIELDS,
        _NACOS_LOCAL_ONLY_FIELDS,
        Settings,
    )

    module = load_script()
    eligible = {
        field_name.upper()
        for field_name in Settings.model_fields
        if field_name not in _NACOS_BOOTSTRAP_FIELDS
        and field_name not in _NACOS_LOCAL_ONLY_FIELDS
    }

    assert eligible <= module.RUNTIME_CONFIG_KEYS


def test_main_keeps_remote_values_authoritative_and_caches_data_bootstrap(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_script()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "NACOS_SERVER_ADDR=http://nacos:8848\n"
        "NACOS_USERNAME=nacos\n"
        "NACOS_PASSWORD=secret\n"
        "POSTGRES_HOST=local-db\n"
        "POSTGRES_PASSWORD=local-pw\n"
        "REDIS_HOST=local-cache\n"
        "WORKER_MAX_CONCURRENCY=5\n"
        "MYSQL_PASSWORD=legacy-secret\n",
        encoding="utf-8",
    )
    published: dict[str, object] = {}

    def fake_load_remote(*_args, **_kwargs):
        return {
            "POSTGRES_HOST": "remote-db",
            "REDIS_PASSWORD": "remote-redis-pw",
            "WORKER_MAX_CONCURRENCY": 12,
        }, "token"

    def fake_publish(_server, **kwargs):
        published.update(kwargs["content"])

    monkeypatch.setattr(module, "load_remote", fake_load_remote)
    monkeypatch.setattr(module, "publish", fake_publish)
    monkeypatch.setattr(sys, "argv", ["nacos-config.py", "--env-file", str(env_file)])

    assert module.main() == 0
    assert published["POSTGRES_HOST"] == "remote-db"
    assert published["POSTGRES_PASSWORD"] == "local-pw"
    assert published["REDIS_HOST"] == "local-cache"
    assert published["REDIS_PASSWORD"] == "remote-redis-pw"
    assert published["WORKER_MAX_CONCURRENCY"] == 12
    assert "NACOS_PASSWORD" not in published
    assert "MYSQL_PASSWORD" not in published
    cached = module.parse_env(env_file, include_excluded=True)
    assert cached["POSTGRES_HOST"] == "remote-db"
    assert cached["REDIS_PASSWORD"] == "remote-redis-pw"


def test_check_mode_reads_nacos_without_publishing_or_rewriting_env(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    module = load_script()
    env_file = tmp_path / ".env"
    original = (
        "NACOS_SERVER_ADDR=http://nacos:8848\n"
        "NACOS_USERNAME=nacos\n"
        "NACOS_PASSWORD=secret\n"
        "POSTGRES_PASSWORD=local-pw\n"
    )
    env_file.write_text(original, encoding="utf-8")
    calls = 0

    def fake_load_remote(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"POSTGRES_PASSWORD": "remote-pw"}, "token"

    def unexpected_publish(*_args, **_kwargs):
        raise AssertionError("check mode must not publish Nacos config")

    monkeypatch.setattr(module, "load_remote", fake_load_remote)
    monkeypatch.setattr(module, "publish", unexpected_publish)
    monkeypatch.setattr(
        sys,
        "argv",
        ["nacos-config.py", "--env-file", str(env_file), "--check"],
    )

    assert module.main() == 0
    assert calls == 1
    assert env_file.read_text(encoding="utf-8") == original
    assert "Nacos 连接及认证验证成功" in capsys.readouterr().out


def test_bootstrap_cache_tightens_permissive_file_mode(tmp_path: Path) -> None:
    module = load_script()
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_PASSWORD=old\n", encoding="utf-8")
    env_file.chmod(0o644)

    module.write_bootstrap_cache(env_file, {"POSTGRES_PASSWORD": "new"})

    assert env_file.stat().st_mode & 0o777 == 0o600


def test_bootstrap_cache_keeps_read_only_owner_mode(tmp_path: Path) -> None:
    module = load_script()
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_PASSWORD=old\n", encoding="utf-8")
    env_file.chmod(0o400)

    module.write_bootstrap_cache(env_file, {"POSTGRES_PASSWORD": "new"})

    assert env_file.stat().st_mode & 0o777 == 0o400


def test_production_config_validation_accepts_remote_runtime_secrets() -> None:
    module = load_script()

    module.validate_production_config(
        {
            "POSTGRES_PASSWORD": "database-secret",
            "REDIS_PASSWORD": "redis-secret",
            "JWT_SECRET_KEY": "j" * 32,
            "X_TOKEN_ENCRYPTION_KEY": "x" * 32,
            "CORS_ORIGINS": ["https://sentinel.example"],
        }
    )


def test_production_config_validation_rejects_remote_placeholders() -> None:
    module = load_script()

    try:
        module.validate_production_config(
            {
                "POSTGRES_PASSWORD": "change-me-postgres",
                "REDIS_PASSWORD": "replace-with-redis-password",
                "JWT_SECRET_KEY": "development-only-change-me-at-least-32-characters",
                "X_TOKEN_ENCRYPTION_KEY": "replace-with-a-separate-32-character-secret",
                "CORS_ORIGINS": [],
            }
        )
    except RuntimeError as exc:
        message = str(exc)
        assert "JWT_SECRET_KEY" in message
        assert "X_TOKEN_ENCRYPTION_KEY" in message
        assert "PostgreSQL" in message
        assert "REDIS_PASSWORD" in message
        assert "CORS_ORIGINS" in message
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("production placeholders should be rejected")


def test_production_env_validator_accepts_nacos_managed_application_secrets(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "NACOS_CONFIG_REQUIRED=true\n"
        "NACOS_SERVER_ADDR=http://nacos:8848\n"
        "POSTGRES_DATABASE=xsentinel\n"
        "POSTGRES_USER=xsentinel\n"
        "POSTGRES_PASSWORD=database-secret\n"
        "POSTGRES_EXPORTER_PASSWORD=exporter-secret\n"
        "REDIS_PASSWORD=redis-secret\n"
        "ADMIN_USERNAME=admin\n"
        "ADMIN_PASSWORD=a-secure-admin-password\n"
        "SERVICE_AUTH_KEY_ENCRYPTION_KEY=a-dedicated-auth-center-kek-with-32-bytes\n"
        "GRAFANA_ADMIN_USER=admin\n"
        "GRAFANA_ADMIN_PASSWORD=a-secure-grafana-password\n",
        encoding="utf-8",
    )
    script = Path(__file__).parents[2] / "infra" / "scripts" / "validate-prod-env.sh"

    result = subprocess.run(
        [str(script), str(env_file)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_production_env_validator_requires_local_secrets_without_required_nacos(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "POSTGRES_DATABASE=xsentinel\n"
        "POSTGRES_USER=xsentinel\n"
        "POSTGRES_PASSWORD=database-secret\n"
        "POSTGRES_EXPORTER_PASSWORD=exporter-secret\n"
        "REDIS_PASSWORD=redis-secret\n"
        "ADMIN_USERNAME=admin\n"
        "ADMIN_PASSWORD=a-secure-admin-password\n"
        "GRAFANA_ADMIN_USER=admin\n"
        "GRAFANA_ADMIN_PASSWORD=a-secure-grafana-password\n",
        encoding="utf-8",
    )
    script = Path(__file__).parents[2] / "infra" / "scripts" / "validate-prod-env.sh"

    result = subprocess.run(
        [str(script), str(env_file)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "JWT_SECRET_KEY is missing or empty" in result.stderr
    assert "X_TOKEN_ENCRYPTION_KEY is missing or empty" in result.stderr
    assert "SERVICE_AUTH_KEY_ENCRYPTION_KEY is missing or empty" in result.stderr


def test_data_owner_publishes_public_mapped_endpoints():
    module = load_script()
    values = {"POSTGRES_HOST": "postgres", "REDIS_HOST": "redis"}
    module.resolve_data_endpoints(values, {
        "AUTO_X_MANAGE_DATA": "true", "NACOS_ADVERTISE_IP": "43.172.88.37",
        "POSTGRES_HOST_PORT": "15432", "REDIS_HOST_PORT": "16379",
    })
    assert values["POSTGRES_HOST"] == values["REDIS_HOST"] == "43.172.88.37"
    assert values["POSTGRES_PORT"] == "15432"
    assert values["REDIS_PORT"] == "16379"


def test_consumer_rejects_docker_only_endpoints():
    import pytest
    module = load_script()
    with pytest.raises(RuntimeError, match="先安装数据节点"):
        module.resolve_data_endpoints({"POSTGRES_HOST": "postgres"}, {"AUTO_X_MANAGE_DATA": "false"})


def test_consumer_preserves_remote_data_owner():
    module = load_script()
    values = {"POSTGRES_HOST": "43.172.88.37", "REDIS_HOST": "43.172.88.37"}
    module.resolve_data_endpoints(values, {"AUTO_X_MANAGE_DATA": "false", "NACOS_ADVERTISE_IP": "203.0.113.11"})
    assert values["POSTGRES_HOST"] == "43.172.88.37"


def test_camoufox_caller_upgrade_preserves_remote_clients_and_secret(tmp_path):
    import hashlib
    import json
    module = load_script()
    remote = {'backend': {'secret_sha256': 'b' * 64, 'grants': {'xhs-worker': 'xhs:execute'}}}
    values = {'SERVICE_AUTH_CLIENTS_JSON': json.dumps(remote)}
    module.ensure_camoufox_caller(values)
    first = dict(values)
    module.ensure_camoufox_caller(values)
    assert values == first
    clients = json.loads(values['SERVICE_AUTH_CLIENTS_JSON'])
    assert clients['backend'] == remote['backend']
    assert clients['xhs-worker']['grants'] == {'camoufox-worker': 'browser:execute'}
    secret = values['SERVICE_CLIENT_XHS_WORKER_SECRET']
    assert clients['xhs-worker']['secret_sha256'] == hashlib.sha256(secret.encode()).hexdigest()
    module.write_control_plane_values(tmp_path, values)
    assert (tmp_path / 'xhs-worker.secret').read_text().strip() == secret
    assert (tmp_path / 'xhs-worker.secret').stat().st_mode & 0o777 == 0o600
    assert module.read_control_plane_values(tmp_path)['SERVICE_CLIENT_XHS_WORKER_SECRET'] == secret
    # Never restore a grant deliberately removed from an existing identity.
    clients['xhs-worker']['grants'] = {}
    values['SERVICE_AUTH_CLIENTS_JSON'] = json.dumps(clients)
    module.ensure_camoufox_caller(values)
    assert json.loads(values['SERVICE_AUTH_CLIENTS_JSON'])['xhs-worker']['grants'] == {}


def test_camoufox_runtime_config_is_remote_but_port_and_image_are_local():
    module = load_script()
    assert 'CAMOUFOX_MAX_CONCURRENCY' in module.RUNTIME_CONFIG_KEYS
    assert 'CAMOUFOX_BROWSER_POOL_SIZE' in module.RUNTIME_CONFIG_KEYS
    assert 'CAMOUFOX_WORKER_IMAGE' in module.EXCLUDED_KEYS
    assert 'CAMOUFOX_SERVICE_ADVERTISE_PORT' in module.EXCLUDED_KEYS


def test_camoufox_defaults_upgrade_preserves_config_and_is_idempotent():
    module = load_script()
    values = {"CAMOUFOX_MAX_CONCURRENCY": 2, "XHS_JOB_TIMEOUT_SECONDS": 450}
    module.ensure_camoufox_defaults(values)
    assert values == {
        "CAMOUFOX_SERVICE_NAME": "xsentinel-camoufox-worker",
        "CAMOUFOX_BROWSER_POOL_SIZE": 1,
        "CAMOUFOX_MAX_CONCURRENCY": 2,
        "CAMOUFOX_JOB_TIMEOUT_SECONDS": 290,
        "CAMOUFOX_JOB_RESULT_TTL_SECONDS": 600,
        "XHS_JOB_TIMEOUT_SECONDS": 450,
    }
    first = dict(values)
    module.ensure_camoufox_defaults(values)
    assert values == first


def test_screenshot_caller_seeds_new_identity_and_preserves_revocation(tmp_path):
    import hashlib
    import json

    module = load_script()
    existing = {"backend": {"secret_sha256": "b" * 64, "grants": {}}}
    values = {"SERVICE_AUTH_CLIENTS_JSON": json.dumps(existing)}
    module.ensure_screenshot_caller(values)
    first = dict(values)
    module.ensure_screenshot_caller(values)
    assert values == first
    clients = json.loads(values["SERVICE_AUTH_CLIENTS_JSON"])
    assert clients["backend"] == existing["backend"]
    secret = values["SERVICE_CLIENT_SCREENSHOT_WORKER_SECRET"]
    assert clients["screenshot-worker"] == {
        "secret_sha256": hashlib.sha256(secret.encode()).hexdigest(),
        "grants": {"camoufox-worker": "browser:execute"},
    }
    module.write_control_plane_values(tmp_path, values)
    secret_file = tmp_path / "screenshot-worker.secret"
    assert secret_file.read_text().strip() == secret
    assert secret_file.stat().st_mode & 0o777 == 0o600
    assert module.read_control_plane_values(tmp_path)["SERVICE_CLIENT_SCREENSHOT_WORKER_SECRET"] == secret
    clients["screenshot-worker"]["grants"] = {}
    values["SERVICE_AUTH_CLIENTS_JSON"] = json.dumps(clients)
    module.ensure_screenshot_caller(values)
    assert json.loads(values["SERVICE_AUTH_CLIENTS_JSON"])["screenshot-worker"]["grants"] == {}


def test_screenshot_caller_rejects_missing_or_mismatched_remote_secret():
    import json
    import pytest

    module = load_script()
    values = {"SERVICE_AUTH_CLIENTS_JSON": json.dumps({
        "screenshot-worker": {"secret_sha256": "b" * 64, "grants": {}}
    })}
    with pytest.raises(RuntimeError, match="screenshot-worker"):
        module.ensure_screenshot_caller(values)
    values["SERVICE_CLIENT_SCREENSHOT_WORKER_SECRET"] = "wrong-secret"
    with pytest.raises(RuntimeError, match="screenshot-worker"):
        module.ensure_screenshot_caller(values)


def test_screenshot_config_separates_runtime_from_local_mounts():
    module = load_script()
    values = {"TWEET_SCREENSHOT_ENABLED": False, "TWEET_SCREENSHOT_RETRY_SECONDS": 60}
    module.ensure_screenshot_defaults(values)
    assert values == {
        "TWEET_SCREENSHOT_ENABLED": False,
        "TWEET_SCREENSHOT_MAX_ATTEMPTS": 3,
        "TWEET_SCREENSHOT_RETRY_SECONDS": 60,
        "TWEET_SCREENSHOT_SCAN_INTERVAL_SECONDS": 5,
    }
    assert set(values) <= module.RUNTIME_CONFIG_KEYS
    assert {
        "TWEET_SCREENSHOT_DIR", "TWEET_SCREENSHOT_VOLUME", "TWEET_SCREENSHOT_CLIENT_SECRET_FILE"
    } <= module.EXCLUDED_KEYS
    assert "SERVICE_CLIENT_SCREENSHOT_WORKER_SECRET" in module.CONTROL_PLANE_KEYS
    assert "SERVICE_CLIENT_SCREENSHOT_WORKER_SECRET" not in module.BOOTSTRAP_CACHE_KEYS


def test_control_plane_init_seeds_screenshot_secret_without_restoring_grants(tmp_path):
    import hashlib
    import json

    root = Path(__file__).parents[2]
    # Skip key generation: this test verifies client bootstrap and permissions.
    (tmp_path / "private.pem").write_text("existing-private-key\n")
    (tmp_path / "public.pem").write_text("existing-public-key\n")
    original = {
        "backend": {"secret_sha256": "b" * 64, "grants": {}},
        "xhs-worker": {"secret_sha256": "x" * 64, "grants": {}},
    }
    clients_path = tmp_path / "clients.json"
    clients_path.write_text(json.dumps(original))
    command = ["bash", str(root / "infra/scripts/microservices-init.sh"), str(tmp_path)]
    subprocess.run(command, check=True, capture_output=True, text=True)
    clients = json.loads(clients_path.read_text())
    secret_file = tmp_path / "screenshot-worker.secret"
    secret = secret_file.read_text().strip()
    assert clients["screenshot-worker"] == {
        "secret_sha256": hashlib.sha256(secret.encode()).hexdigest(),
        "grants": {"camoufox-worker": "browser:execute"},
    }
    assert secret_file.stat().st_mode & 0o777 == 0o600
    assert clients["xhs-worker"] == original["xhs-worker"]
    clients["screenshot-worker"]["grants"] = {}
    clients_path.write_text(json.dumps(clients))
    subprocess.run(command, check=True, capture_output=True, text=True)
    assert secret_file.read_text().strip() == secret
    assert json.loads(clients_path.read_text())["screenshot-worker"]["grants"] == {}


def test_monitor_upgrade_adds_only_selected_browser_and_preserves_remote(monkeypatch, tmp_path):
    import copy
    module = load_script()
    topology = module.monitor_seed_topology("hn-1", tmp_path)
    topology["services"] = [s for s in topology["services"]
                            if s["container_service"] != "camoufox-worker"]
    topology["interval_seconds"] = 17
    remote = {
        "x-sentinel-monitor-topology.json": copy.deepcopy(topology),
        "x-sentinel-monitor-nodes.json": {
            "nodes": {"hn-1": {"advertise_ip": "203.0.113.11"}}
        },
    }
    published = []

    def fake_load(_server, *, data_id, **_kwargs):
        return copy.deepcopy(remote.get(data_id))

    def fake_publish(_server, *, data_id, content, **_kwargs):
        remote[data_id] = copy.deepcopy(content)
        published.append(data_id)

    monkeypatch.setattr(module, "load_json_document", fake_load)
    monkeypatch.setattr(module, "publish", fake_publish)
    common = dict(server="http://nacos", namespace="public", group="X_SENTINEL",
                  token="", timeout=3, control_dir=tmp_path, seed_node_id="",
                  local_env={"NACOS_ADVERTISE_IP": "203.0.113.11",
                             "CAMOUFOX_WORKER_HOST_PORT": "18007"})
    module.sync_monitor_documents(**common, selected_services="xhs-worker,monitor-agent")
    assert published == []
    assert remote["x-sentinel-monitor-topology.json"] == topology

    module.sync_monitor_documents(**common, selected_services="camoufox-worker,monitor-agent")
    updated = remote["x-sentinel-monitor-topology.json"]
    assert updated["services"][:-1] == topology["services"]
    assert updated["interval_seconds"] == 17
    assert updated["nodes"] == topology["nodes"]
    assert updated["services"][-1] == {
        "id": "hn1-camoufox-worker", "name": "Camoufox Worker",
        "component": "camoufox_worker", "node": "hn-1", "project": "x-sentinel",
        "container_service": "camoufox-worker", "port": 18007,
    }
    assert published == ["x-sentinel-monitor-topology.json"]

    updated["services"][-1].update(id="custom-browser", name="Custom Browser", port=28007)
    before = copy.deepcopy(updated)
    module.sync_monitor_documents(**common, selected_services="camoufox-worker")
    assert remote["x-sentinel-monitor-topology.json"] == before
    assert published == ["x-sentinel-monitor-topology.json"]


def test_monitor_browser_upgrade_rejects_id_collision(monkeypatch, tmp_path):
    import copy
    import pytest
    module = load_script()
    topology = module.monitor_seed_topology("hn-1", tmp_path)
    topology["services"] = [s for s in topology["services"]
                            if s["container_service"] != "camoufox-worker"]
    topology["services"][0]["id"] = "hn1-camoufox-worker"
    remote = {
        "x-sentinel-monitor-topology.json": topology,
        "x-sentinel-monitor-nodes.json": {
            "nodes": {"hn-1": {"advertise_ip": "203.0.113.11"}}
        },
    }
    monkeypatch.setattr(module, "load_json_document",
                        lambda _server, *, data_id, **_kwargs: copy.deepcopy(remote[data_id]))
    monkeypatch.setattr(module, "publish", lambda *_args, **_kwargs: pytest.fail("must not publish"))
    with pytest.raises(RuntimeError, match="已被其他实例占用"):
        module.sync_monitor_documents(
            "http://nacos", namespace="public", group="X_SENTINEL", token="", timeout=3,
            control_dir=tmp_path, seed_node_id="", selected_services="camoufox-worker",
            local_env={"NACOS_ADVERTISE_IP": "203.0.113.11"},
        )
