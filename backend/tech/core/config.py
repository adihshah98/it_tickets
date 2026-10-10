"""Settings, read from the repo's .env (model key) and tenant/.env (Graph app, written by setup_app.py)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


@dataclass(frozen=True)
class Settings:
    tenant_id: str
    client_id: str
    certificate_path: Path
    domain: str
    anthropic_api_key: str | None
    database_url: str
    link_key: str | None = None  # Fernet key for one-time links; see ensure_link_key

    @classmethod
    def load(cls, repo: Path = REPO) -> Settings:
        env = read_env_file(repo / "tenant" / ".env") | read_env_file(repo / ".env") | dict(os.environ)
        missing = [k for k in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_CERTIFICATE_PATH", "M365_DOMAIN")
                   if not env.get(k)]
        if missing:
            raise RuntimeError(f"Missing settings {missing}. Run tenant/setup_app.py first.")
        return cls(
            tenant_id=env["AZURE_TENANT_ID"],
            client_id=env["AZURE_CLIENT_ID"],
            certificate_path=repo / "tenant" / env["AZURE_CLIENT_CERTIFICATE_PATH"],
            domain=env["M365_DOMAIN"],
            anthropic_api_key=env.get("ANTHROPIC_API_KEY") or None,
            database_url=env.get("DATABASE_URL", f"sqlite:///{repo / 'data' / 'tech.db'}"),
            link_key=env.get("LINK_KEY") or None,
        )


def ensure_link_key(settings: Settings, repo: Path = REPO) -> str:
    """The Fernet key that encrypts TAPs in one-time links. Created once and appended to the repo's .env."""
    if settings.link_key:
        return settings.link_key
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    env_file = repo / ".env"
    text = env_file.read_text() if env_file.exists() else ""
    sep = "" if not text or text.endswith("\n") else "\n"
    note = "# Encrypts TAPs in one-time links (created by the app). Never commit."
    env_file.write_text(f"{text}{sep}{note}\nLINK_KEY={key}\n")
    return key
