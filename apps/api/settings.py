"""Centralized environment configuration.

Loaded once at startup from the process environment (which `python-dotenv`
populates from `.env` if present). All other modules import `get_settings()` —
NEVER read `os.environ` directly. This is the trust boundary for credentials.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # LLM
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # MSSQL
    mssql_ppe_server: str = ""
    mssql_ppe_database: str = ""
    mssql_ppe_user: str = ""
    mssql_ppe_password: str = ""

    # Cosmos
    cosmos_ppe_endpoint: str = ""
    cosmos_ppe_key: str = ""
    cosmos_ppe_database: str = ""

    # Redis
    redis_ppe_url: str = ""

    # Datadog
    datadog_api_key: str = ""
    datadog_app_key: str = ""
    datadog_site: str = "datadoghq.com"

    # K8s
    kubeconfig: str = ""

    # Local
    database_url: str = "postgresql://sherlock:sherlock_local_dev@localhost:5433/sherlock"
    sherlock_log_level: str = "INFO"
    sherlock_release: str = "ppe"
    sherlock_investigations_dir: Path = Path("./investigations")
    sherlock_repos_dir: Path = Path("./repos")
    sherlock_db_path: Path = Path("./sherlock.db")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
