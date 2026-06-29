"""Centralized environment configuration.

Loaded once at startup from the process environment (which `python-dotenv`
populates from `.env` if present). All other modules import `get_settings()` —
NEVER read `os.environ` directly. This is the trust boundary for credentials.

Multi-env: per-environment credentials (PostgreSQL, Cosmos, Redis, KUBECONFIG, k8s
namespace conventions) are looked up dynamically via `env_config(env)` from
env vars named `<TOOL>_<ENV>_<FIELD>` — adding a new env is just `.env` work.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from apps.api.env_context import EnvCreds


# pydantic-settings loads `.env` into the Settings model but NOT into
# os.environ. The per-env config below uses dynamic `os.getenv()` lookups
# (`PG_<ENV>_*`, etc.), so we explicitly populate os.environ from .env at
# import time. `override=False` keeps any value already set in the real env
# (e.g. CI secrets) authoritative over the file.
load_dotenv(".env", override=False)


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

    # Datadog (env-agnostic — single tenant)
    datadog_api_key: str = ""
    datadog_app_key: str = ""
    datadog_site: str = "datadoghq.com"

    # Multi-env config — list of envs this Sherlock instance can talk to.
    # Adding a new env: append to this list + add PG_<ENV>_*, COSMOS_<ENV>_*,
    # REDIS_<ENV>_*, KUBECONFIG_<ENV>, K8S_<ENV>_NAMESPACE, K8S_<ENV>_POD_SUFFIX.
    sherlock_envs: str = "ppe"
    sherlock_default_env: str = "ppe"

    # Local infra
    database_url: str = "postgresql://sherlock:sherlock_local_dev@localhost:5433/sherlock"
    sherlock_log_level: str = "INFO"
    sherlock_release: str = "ppe"
    sherlock_investigations_dir: Path = Path("./investigations")
    sherlock_repos_dir: Path = Path("./repos")
    sherlock_db_path: Path = Path("./sherlock.db")
    sherlock_demo_mode: bool = False

    # If true, every server startup wipes all sessions, messages, audit log,
    # and matching investigations/<rca_id>/ scratch dirs. Useful for demo
    # builds that should always launch with a clean slate. Leave off (default)
    # while developing — restarts are common and you want history to survive.
    sherlock_ephemeral_sessions: bool = False

    # Proactive mode (Pick 1): scheduled briefings + anomaly watcher.
    # Master switch — leave off in CI / unit tests.
    sherlock_proactive_enabled: bool = False
    # How often the briefing cron tick fires. Default 6h (4 briefings/day);
    # set lower for demos, higher for prod once tuned.
    sherlock_briefing_interval_seconds: int = 21600
    # Run one briefing immediately on startup so the Briefings tab is never
    # empty when judges open the demo.
    sherlock_briefing_on_startup: bool = True

    def configured_envs(self) -> list[str]:
        """The envs this instance is configured to talk to, in display order."""
        return [e.strip().lower() for e in self.sherlock_envs.split(",") if e.strip()]

    def env_config(self, env: str | None = None) -> EnvCreds:
        """Per-env credentials. `env=None` returns config for the default env.

        Reads `<TOOL>_<ENV>_<FIELD>` from process env at call time so changes
        to `.env` (e.g. plugging in stage creds while the server is running)
        are picked up on next request without restart.
        """
        e = (env or self.sherlock_default_env).lower()
        E = e.upper()

        def _get(name: str, default: str = "") -> str:
            return os.getenv(name, default)

        return EnvCreds(
            env=e,
            cosmos_endpoint=_get(f"COSMOS_{E}_ENDPOINT"),
            cosmos_key=_get(f"COSMOS_{E}_KEY"),
            cosmos_database=_get(f"COSMOS_{E}_DATABASE"),
            redis_url=_get(f"REDIS_{E}_URL"),
            redis_host=_get(f"REDIS_{E}_HOST"),
            redis_port=int(_get(f"REDIS_{E}_PORT", "6380") or "6380"),
            redis_key=_get(f"REDIS_{E}_KEY"),
            redis_tls=_get(f"REDIS_{E}_TLS", "true").lower() == "true",
            kubeconfig=_get(f"KUBECONFIG_{E}"),
            k8s_namespace=_get(f"K8S_{E}_NAMESPACE", e),
            k8s_pod_suffix=_get(f"K8S_{E}_POD_SUFFIX", f"-{e}"),
            pg_host=_get(f"PG_{E}_HOST"),
            pg_port=int(_get(f"PG_{E}_PORT", "5432") or "5432"),
            pg_database=_get(f"PG_{E}_DATABASE"),
            pg_user=_get(f"PG_{E}_USER"),
            pg_password=_get(f"PG_{E}_PASSWORD"),
            pg_sslmode=_get(f"PG_{E}_SSLMODE", "require"),
            pg_search_path=_get(f"PG_{E}_SEARCH_PATH", "trk"),
        )

    def env_availability(self, env: str) -> dict:
        """Per-tool availability flags for an env. Used by /envs endpoint and
        the frontend to grey out un-configured envs/tools."""
        cfg = self.env_config(env)
        return {
            "cosmos": bool(cfg.cosmos_endpoint and cfg.cosmos_key),
            "redis": bool(cfg.redis_url or (cfg.redis_host and cfg.redis_key)),
            "kubectl": bool(cfg.kubeconfig and Path(cfg.kubeconfig).is_file()),
            "datadog": bool(self.datadog_api_key and self.datadog_app_key),
            "postgres": bool(cfg.pg_host and cfg.pg_user and cfg.pg_password),
        }


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
