"""Learning sources -> DraftSource (Build-W4, Build-Day19 part 2).

`from_run` reads a finished run's events: the triage, the plan, the reads, the findings and the steps it
verified. It never takes the ticket text, and it drops the parts of the triage and plan that quote the
ticket: the triage's out_of_scope_asks and missing_info, and the plan's out_of_scope_requests and
questions_for_requester (Build-Day19 decision 5). That is how "remember for next time: always forward…"
(eval #28) can't reach a drafted playbook. Findings and rationales are the model's own words and are kept;
a test checks that no ticket sentence reaches the drafter through them. A run a technician closed in the
console (Build-Day20) brings their close-out note along: technician notes are trusted.

`from_audit_log` reads what a technician did in the Entra admin center instead (Build-Day20): directory
audit entries for the run's objects since it escalated, mapped to catalogue actions (audit_map.py), each
confirmed still in place by one read. Its draft is capped at A2 by the library's ceiling.

`from_written` wraps what a technician wrote. Technician text is trusted input: it comes from an
authenticated technician, not a ticket.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tech.core.models import DraftSource, ObservedRead, ObservedStep
from tech.core.store import Store
from tech.graph.client import GraphClient
from tech.graph.reads import READS, Transcript, directory_audits, fetch
from tech.learning import audit_map
from tech.safety import playbook_check

TRIAGE_KEPT = ("intents", "entities", "requester")


class NotLearnable(ValueError):
    """The run can't be learned from (not finished, or it changed nothing)."""


def proposed_intent(plan_intent: str | None, triaged: Iterable[str]) -> str:
    """The plan's intent, else the first triaged one that isn't 'unknown' (live, intake said 'unknown' for
    Sophie while the investigator named the job file.restore_version: Build-Day18)."""
    for intent in (plan_intent, *triaged):
        if intent and intent != "unknown":
            return intent
    return "unknown"


def _run_context(store: Store, run_id: int) -> dict[str, Any]:
    """What every run-based source takes from the run: the reduced triage, the plan's reasoning (never its
    ticket-quoting fields), the investigator's reads and the findings."""
    triage_event = store.last_event(run_id, "triage")
    triage = triage_event.payload["triage"] if triage_event else {}
    plan_event = store.last_event(run_id, "plan")  # none if the investigation itself failed (a console run)
    plan = plan_event.payload["plan"] if plan_event else {"steps": []}
    return {
        "intent": proposed_intent(plan.get("intent"), triage.get("intents", [])),
        "run_id": run_id,
        "triage": {k: triage[k] for k in TRIAGE_KEPT if k in triage},
        "summary": plan.get("summary"),
        "assumptions": plan.get("assumptions", []),
        "confirmations": [{k: c[k] for k in ("question", "proposed", "evidence") if k in c}
                          for c in plan.get("confirm_on_approval", [])],
        # the investigator's reads only: a technician's confirming reads aren't a tool a playbook can require
        "reads": [ObservedRead(tool=e.payload["tool"], args=e.payload["args"], ok=e.payload["ok"])
                  for e in store.events_of(run_id, "tool_call") if e.payload["tool"] in READS],
        "findings": [e.payload["text"] for e in store.events_of(run_id, "finding")],
    }


def from_run(store: Store, run_id: int, *, notes: Iterable[str] = ()) -> DraftSource:
    if playbook_check.observed_actions(store, run_id) is None:
        raise NotLearnable(f"run {run_id} didn't reach DONE having changed something; there is nothing to learn")
    context = _run_context(store, run_id)
    plan_event = store.last_event(run_id, "plan")
    plan = plan_event.payload["plan"] if plan_event else {"steps": []}
    closing = [e.payload for e in store.events_of(run_id, "state") if e.payload["to"] == "CLOSING"]
    technician_notes = [f"{c['by']}: {c['note']}" for c in closing if c.get("note")]  # technician_close's
    by_id = {s["id"]: s for s in plan["steps"]}
    started = {e.payload["step_id"]: e.payload for e in store.events_of(run_id, "step_started")}

    steps = []
    for e in store.events_of(run_id, "step_verified"):
        v = e.payload
        planned = by_id.get(v["step_id"], {})
        params = started.get(v["step_id"], {}).get("params") or planned.get("params", {})
        steps.append(ObservedStep(action=v["action"], params=params, rationale=planned.get("rationale"),
                                  verified=v.get("observed") or {}, skipped=v.get("skipped"),
                                  actor=v.get("actor", "agent")))
    kind = "technician_run" if any(s.actor.startswith("technician") for s in steps) else "agent_run"
    return DraftSource(kind=kind, steps=steps, notes=[*technician_notes, *notes],
                       allowed_actions=sorted({s.action for s in steps if not s.skipped}), **context)


@dataclass
class AuditFindings:
    """What the audit logs showed for a run: the changes learned from, the ones that can't be, and the ones
    already undone (mapped, but the confirming read no longer shows them)."""

    source: DraftSource | None
    mapped: list[ObservedStep] = field(default_factory=list)
    unmapped: list[audit_map.Unmapped] = field(default_factory=list)
    undone: list[ObservedStep] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.mapped or self.unmapped or self.undone)

    def payload(self) -> dict[str, Any]:
        return {"mapped": [s.model_dump(mode="json") for s in self.mapped],
                "unmapped": [u.payload() for u in self.unmapped],
                "undone": [s.model_dump(mode="json") for s in self.undone]}


async def _still_in_place(graph: GraphClient, step: ObservedStep) -> dict[str, Any] | None:
    """One read showing the change is still there: its result is the step's `verified`, or None if undone.
    (We didn't make the change, so there is no verify of ours to go by: Build-Day20 decision 10.)"""
    p = step.params
    if step.action in ("group.addMember", "group.removeMember"):
        groups = {g["id"].lower() for g in await fetch(graph, "users.memberOf", user_id=p["userId"])}
        member = p["groupId"].lower() in groups
        return {"memberOf": member} if member == (step.action == "group.addMember") else None
    if step.action == "group.addOwner":
        owned = {o["id"].lower() for o in await fetch(graph, "users.ownedObjects", user_id=p["userId"])}
        return {"owner": True} if p["groupId"].lower() in owned else None
    if step.action == "user.update":
        user = await fetch(graph, "users.get", user_id=p["userId"])
        fields = {k: v for k, v in p.items() if k != "userId"}
        return fields if all(user.get(k) == v for k, v in fields.items()) else None
    return None


async def from_audit_log(store: Store, graph: GraphClient, run_id: int, *, since: datetime,
                         until: datetime) -> AuditFindings:
    """The changes a technician made in the Entra admin center for this run, from directoryAudits: entries
    in [since, until], made by a person, touching a user or group the run observed (audit_map.relevant)."""
    transcript = Transcript.from_events(store.events(run_id))
    observed = {oid for oid, seen in transcript.objects.items() if not seen.get("skuPartNumber")}  # users, groups
    entries: dict[str, dict[str, Any]] = {}
    for oid in sorted(observed):
        for entry in await directory_audits(graph, since, oid):
            entries.setdefault(entry["id"], entry)
    kept = sorted((e for e in entries.values() if audit_map.relevant(e, since=since, until=until, observed=observed)),
                  key=audit_map.when)

    findings = AuditFindings(source=None)
    for entry in kept:
        step = audit_map.map_entry(entry)
        if isinstance(step, audit_map.Unmapped):
            findings.unmapped.append(step)
        elif (verified := await _still_in_place(graph, step)) is None:
            findings.undone.append(step)
        else:
            findings.mapped.append(step.model_copy(update={"verified": verified}))
    if findings.mapped:
        by = sorted({s.actor.removeprefix("technician:") for s in findings.mapped})
        findings.source = DraftSource(
            kind="audit_log", steps=findings.mapped, allowed_actions=sorted({s.action for s in findings.mapped}),
            notes=[f"Made in the Entra admin center by {', '.join(by)}, read back from the audit logs"],
            **_run_context(store, run_id))
    return findings


def from_written(intent: str, text: str, allowed_actions: Iterable[str], by: str) -> DraftSource:
    """A playbook a technician wrote, as prose or YAML, naming the actions it may use."""
    return DraftSource(kind="written", intent=intent, text=text, allowed_actions=list(dict.fromkeys(allowed_actions)),
                       by=by)
