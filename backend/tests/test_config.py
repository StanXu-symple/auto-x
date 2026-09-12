import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_application_service_default_nacos_identity_and_ports(monkeypatch) -> None:
    for key in (
        "NACOS_SERVICE_NAME",
        "NACOS_SERVICE_PORT",
        "WORKER_METRICS_PORT",
        "AI_WORKER_METRICS_PORT",
        "QQ_WORKER_PORT",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = Settings(_env_file=None, nacos_config_enabled=False)

    assert settings.nacos_service_name == "xsentinel-backend"
    assert settings.nacos_service_port == 8200
    assert settings.worker_metrics_port == 8201
    assert settings.ai_worker_metrics_port == 8202
    assert settings.qq_worker_port == 8203


def test_component_database_and_redis_settings_build_urls() -> None:
    settings = Settings(
        _env_file=None,
        postgres_dsn="",
        postgres_host="db.internal",
        postgres_port=5433,
        postgres_database="sentinel",
        postgres_user="user@example",
        postgres_password="p@ss word",
        redis_url="",
        redis_host="cache.internal",
        redis_port=6380,
        redis_db=2,
        redis_password="redis secret",
    )
    assert settings.postgres_dsn == (
        "postgresql+asyncpg://user%40example:p%40ss%20word@db.internal:5433/sentinel"
    )
    assert settings.redis_url == "redis://:redis%20secret@cache.internal:6380/2"
    assert settings.postgres_pool_size == 3
    assert settings.postgres_max_overflow == 2
    assert settings.xhs_browser_pool_size == 1
    assert settings.xhs_browser_max_concurrency == 1


def test_explicit_urls_take_precedence() -> None:
    settings = Settings(
        _env_file=None,
        postgres_dsn="postgresql+asyncpg://explicit/db",
        redis_url="redis://explicit/4",
    )
    assert settings.postgres_dsn == "postgresql+asyncpg://explicit/db"
    assert settings.redis_url == "redis://explicit/4"


def test_nacos_config_overrides_dotenv_and_environment(monkeypatch, tmp_path) -> None:
    """Remote application settings win while Nacos bootstrap stays local."""

    class FakeNacosConfigClient:
        calls: list[dict[str, object]] = []

        def __init__(self, **kwargs):
            self.calls.append(kwargs)

        def get_json_config(self, data_id):
            assert data_id == "remote-config.json"
            return {
                "postgresql": {
                    "host": "remote-db",
                    "port": 5544,
                    "password": "remote-pw",
                },
                "redis_host": "remote-cache",
                "redis_port": 6381,
                "worker": {"max_concurrency": 17},
                "cors_origins": ["https://remote.example"],
                "CODEX_BRIDGE_TOKEN": "must-stay-local",
                "jwt_secret_key": "remote-jwt-secret-over-32-characters-long",
                "x_token_encryption_key": "remote-x-token-key-over-32-characters-long",
                "admin_password": "must-stay-local",
                "TZ": "UTC",
                "environment": "production",
                "debug": True,
                "startup_strict": False,
                "auto_create_tables": False,
                "xhs_transport": "http",
                "xhs_service_name": "remote-xhs",
                "xhs_service_port": 9806,
                "service_auth_url": "http://remote-auth:9100",
                "monitor_center_url": "http://remote-monitor:9102",
                "service_client_secret_file": "/remote/backend.secret",
                "service_auth_public_key_file": "/remote/public.pem",
                "nacos_service_name": "remote-api",
                "nacos_advertise_ip": "192.0.2.10",
                "nacos_service_port": 9800,
                "worker_metrics_port": 9801,
                "ai_worker_metrics_port": 9802,
                "qq_worker_port": 9803,
                "qq_worker_metrics_port": 9804,
                "xhs_worker_metrics_port": 9805,
                "xhs_service_advertise_ip": "192.0.2.11",
                "xhs_service_advertise_port": 9806,
                # Bootstrap credentials are intentionally ignored.
                "nacos_password": "should-not-win",
            }

    monkeypatch.setattr("app.control_plane.nacos_config.NacosConfigClient", FakeNacosConfigClient)
    monkeypatch.setenv("NACOS_SERVER_ADDR", "http://nacos.internal:8848")
    monkeypatch.setenv("NACOS_USERNAME", "env-user")
    monkeypatch.setenv("NACOS_PASSWORD", "env-password")
    monkeypatch.setenv("POSTGRES_HOST", "process-db")
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("STARTUP_STRICT", "true")
    monkeypatch.setenv("AUTO_CREATE_TABLES", "true")
    monkeypatch.setenv("XHS_TRANSPORT", "redis")
    monkeypatch.setenv("XHS_SERVICE_NAME", "local-xhs")
    monkeypatch.setenv("XHS_SERVICE_PORT", "8006")
    monkeypatch.setenv("SERVICE_AUTH_URL", "http://local-auth:9100")
    monkeypatch.setenv("MONITOR_CENTER_URL", "http://local-monitor:9102")
    monkeypatch.setenv("SERVICE_CLIENT_SECRET_FILE", "/local/backend.secret")
    monkeypatch.setenv("SERVICE_AUTH_PUBLIC_KEY_FILE", "/local/public.pem")
    monkeypatch.setenv("NACOS_SERVICE_NAME", "local-api")
    monkeypatch.setenv("NACOS_ADVERTISE_IP", "127.0.0.1")
    monkeypatch.setenv("NACOS_SERVICE_PORT", "8200")
    monkeypatch.setenv("WORKER_METRICS_PORT", "8201")
    monkeypatch.setenv("AI_WORKER_METRICS_PORT", "8202")
    monkeypatch.setenv("QQ_WORKER_PORT", "8203")
    monkeypatch.setenv("QQ_WORKER_METRICS_PORT", "8004")
    monkeypatch.setenv("XHS_WORKER_METRICS_PORT", "8005")
    monkeypatch.setenv("XHS_SERVICE_ADVERTISE_IP", "127.0.0.2")
    monkeypatch.setenv("XHS_SERVICE_ADVERTISE_PORT", "8006")
    monkeypatch.setenv("ADMIN_PASSWORD", "local-admin-password")
    monkeypatch.setenv("CODEX_BRIDGE_TOKEN", "local-bridge-token")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "NACOS_CONFIG_DATA_ID=remote-config.json\nPOSTGRES_PORT=5443\nREDIS_HOST=dotenv-cache\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.postgres_host == "remote-db"
    assert settings.postgres_port == 5544
    assert settings.postgres_password == "remote-pw"
    assert settings.postgres_dsn == (
        "postgresql+asyncpg://xsentinel:remote-pw@remote-db:5544/xsentinel"
    )
    assert settings.redis_host == "remote-cache"
    assert settings.redis_port == 6381
    assert settings.redis_url == "redis://remote-cache:6381/0"
    assert settings.worker_max_concurrency == 17
    assert settings.cors_origins == ["https://remote.example"]
    assert settings.codex_bridge_api_key == "local-bridge-token"
    assert settings.jwt_secret_key == "remote-jwt-secret-over-32-characters-long"
    assert settings.x_token_encryption_key == "remote-x-token-key-over-32-characters-long"
    assert settings.app_timezone == "UTC"
    assert settings.admin_password == "local-admin-password"
    assert settings.environment == "staging"
    assert settings.debug is False
    assert settings.startup_strict is True
    assert settings.auto_create_tables is True
    assert settings.xhs_transport == "redis"
    assert settings.xhs_service_name == "local-xhs"
    assert settings.xhs_service_port == 8006
    assert settings.service_auth_url == "http://local-auth:9100"
    assert settings.monitor_center_url == "http://local-monitor:9102"
    assert settings.service_client_secret_file == "/local/backend.secret"
    assert settings.service_auth_public_key_file == "/local/public.pem"
    assert settings.nacos_service_name == "local-api"
    assert settings.nacos_advertise_ip == "127.0.0.1"
    assert settings.nacos_service_port == 8200
    assert settings.worker_metrics_port == 8201
    assert settings.ai_worker_metrics_port == 8202
    assert settings.qq_worker_port == 8203
    assert settings.qq_worker_metrics_port == 8004
    assert settings.xhs_worker_metrics_port == 8005
    assert settings.xhs_service_advertise_ip == "127.0.0.2"
    assert settings.xhs_service_advertise_port == 8006
    assert settings.nacos_password == "env-password"
    assert FakeNacosConfigClient.calls[0]["server"] == "http://nacos.internal:8848"


def test_nacos_config_explicit_init_values_remain_highest_priority(monkeypatch) -> None:
    class FakeNacosConfigClient:
        called = False

        def __init__(self, **_kwargs):
            type(self).called = True

        def get_json_config(self, _data_id):
            return {
                "postgres_host": "remote-db",
                "redis_port": 6381,
                "worker_max_concurrency": 19,
            }

    monkeypatch.setattr("app.control_plane.nacos_config.NacosConfigClient", FakeNacosConfigClient)
    settings = Settings(
        _env_file=None,
        nacos_server_addr="http://nacos:8848",
        postgres_host="init-db",
        redis_port=6390,
    )
    assert FakeNacosConfigClient.called is True
    assert settings.postgres_host == "init-db"
    assert settings.redis_port == 6390
    assert settings.worker_max_concurrency == 19


def test_nacos_nested_database_alias_rebuilds_connection_url(monkeypatch) -> None:
    class FakeNacosConfigClient:
        def __init__(self, **_kwargs):
            pass

        def get_json_config(self, _data_id):
            return {"postgres": {"db": "remote-db-name"}, "redis": {"db": 3}}

    monkeypatch.setattr("app.control_plane.nacos_config.NacosConfigClient", FakeNacosConfigClient)
    settings = Settings(
        _env_file=None,
        nacos_server_addr="http://nacos:8848",
        postgres_host="remote-db-host",
        redis_host="remote-redis-host",
    )

    assert settings.postgres_database == "remote-db-name"
    assert settings.postgres_dsn.endswith("@remote-db-host:5432/remote-db-name")
    assert settings.redis_db == 3
    assert settings.redis_url == "redis://remote-redis-host:6379/3"


def test_nacos_pool_tuning_does_not_replace_explicit_connection_urls(monkeypatch) -> None:
    class FakeNacosConfigClient:
        def __init__(self, **_kwargs):
            pass

        def get_json_config(self, _data_id):
            return {"postgres_pool_size": 9, "redis_socket_timeout_seconds": 8}

    monkeypatch.setattr("app.control_plane.nacos_config.NacosConfigClient", FakeNacosConfigClient)
    monkeypatch.setenv("NACOS_SERVER_ADDR", "http://nacos:8848")
    monkeypatch.setenv("POSTGRES_DSN", "postgresql+asyncpg://explicit/db")
    monkeypatch.setenv("REDIS_URL", "redis://explicit/4")
    settings = Settings(_env_file=None)
    assert settings.postgres_pool_size == 9
    assert settings.redis_socket_timeout_seconds == 8
    assert settings.postgres_dsn == "postgresql+asyncpg://explicit/db"
    assert settings.redis_url == "redis://explicit/4"


def test_nacos_config_failure_falls_back_unless_required(monkeypatch) -> None:
    class FailingNacosConfigClient:
        def __init__(self, **_kwargs):
            raise OSError("Nacos unavailable")

    monkeypatch.setattr(
        "app.control_plane.nacos_config.NacosConfigClient", FailingNacosConfigClient
    )
    fallback = Settings(
        _env_file=None,
        nacos_server_addr="http://nacos:8848",
        postgres_host="fallback-db",
    )
    assert fallback.postgres_host == "fallback-db"

    with pytest.raises(RuntimeError, match="Unable to load Nacos Config"):
        Settings(
            _env_file=None,
            nacos_server_addr="http://nacos:8848",
            nacos_config_required=True,
        )

    with pytest.raises(RuntimeError, match="NACOS_SERVER_ADDR"):
        Settings(_env_file=None, nacos_config_required=True)


def test_nacos_config_can_be_disabled(monkeypatch) -> None:
    class UnexpectedNacosConfigClient:
        def __init__(self, **_kwargs):
            raise AssertionError("Nacos must not be contacted when disabled")

    monkeypatch.setattr(
        "app.control_plane.nacos_config.NacosConfigClient", UnexpectedNacosConfigClient
    )
    settings = Settings(
        _env_file=None,
        nacos_server_addr="http://nacos:8848",
        nacos_config_enabled=False,
    )
    assert settings.nacos_config_enabled is False


def production_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "environment": "production",
        "postgres_dsn": "postgresql+asyncpg://user:strong-db-secret@db/sentinel",
        "redis_url": "redis://cache/0",
        "jwt_secret_key": "a-secure-production-jwt-secret-over-32-characters",
        "admin_password": "a-secure-admin-password",
        "x_token_encryption_key": "a-separate-x-token-encryption-key-over-32-characters",
    }
    values.update(overrides)
    return Settings(**values)


def test_production_settings_accept_non_placeholder_secrets() -> None:
    assert production_settings().environment == "production"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("jwt_secret_key", "development-only-secret-that-is-long-enough"),
        ("admin_password", "change-me-admin-password"),
        ("x_token_encryption_key", "replace-with-real-token-key-that-is-long"),
        ("x_token_encryption_key", ""),
        ("postgres_dsn", "postgresql+asyncpg://user:change-me-database@db/sentinel"),
        ("redis_url", "redis://:replace-with-real-password@cache/0"),
    ],
)
def test_production_settings_reject_placeholders(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        production_settings(**{field: value})
