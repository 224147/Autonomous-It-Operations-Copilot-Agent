"""Application configuration. All values come from environment variables (or a .env file).

Secrets are never hardcoded; in production they must be supplied through the
environment, a Kubernetes secret, or HashiCorp Vault (see backend/vault.py).
"""
from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_EPHEMERAL_SECRET: str | None = None


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- general -----------------------------------------------------------
    app_name: str = "Autonomous IT Operations Copilot"
    app_version: str = "1.0.0"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = False
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000"
    worker_enabled: bool = True
    worker_concurrency: int = 2
    seed_demo_data: bool = False
    metrics_token: SecretStr = SecretStr("")

    # --- datastores --------------------------------------------------------
    database_url: str = "sqlite:///./data/copilot.db"
    db_auto_create: bool = True
    redis_url: str = ""
    chroma_persist_dir: str = "./data/chroma"
    chroma_host: str = ""
    chroma_port: int = 8000
    kb_seed_dir: str = "./knowledge_base"

    # --- authentication / security -----------------------------------------
    jwt_secret: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 7
    login_max_attempts: int = 5
    login_lockout_minutes: int = 15
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: SecretStr = SecretStr("")
    ingest_api_keys: SecretStr = SecretStr("")

    # --- Azure Entra ID (SSO) ----------------------------------------------
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_role_map: str = ""  # JSON object: {"<group-object-id>": "admin", ...}
    entra_default_role: str = "viewer"

    # --- HashiCorp Vault ---------------------------------------------------
    vault_addr: str = ""
    vault_token: SecretStr = SecretStr("")
    vault_k8s_role: str = ""
    vault_namespace: str = ""
    vault_kv_mount: str = "secret"
    vault_secret_path: str = "autonomous-it-ops-copilot"  # noqa: S105 (a path, not a secret)

    # --- LLM / embeddings ---------------------------------------------------
    llm_provider: Literal["auto", "openai", "azure", "none"] = "auto"
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = ""
    openai_chat_model: str = "gpt-4o"
    openai_embedding_model: str = "text-embedding-3-large"
    azure_openai_endpoint: str = ""
    azure_openai_api_key: SecretStr = SecretStr("")
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_chat_deployment: str = ""
    azure_openai_embedding_deployment: str = ""
    llm_timeout_seconds: float = 30.0

    # --- remediation ---------------------------------------------------------
    remediation_mode: Literal["dry_run", "live"] = "dry_run"
    remediation_allowed_namespaces: str = ""  # empty = any namespace in the service inventory
    remediation_cooldown_seconds: int = 300
    auto_approve_max_risk: Literal["none", "low"] = "none"
    approval_ttl_minutes: int = 60
    cache_redis_url: str = ""
    aws_region: str = ""
    ticket_webhook_url: str = ""
    ticket_webhook_token: SecretStr = SecretStr("")
    verify_wait_seconds: int = 60
    verify_poll_seconds: int = 10
    max_remediation_attempts: int = 2

    # --- monitoring integrations ---------------------------------------------
    prometheus_url: str = ""
    prometheus_queries_file: str = ""
    loki_url: str = ""
    poll_interval_seconds: int = 30
    detection_sweep_seconds: int = 30
    detection_window_minutes: int = 15
    incident_dedup_minutes: int = 60
    signal_retention_per_series: int = 3000

    # --- platform self-observability -----------------------------------------
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "autonomous-it-ops-copilot"

    # ----------------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return _csv(self.cors_origins)

    @property
    def allowed_namespaces(self) -> list[str]:
        return _csv(self.remediation_allowed_namespaces)

    @property
    def ingest_key_list(self) -> list[str]:
        return _csv(self.ingest_api_keys.get_secret_value())

    @property
    def jwt_secret_value(self) -> str:
        global _EPHEMERAL_SECRET
        value = self.jwt_secret.get_secret_value()
        if value:
            return value
        if _EPHEMERAL_SECRET is None:
            _EPHEMERAL_SECRET = secrets.token_urlsafe(48)
            logger.warning("JWT_SECRET is not set; using an ephemeral secret (tokens are invalidated on restart)")
        return _EPHEMERAL_SECRET

    def validate_for_runtime(self) -> None:
        """Fail fast on unsafe production configuration."""
        if not self.is_production:
            return
        problems: list[str] = []
        if len(self.jwt_secret.get_secret_value()) < 32:
            problems.append("JWT_SECRET must be set to at least 32 characters")
        if self.database_url.startswith("sqlite"):
            problems.append("DATABASE_URL must point to PostgreSQL in production")
        if "*" in self.cors_origin_list:
            problems.append("CORS_ORIGINS must not contain '*' in production")
        if problems:
            raise RuntimeError("Invalid production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.vault_addr:
        from backend.vault import load_vault_overrides

        overrides = load_vault_overrides(settings)
        if overrides:
            settings = Settings(**overrides)
    return settings


def reset_settings_cache() -> None:
    get_settings.cache_clear()
