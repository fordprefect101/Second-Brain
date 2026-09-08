"""Configuration, validated once at import.

Fail fast and loudly. A local tool that starts successfully and then throws on the
first request is worse than one that refuses to start with a clear reason — the
error surfaces far from its cause, usually in the UI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """Configuration is missing or unusable. Raised at startup, never mid-request."""


@dataclass(frozen=True)
class Config:
    database_url: str

    # None when no vault is configured. An unconfigured integration is a normal
    # state for this app, so it must not prevent startup — the /notes routes
    # return 503 with an actionable message instead.
    vault_path: Path | None = None

    # The Vite dev server. Narrow by default: this app is local-only and
    # single-user (Plan.md §22 — least privilege applies to CORS too).
    cors_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    @property
    def safe_database_url(self) -> str:
        """DATABASE_URL with the password stripped, safe to log or return in /health."""
        if "@" not in self.database_url:
            return self.database_url
        scheme, _, rest = self.database_url.partition("://")
        _, _, host = rest.rpartition("@")
        return f"{scheme}://***@{host}"


def load_config() -> Config:
    database_url = os.getenv("DATABASE_URL", "").strip()

    if not database_url:
        raise ConfigError(
            "DATABASE_URL is not set.\n"
            f"  cp {REPO_ROOT / '.env.example'} {REPO_ROOT / '.env'}\n"
            "  then set POSTGRES_PASSWORD and update the password inside DATABASE_URL."
        )

    if not database_url.startswith(("postgresql://", "postgres://")):
        raise ConfigError(
            f"DATABASE_URL does not look like a Postgres URL: {database_url[:20]}..."
        )

    # Caught here rather than as a confusing auth failure from Postgres later.
    if "replace-with-a-local-password" in database_url:
        raise ConfigError(
            "DATABASE_URL still contains the placeholder password from .env.example. "
            "Set a real local password in .env, in both POSTGRES_PASSWORD and "
            "DATABASE_URL."
        )

    raw_vault = os.getenv("OBSIDIAN_VAULT_PATH", "").strip()
    vault_path = Path(raw_vault).expanduser() if raw_vault else None

    return Config(database_url=database_url, vault_path=vault_path)


config = load_config()
