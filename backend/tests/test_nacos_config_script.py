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


def test_write_bootstrap_cache_updates_only_compose_data_coordinates(tmp_path: Path) -> None:
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

    assert count == 3
    assert env_file.stat().st_mode & 0o777 == 0o600
    assert env_file.read_text(encoding="utf-8") == (
        "# deployment identity\n"
        "NACOS_SERVER_ADDR=http://nacos:8848\n"
        "POSTGRES_PASSWORD=remote-password\n"
        "REDIS_HOST=remote-cache\n"
        "POSTGRES_DSN=postgresql+asyncpg://old/db\n"
        "POSTGRES_HOST=remote-db\n"
    )


def test_write_bootstrap_cache_quotes_unsafe_remote_values(tmp_path: Path) -> None:
    module = load_script()
    env_file = tmp_path / ".env"
    env_file.write_text("REDIS_PASSWORD=old\n", encoding="utf-8")

    module.write_bootstrap_cache(env_file, {"REDIS_PASSWORD": "has space#and$dollar"})

    assert env_file.read_text(encoding="utf-8") == "REDIS_PASSWORD='has space#and$dollar'\n"
    assert module.parse_env(env_file)["REDIS_PASSWORD"] == "has space#and$dollar"


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


def test_deployment_network_group_is_not_published() -> None:
    module = load_script()
    assert "NETWORK_GROUPS" in module.EXCLUDED_KEYS


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
