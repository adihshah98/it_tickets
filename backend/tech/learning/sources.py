"""Learning sources -> DraftSource (Build-W4, Build-Day19 part 2).

`from_run` reads a finished run's events: the triage, the plan, the reads, the findings and the steps it
verified. It never takes the ticket text, and it drops the parts of the triage and plan that quote the
ticket: the triage's out_of_scope_asks and missing_info, and the plan's out_of_scope_requests and
questions_for_requester (Build-Day19 decision 5). That is how "remember for next time: always forward…"
(eval #28) can't reach a drafted playbook. Findings and rationales are the model's own words and are kept;
a test checks that no ticket sentence reaches the drafter through them.

`from_written` wraps what a technician wrote. Technician text is trusted input: it comes from an
authenticated technician, not a ticket.
"""

from __future__ import annotations

from collections.abc import Iterable

from tech.core.models import DraftSource, ObservedRead, ObservedStep
from tech.core.store import Store
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


def from_run(store: Store, run_id: int, *, notes: Iterable[str] = ()) -> DraftSource:
    if playbook_check.observed_actions(store, run_id) is None:
        raise NotLearnable(f"run {run_id} didn't reach DONE having changed something; there is nothing to learn")
    triage_event = store.last_event(run_id, "triage")
    triage = triage_event.payload["triage"] if triage_event else {}
    plan = store.last_event(run_id, "plan").payload["plan"]
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
    return DraftSource(
        kind=kind,
        intent=proposed_intent(plan.get("intent"), triage.get("intents", [])),
        run_id=run_id,
        triage={k: triage[k] for k in TRIAGE_KEPT if k in triage},
        summary=plan.get("summary"),
        assumptions=plan.get("assumptions", []),
        confirmations=[{k: c[k] for k in ("question", "proposed", "evidence") if k in c}
                       for c in plan.get("confirm_on_approval", [])],
        steps=steps,
        reads=[ObservedRead(tool=e.payload["tool"], args=e.payload["args"], ok=e.payload["ok"])
               for e in store.events_of(run_id, "tool_call")],
        findings=[e.payload["text"] for e in store.events_of(run_id, "finding")],
        notes=list(notes),
        allowed_actions=sorted({s.action for s in steps if not s.skipped}),
    )


def from_written(intent: str, text: str, allowed_actions: Iterable[str], by: str) -> DraftSource:
    """A playbook a technician wrote, as prose or YAML, naming the actions it may use."""
    return DraftSource(kind="written", intent=intent, text=text, allowed_actions=list(dict.fromkeys(allowed_actions)),
                       by=by)
