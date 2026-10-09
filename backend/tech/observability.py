"""Logfire setup. Call configure_logfire() once per process, before creating agents or clients."""

from __future__ import annotations

import logfire

from tech.config import REPO

# Logfire already scrubs password/secret/token/api_key/auth/... by default.
# Add the Graph field that carries a Temporary Access Pass, so it never reaches traces.
EXTRA_SCRUB_PATTERNS = ["temporaryAccessPass", "temporary_access_pass", r"\btap\b"]


def configure_logfire(*, environment: str = "dev", send: bool = True) -> None:
    logfire.configure(
        service_name="ai-technician",
        environment=environment,
        data_dir=REPO / ".logfire",
        send_to_logfire="if-token-present" if send else False,
        console=False,
        scrubbing=logfire.ScrubbingOptions(extra_patterns=EXTRA_SCRUB_PATTERNS),
    )
    logfire.instrument_pydantic_ai()
    # Request/response bodies are not captured by default; keep it that way (TAPs travel in bodies).
    logfire.instrument_httpx()
