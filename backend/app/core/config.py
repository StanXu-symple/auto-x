from functools import lru_cache
from typing import Literal
from urllib.parse import quote, unquote, urlsplit

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "X Sentinel"
    environment: Literal["development", "test", "staging", "production"] = "development"
    debug: bool = False
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"
    startup_strict: bool = False
    auto_create_tables: bool = True
    app_timezone: str = "Asia/Shanghai"

    mysql_dsn: str = ""
    mysql_host: str = "mysql"
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_database: str = "xsentinel"
    mysql_user: str = "xsentinel"
    mysql_password: str = "xsentinel"
    mysql_pool_size: int = Field(default=10, ge=1, le=100)
    mysql_max_overflow: int = Field(default=20, ge=0, le=200)
    mysql_pool_recycle_seconds: int = Field(default=1800, ge=60)

    redis_url: str = ""
    redis_host: str = "redis"
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_db: int = Field(default=0, ge=0, le=15)
    redis_password: str = ""
    redis_socket_timeout_seconds: float = Field(default=3.0, gt=0, le=30)

    jwt_secret_key: str = "development-only-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = Field(default=480, ge=5, le=10080)
    admin_username: str = "admin"
    admin_password: str = "change-me-now"

    x_token_encryption_key: str = ""
    x_token_cache_ttl_seconds: int = Field(default=300, ge=30, le=86400)
    x_api_base_url: str = "https://api.x.com/2"
    x_request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    x_max_pages_per_poll: int = Field(default=5, ge=1, le=50)
    x_page_size: int = Field(default=100, ge=5, le=100)

    default_poll_interval_seconds: int = Field(default=300, ge=15, le=86400)
    worker_scan_interval_seconds: float = Field(default=2.0, ge=0.5, le=60)
    worker_max_concurrency: int = Field(default=5, ge=1, le=100)
    worker_batch_size: int = Field(default=100, ge=1, le=1000)
    worker_lock_ttl_seconds: int = Field(default=120, ge=15, le=3600)
    worker_heartbeat_ttl_seconds: int = Field(default=30, ge=10, le=300)
    worker_metrics_port: int = Field(default=8001, ge=0, le=65535)
    pagination_resume_delay_seconds: float = Field(default=1.0, ge=0.1, le=60)
    x_auth_gate_seconds: int = Field(default=300, ge=30, le=3600)

    login_rate_limit_attempts: int = Field(default=5, ge=1, le=100)
    login_rate_limit_window_seconds: int = Field(default=300, ge=30, le=3600)

    # Legacy environment fields remain readable for deployment compatibility. Runtime AI
    # generation uses the encrypted singleton managed through /api/v1/ai-data-source.
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    codex_bridge_url: str = ""
    codex_bridge_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("CODEX_BRIDGE_TOKEN", "CODEX_BRIDGE_API_KEY"),
    )
    ai_allowed_provider_hosts: list[str] = Field(default_factory=lambda: ["api.openai.com"])
    ai_worker_scan_interval_seconds: float = Field(default=2.0, ge=0.5, le=60)
    ai_worker_max_concurrency: int = Field(default=3, ge=1, le=50)
    ai_worker_batch_size: int = Field(default=50, ge=1, le=500)
    ai_worker_lock_ttl_seconds: int = Field(default=180, ge=30, le=3600)
    ai_worker_heartbeat_ttl_seconds: int = Field(default=30, ge=10, le=300)
    ai_worker_metrics_port: int = Field(default=8002, ge=0, le=65535)


    qq_auth_url: str = "https://bots.qq.com/app/getAppAccessToken"
    qq_api_base_url: str = "https://api.sgroup.qq.com/"
    qq_request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    qq_worker_scan_interval_seconds: float = Field(default=2.0, ge=0.5, le=60)
    qq_worker_max_concurrency: int = Field(default=5, ge=1, le=50)
    qq_worker_batch_size: int = Field(default=100, ge=1, le=1000)
    qq_worker_send_interval_seconds: float = Field(default=5.0, ge=0.0, le=300)
    qq_worker_max_attempts: int = Field(default=3, ge=1, le=10)
    qq_worker_lock_ttl_seconds: int = Field(default=60, ge=15, le=600)
    qq_worker_heartbeat_ttl_seconds: int = Field(default=30, ge=10, le=300)
    qq_worker_port: int = Field(default=8003, ge=0, le=65535)
    qq_worker_metrics_port: int = Field(default=8004, ge=0, le=65535)

    xhs_job_timeout_seconds: float = Field(default=180.0, ge=30, le=600)
    xhs_job_result_ttl_seconds: int = Field(default=300, ge=60, le=3600)
    xhs_worker_heartbeat_ttl_seconds: int = Field(default=30, ge=10, le=300)
    xhs_worker_metrics_port: int = Field(default=8005, ge=0, le=65535)

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if not self.mysql_dsn:
            user = quote(self.mysql_user, safe="")
            password = quote(self.mysql_password, safe="")
            database = quote(self.mysql_database, safe="")
            self.mysql_dsn = (
                f"mysql+aiomysql://{user}:{password}@{self.mysql_host}:"
                f"{self.mysql_port}/{database}?charset=utf8mb4"
            )
        if not self.redis_url:
            credentials = f":{quote(self.redis_password, safe='')}@" if self.redis_password else ""
            self.redis_url = (
                f"redis://{credentials}{self.redis_host}:{self.redis_port}/{self.redis_db}"
            )
        if self.environment == "production":
            forbidden_prefixes = ("development-only-", "change-me-", "replace-with-")

            def placeholder(value: str) -> bool:
                return value.lower().startswith(forbidden_prefixes)

            if len(self.jwt_secret_key) < 32 or placeholder(self.jwt_secret_key):
                raise ValueError("JWT_SECRET_KEY must contain at least 32 characters in production")
            if len(self.admin_password) < 12 or placeholder(self.admin_password):
                raise ValueError("ADMIN_PASSWORD must contain at least 12 characters in production")
            parsed_mysql_password = unquote(urlsplit(self.mysql_dsn).password or "")
            if (
                not parsed_mysql_password
                or parsed_mysql_password == "xsentinel"
                or placeholder(parsed_mysql_password)
            ):
                raise ValueError(
                    "MySQL password must be non-empty and non-placeholder in production"
                )
            if len(self.x_token_encryption_key) < 32 or placeholder(
                self.x_token_encryption_key
            ):
                raise ValueError(
                    "X_TOKEN_ENCRYPTION_KEY must contain at least 32 characters in production"
                )
            parsed_redis_password = unquote(urlsplit(self.redis_url).password or "")
            if parsed_redis_password and placeholder(parsed_redis_password):
                raise ValueError("REDIS_PASSWORD must not be a placeholder in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
