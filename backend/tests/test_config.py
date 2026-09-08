import pytest
from pydantic import ValidationError

from app.core.config import Settings


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
