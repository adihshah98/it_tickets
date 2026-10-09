"""Settings, read from the repo's .env (model key) and tenant/.env (Graph app, written by setup_app.py)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


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
        )
