from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import quote, unquote, urlsplit

from pydantic import AliasChoices, Field, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

logger = logging.getLogger(__name__)


# These values are needed to reach Nacos itself and therefore must remain in
# the process environment (or the local dotenv file). They are deliberately
# never accepted from the remote document, so a bad/compromised config cannot
# redirect the bootstrap client or replace its credentials.
_NACOS_BOOTSTRAP_FIELDS = {
    "nacos_server_addr",
    "nacos_namespace",
    "nacos_group",
    "nacos_username",
    "nacos_password",
    "nacos_config_enabled",
    "nacos_config_data_id",
    "nacos_config_group",
    "nacos_config_timeout_seconds",
    "nacos_config_required",
}

# These values describe the process/container in which the application is
# running. They must remain local so a shared Nacos document cannot make every
# service bind to the same port, switch a deployment's transport mode, or use
# another host's mounted secret file. Runtime tuning and shared data-service
# coordinates remain eligible for Nacos Config.
_NACOS_LOCAL_ONLY_FIELDS = {
    "environment",
    "debug",
    "startup_strict",
    "auto_create_tables",
    "service_auth_url",
    "monitor_center_url",
    "service_client_secret_file",
    "service_auth_public_key_file",
    "nacos_service_name",
    "nacos_advertise_ip",
    "nacos_service_port",
    "worker_metrics_port",
    "ai_worker_metrics_port",
    "qq_worker_port",
    "qq_worker_metrics_port",
    "xhs_worker_metrics_port",
    "xhs_transport",
    "xhs_service_name",
    "xhs_service_port",
    "xhs_service_advertise_ip",
    "xhs_service_advertise_port",
    # Provider credentials are stored encrypted in PostgreSQL by the data
    # source UI.  Keep legacy environment fallbacks local instead of allowing
    # a shared config document to become a second plaintext credential store.
    "openai_api_key",
    "codex_bridge_api_key",
    # The administrator credential seeds a database row and must not be
    # rotated implicitly by a shared remote document.  JWT/X-token keys are
    # intentionally *not* listed here because replicas need the same values.
    "admin_username",
    "admin_password",
}

_POSTGRES_DSN_COMPONENT_FIELDS = {
    "postgres_host",
    "postgres_port",
    "postgres_database",
    "postgres_user",
    "postgres_password",
}
_REDIS_URL_COMPONENT_FIELDS = {
    "redis_host",
    "redis_port",
    "redis_db",
    "redis_password",
}


def _normalise_key(value: object) -> str:
    """Return a settings-compatible key for a flat or nested JSON document."""

    key = str(value).strip().lower().replace("-", "_").replace(".", "_")
    # Accept the names commonly used by PostgreSQL/Compose dotenv files while
    # keeping the application's canonical field names stable.
    if key == "postgresql":
        return "postgres"
    if key.startswith("postgresql_"):
        return "postgres_" + key[len("postgresql_") :]
    if key == "postgres_db":
        return "postgres_database"
    if key == "tz":
        return "app_timezone"
    return key


def _flatten_config(value: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested Nacos JSON while preserving arrays and scalar values.

    Both of these forms are accepted, which makes the config document pleasant
    to edit by hand and easy for the installer to generate::

        {"postgres_host": "db", "redis_port": 6379}
        {"postgres": {"host": "db"}, "redis": {"port": 6379}}
    """

    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = _normalise_key(raw_key)
        if not key:
            continue
        # Normalize after joining the prefix as well. This handles aliases in
        # nested documents, for example ``{"postgres": {"db": ...}}``.
        full_key = _normalise_key(f"{prefix}_{key}" if prefix else key)
        if isinstance(raw_value, Mapping):
            result.update(_flatten_config(raw_value, full_key))
        else:
            result[full_key] = raw_value
    return result


def _non_empty(value: Any) -> Any:
    """Treat empty bootstrap strings as unset while retaining false/zero values."""

    if isinstance(value, str) and not value.strip():
        return None
    return value


class NacosConfigSettingsSource(PydanticBaseSettingsSource):
    """Synchronous Nacos Config source used during Settings construction.

    Settings are resolved while modules such as ``app.db.session`` are
    imported, before an asyncio event loop exists. A small synchronous HTTP
    request is therefore intentional here; runtime service-to-service calls
    continue to use the async Nacos client.
    """

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        *,
        env_settings: EnvSettingsSource,
        dotenv_settings: DotEnvSettingsSource,
        _init_state: Any = None,
    ) -> None:
        super().__init__(settings_cls, _init_state=_init_state)
        self.env_settings = env_settings
        self.dotenv_settings = dotenv_settings

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:  # pragma: no cover - source is mapping based
        raise NotImplementedError("Nacos source resolves the complete document at once")

    def __call__(self) -> dict[str, Any]:
        bootstrap = self._bootstrap_values()
        if not _as_bool(bootstrap.get("nacos_config_enabled"), default=True):
            return {}

        required = _as_bool(bootstrap.get("nacos_config_required"), default=False)
        server = str(bootstrap.get("nacos_server_addr") or "").strip()
        if not server:
            if required:
                raise RuntimeError(
                    "NACOS_SERVER_ADDR must be configured when NACOS_CONFIG_REQUIRED=true"
                )
            return {}

        namespace = str(bootstrap.get("nacos_namespace") or "public").strip() or "public"
        naming_group = str(bootstrap.get("nacos_group") or "X_SENTINEL").strip()
        config_group = str(bootstrap.get("nacos_config_group") or naming_group).strip()
        data_id = (
            str(bootstrap.get("nacos_config_data_id") or "x-sentinel-config.json").strip()
            or "x-sentinel-config.json"
        )
        timeout = _as_float(bootstrap.get("nacos_config_timeout_seconds"), default=3.0)

        client = None
        try:
            # Import lazily to keep core settings usable in tools that only
            # install the settings package and to avoid import cycles.
            from app.control_plane.nacos_config import NacosConfigClient

            client = NacosConfigClient(
                server=server,
                namespace=namespace,
                group=config_group,
                username=str(bootstrap.get("nacos_username") or "").strip(),
                password=str(bootstrap.get("nacos_password") or ""),
                timeout=timeout,
            )
            payload = client.get_json_config(data_id)
        except Exception as exc:  # noqa: BLE001 - startup fallback is intentional
            message = f"Unable to load Nacos Config {data_id!r}: {exc}"
            if required:
                raise RuntimeError(message) from exc
            logger.warning("%s; using local environment fallback", message)
            return {}
        finally:
            if client is not None:
                close = getattr(client, "close", None)
                if close is not None:
                    try:
                        close()
                    except Exception:  # noqa: BLE001 - cleanup must not mask config errors
                        logger.debug("Failed to close temporary Nacos Config client", exc_info=True)

        if payload is None:
            message = f"Nacos Config {data_id!r} was not found in group {config_group!r}"
            if required:
                raise RuntimeError(message)
            logger.warning("%s; using local environment fallback", message)
            return {}
        if not isinstance(payload, Mapping):
            message = f"Nacos Config {data_id!r} must contain a JSON object"
            if required:
                raise RuntimeError(message)
            logger.warning("%s; using local environment fallback", message)
            return {}

        fields = self.settings_cls.model_fields
        # Map validation aliases as well as canonical field names. This keeps
        # hand-written documents using legacy names such as
        # ``codex_bridge_token`` compatible with the existing env contract.
        field_keys: dict[str, str] = {}
        for field_name, field in fields.items():
            field_keys[_normalise_key(field_name)] = field_name
            alias = field.validation_alias
            if isinstance(alias, str):
                field_keys[_normalise_key(alias)] = field_name
            elif isinstance(alias, AliasChoices):
                for choice in alias.choices:
                    if isinstance(choice, str):
                        field_keys[_normalise_key(choice)] = field_name
        result: dict[str, Any] = {}
        for raw_key, value in _flatten_config(payload).items():
            # A JSON ``null`` is treated as an omitted setting. This lets an
            # operator remove a remote override and fall back to the local
            # value without making a non-nullable Pydantic field invalid.
            if value is None:
                continue
            if (
                raw_key in _NACOS_BOOTSTRAP_FIELDS
                or raw_key not in field_keys
            ):
                continue
            key = field_keys[raw_key]
            if key in _NACOS_LOCAL_ONLY_FIELDS:
                continue
            field = fields[key]
            # Values published by the shell installer are normally native JSON
            # values. Also accept JSON-encoded arrays/objects for hand-written
            # documents, matching pydantic-settings' dotenv behaviour.
            if isinstance(value, str) and self.field_is_complex(field):
                try:
                    value = json.loads(value)
                except (TypeError, ValueError):
                    pass
            # Pydantic treats a validation alias as the input key for a
            # settings source. Emit the preferred alias when one exists;
            # otherwise a canonical field name is sufficient.
            target_key = key
            alias = field.validation_alias
            if isinstance(alias, str):
                target_key = alias
            elif isinstance(alias, AliasChoices) and alias.choices:
                target_key = str(alias.choices[0])
            result[target_key] = value
        if "postgres_dsn" not in result and _POSTGRES_DSN_COMPONENT_FIELDS.intersection(result):
            result["postgres_dsn"] = ""
        if "redis_url" not in result and _REDIS_URL_COMPONENT_FIELDS.intersection(result):
            result["redis_url"] = ""
        return result

    def _bootstrap_values(self) -> dict[str, Any]:
        """Collect bootstrap keys from explicit init, process env and dotenv.

        The source itself is ordered before regular env/dotenv sources so that
        remote values take precedence for application settings. Bootstrap
        values are merged in the opposite direction (init > process env >
        dotenv) and are never returned as remote values.
        """

        merged: dict[str, Any] = {}
        for source in (self.dotenv_settings.env_vars, self.env_settings.env_vars):
            for raw_key, raw_value in source.items():
                key = _normalise_key(raw_key)
                if key in _NACOS_BOOTSTRAP_FIELDS:
                    value = _non_empty(raw_value)
                    if value is not None:
                        merged[key] = value
        for raw_key, raw_value in self.current_state.items():
            key = _normalise_key(raw_key)
            if key in _NACOS_BOOTSTRAP_FIELDS:
                value = _non_empty(raw_value)
                if value is not None:
                    merged[key] = value
        # ``current_state`` contains explicit init kwargs and must win over
        # process env. The maps above are already dotenv < env < init.
        return merged


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def _as_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    # Match the validated Settings field and keep a malformed bootstrap value
    # from turning startup into an unexpectedly long blocking request.
    return parsed if 0 < parsed <= 30 else default


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

    postgres_dsn: str = ""
    postgres_host: str = "postgres"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_database: str = "xsentinel"
    postgres_user: str = "xsentinel"
    postgres_password: str = "xsentinel"
    postgres_pool_size: int = Field(default=3, ge=1, le=100)
    postgres_max_overflow: int = Field(default=2, ge=0, le=200)
    postgres_pool_recycle_seconds: int = Field(default=1800, ge=60)

    redis_url: str = ""
    redis_host: str = "redis"
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_db: int = Field(default=0, ge=0, le=15)
    redis_password: str = ""
    redis_socket_timeout_seconds: float = Field(default=3.0, gt=0, le=30)

    # Distributed control plane. Empty values retain the single-host collector.
    service_auth_url: str = ""
    service_client_secret_file: str = "/etc/xsentinel/secrets/backend.secret"
    monitor_center_url: str = ""
    monitor_request_timeout_seconds: float = Field(default=4.0, gt=0, le=30)
    monitor_stale_seconds: float = Field(default=45.0, ge=10, le=300)
    nacos_server_addr: str = ""
    nacos_namespace: str = "public"
    nacos_group: str = "X_SENTINEL"
    nacos_username: str = ""
    nacos_password: str = ""
    # Nacos Config bootstrap. These values are intentionally local bootstrap
    # settings; all application settings may be supplied by the remote JSON.
    nacos_config_enabled: bool = True
    nacos_config_data_id: str = "x-sentinel-config.json"
    nacos_config_group: str = ""
    nacos_config_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    nacos_config_required: bool = False
    nacos_service_name: str = "xsentinel-backend"
    nacos_advertise_ip: str = ""
    nacos_service_port: int = Field(default=8200, ge=1, le=65535)

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
    worker_metrics_port: int = Field(default=8201, ge=0, le=65535)
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
    ai_worker_metrics_port: int = Field(default=8202, ge=0, le=65535)

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
    qq_worker_port: int = Field(default=8203, ge=0, le=65535)
    qq_worker_metrics_port: int = Field(default=8004, ge=0, le=65535)

    xhs_job_timeout_seconds: float = Field(default=300.0, ge=30, le=600)
    xhs_job_result_ttl_seconds: int = Field(default=300, ge=60, le=3600)
    xhs_browser_pool_size: int = Field(default=1, ge=1, le=32)
    xhs_browser_max_concurrency: int = Field(default=1, ge=1, le=32)
    xhs_worker_heartbeat_ttl_seconds: int = Field(default=30, ge=10, le=300)
    xhs_worker_metrics_port: int = Field(default=8005, ge=0, le=65535)
    xhs_transport: Literal["redis", "http"] = "redis"
    xhs_service_name: str = "xsentinel-xhs-worker"
    xhs_service_port: int = Field(default=8006, ge=1, le=65535)
    xhs_service_advertise_ip: str = ""
    xhs_service_advertise_port: int = Field(default=8006, ge=1, le=65535)
    service_auth_public_key_file: str = "/run/xsentinel/public.pem"

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings,
        env_settings: EnvSettingsSource,
        dotenv_settings: DotEnvSettingsSource,
        file_secret_settings,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Nacos is deliberately before env/dotenv: once a remote key exists it
        # is authoritative, while local values remain a useful fallback for
        # first boot and for fields not yet migrated. Explicit constructor
        # values retain the highest priority through init_settings.
        nacos_settings = NacosConfigSettingsSource(
            settings_cls,
            env_settings=env_settings,
            dotenv_settings=dotenv_settings,
            _init_state=init_settings._init_state,
        )
        return (
            init_settings,
            nacos_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Settings:
        if not self.postgres_dsn:
            user = quote(self.postgres_user, safe="")
            password = quote(self.postgres_password, safe="")
            database = quote(self.postgres_database, safe="")
            self.postgres_dsn = (
                f"postgresql+asyncpg://{user}:{password}@{self.postgres_host}:"
                f"{self.postgres_port}/{database}"
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
            parsed_postgres_password = unquote(urlsplit(self.postgres_dsn).password or "")
            if (
                not parsed_postgres_password
                or parsed_postgres_password == "xsentinel"
                or placeholder(parsed_postgres_password)
            ):
                raise ValueError(
                    "PostgreSQL password must be non-empty and non-placeholder in production"
                )
            if len(self.x_token_encryption_key) < 32 or placeholder(self.x_token_encryption_key):
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
