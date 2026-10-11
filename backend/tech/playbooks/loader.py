"""Load the client facts and playbooks (Tech.MD §6).

Each YAML file is used two ways: parsed into a model for code (lint, gate, close-out), and passed
verbatim, comments included, to the investigator, because the comments carry the reasoning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

HERE = Path(__file__).resolve().parent
CLIENT_FACTS = "client_facts.yaml"


class GroupHandling(BaseModel):
    group: str
    handling: Literal["automatic", "technician"]
    note: str
    aka: list[str] = []  # other names a needs_technician item may use for it (close-out's DoD probe)


class ClientFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client: str
    usageLocation: str
    upn_format: str | None
    default_sku: str
    approvers: dict[str, list[str]]
    group_handling: list[GroupHandling] = []
    license_reserve: list[dict[str, str]] = []
    non_graph_access: list[dict[str, str]] = []
    known_service_accounts: list[str] = []
    oob_contacts: str
    autonomy: dict[str, Literal["A1", "A2", "A3"]]

    def is_approver(self, email: str, scope: str) -> bool:
        return email.strip().lower() in {a.lower() for a in self.approvers.get(scope, [])}


class Playbook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str
    goal: str
    must_investigate: list[str]
    questions_to_resolve: list[str] = []
    triage_checks: list[dict[str, str]] = []
    definition_of_done: list[dict[str, Any]]
    allowed_actions: list[str]
    gotchas: list[str] = []
    never: list[str] = []

    @property
    def must_investigate_tools(self) -> list[str]:
        """'users.get(peer)' -> 'users.get'."""
        return [item.split("(")[0].strip() for item in self.must_investigate]


def _text(name: str, domain: str) -> str:
    return (HERE / name).read_text().replace("{domain}", domain)


def intents() -> list[str]:
    return sorted(p.stem for p in HERE.glob("*.yaml") if p.name != CLIENT_FACTS)


def load_client_facts(domain: str) -> ClientFacts:
    return ClientFacts.model_validate(yaml.safe_load(_text(CLIENT_FACTS, domain)))


def load_playbook(intent: str, domain: str) -> Playbook:
    if intent not in intents():
        raise KeyError(f"no playbook for {intent!r}")
    playbook = Playbook.model_validate(yaml.safe_load(_text(f"{intent}.yaml", domain)))
    if playbook.intent != intent:
        raise ValueError(f"{intent}.yaml declares intent {playbook.intent!r}")
    return playbook


def load_playbooks(for_intents: list[str], domain: str) -> dict[str, Playbook]:
    """The playbooks for the triaged intents that have one (none for 'unknown' or a no-playbook intent)."""
    return {i: load_playbook(i, domain) for i in for_intents if i in intents()}


def prompt_text(for_intents: list[str], domain: str) -> str:
    """Client facts plus the playbooks for the triaged intents, as YAML, for the investigator's instructions."""
    parts = [_text(CLIENT_FACTS, domain)]
    parts += [_text(f"{i}.yaml", domain) for i in for_intents if i in intents()]
    return "\n---\n".join(parts)
