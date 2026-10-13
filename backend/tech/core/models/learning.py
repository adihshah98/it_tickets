"""What a playbook is learned from (Build-W4 "Every learning source produces the same thing").

Each source (an agent run, a technician's console run, a written playbook, audit logs) is reduced to a
DraftSource; one drafter turns any DraftSource into a playbook. A DraftSource carries no ticket text: the
triage is reduced to the intents, the entities and the requester, without the quoted asks
(learning/sources.py), so a "remember for next time" in a ticket can't reach a playbook (eval #28).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

SourceKind = Literal["agent_run", "technician_run", "written", "audit_log"]


class ObservedStep(BaseModel):
    """A step a run did and verified: what it changed, why, and what the read-back showed."""

    action: str
    params: dict[str, Any] = {}
    rationale: str | None = None
    verified: dict[str, Any] = {}       # the verify read-back (step_verified.observed)
    skipped: str | None = None          # "already true": verified without a write
    actor: str = "agent"                # or technician:{name} (Week 4 Day 20)


class ObservedRead(BaseModel):
    tool: str
    args: dict[str, Any] = {}
    ok: bool = True


class DraftSource(BaseModel):
    kind: SourceKind
    intent: str                          # proposed; the person approving can rename it
    run_id: int | None = None
    triage: dict[str, Any] = {}          # intents, entities, requester: never the ticket's asks
    summary: str | None = None           # the plan's one-line summary
    assumptions: list[str] = []
    confirmations: list[dict[str, Any]] = []  # what the approver was asked to confirm, and the evidence
    steps: list[ObservedStep] = []
    reads: list[ObservedRead] = []
    findings: list[str] = []
    notes: list[str] = []                # from the technician: trusted (authenticated), unlike a ticket
    allowed_actions: list[str] = []      # written: the actions the technician named; runs: those observed
    text: str | None = None              # written: the technician's prose or YAML
    by: str | None = None

    @property
    def observed_actions(self) -> set[str] | None:
        """The actions the run verified with a write, or None for a source with no run (written)."""
        if self.kind == "written":
            return None
        return {s.action for s in self.steps if not s.skipped}
