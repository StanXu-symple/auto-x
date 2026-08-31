import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_component_database_and_redis_settings_build_urls() -> None:
    settings = Settings(
        _env_file=None,
        mysql_dsn="",
        mysql_host="db.internal",
        mysql_port=3307,
        mysql_database="sentinel",
        mysql_user="user@example",
        mysql_password="p@ss word",
        redis_url="",
        redis_host="cache.internal",
        redis_port=6380,
        redis_db=2,
        redis_password="redis secret",
    )
    assert settings.mysql_dsn == (
        "mysql+aiomysql://user%40example:p%40ss%20word@db.internal:3307/sentinel?charset=utf8mb4"
    )
    assert settings.redis_url == "redis://:redis%20secret@cache.internal:6380/2"


def test_explicit_urls_take_precedence() -> None:
    settings = Settings(
        _env_file=None,
        mysql_dsn="mysql+aiomysql://explicit/db",
        redis_url="redis://explicit/4",
    )
    assert settings.mysql_dsn == "mysql+aiomysql://explicit/db"
    assert settings.redis_url == "redis://explicit/4"


def production_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "environment": "production",
        "mysql_dsn": "mysql+aiomysql://user:strong-db-secret@db/sentinel",
        "redis_url": "redis://cache/0",
        "jwt_secret_key": "a-secure-production-jwt-secret-over-32-characters",
        "admin_password": "a-secure-admin-password",
        "x_bearer_token": "real-looking-bearer-token",
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
        ("x_bearer_token", "replace-with-real-token"),
        ("x_bearer_token", ""),
        ("mysql_dsn", "mysql+aiomysql://user:change-me-database@db/sentinel"),
        ("redis_url", "redis://:replace-with-real-password@cache/0"),
    ],
)
def test_production_settings_reject_placeholders(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        production_settings(**{field: value})
