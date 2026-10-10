"""Run state machine (Tech.MD §3): drives a ticket from RECEIVED to DONE, ESCALATED or HELD.

    start     intake -> investigation (linter in the loop) -> gate. With nothing to approve, executes.
    approve   an approver approves (or rejects) the plan, with their answers to the confirm questions,
              then the executor runs it. Also releases a HELD run: the approver confirms the requester.
              The API calls the two halves itself: `accept` inline (errors go back to the caller), then
              `execute` as a background task.
    resume    carries on an execution a crashed process left in EXECUTING.

Each call can run in a new process (the CLI): what a run needs is rebuilt from its events: the plan
from `plan`, the intents from `triage`, the approver's answers from `approval`, finished steps and
their outputs from `step_verified`. Every transition is checked against TRANSITIONS.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from pydantic import ValidationError

from tech.agents.intake import MODEL as INTAKE_MODEL
from tech.agents.intake import run_intake
from tech.agents.investigator import MODEL as INVESTIGATOR_MODEL
from tech.agents.investigator import investigate
from tech.core.config import REPO, Settings, ensure_link_key
from tech.core.models import STEP_TYPES, Plan, Triage
from tech.core.observability import configure_logfire
from tech.core.store import RunState, Store
from tech.execution import scheduler
from tech.execution.catalogue import ACTIONS, FOLLOWUP_ACTIONS
from tech.execution.executor import Execution
from tech.graph.client import GraphClient, graph_from_settings
from tech.graph.writes import SecretLeak
from tech.playbooks import ClientFacts, load_client_facts
from tech.safety import gate

S = RunState
TERMINAL = {S.DONE, S.ESCALATED}
TRANSITIONS: dict[RunState, set[RunState]] = {
    S.RECEIVED: {S.TRIAGED},
    S.TRIAGED: {S.INVESTIGATING},
    S.INVESTIGATING: {S.PLAN_PROPOSED},
    S.PLAN_PROPOSED: {S.PLAN_CHECKED},
    S.PLAN_CHECKED: {S.AWAITING_APPROVAL, S.EXECUTING},
    S.AWAITING_APPROVAL: {S.EXECUTING},
    S.HELD: {S.EXECUTING},
    S.EXECUTING: {S.REPLANNING, S.CLOSING},
    S.REPLANNING: {S.EXECUTING},
    S.CLOSING: {S.DONE},
}
for _state in S:  # any live run can escalate or be held (§3)
    if _state not in TERMINAL:
        TRANSITIONS.setdefault(_state, set()).update({S.ESCALATED, S.HELD} - {_state})

ANSWER_KEYS = ("upn_format", "start_date")


class RunError(RuntimeError):
    """A request the run can't take in its current state (e.g. approving a DONE run)."""


@dataclass
class Runtime:
    """What runs need from outside: the store, the tenant and the client's facts."""

    store: Store
    graph: GraphClient
    facts: ClientFacts
    domain: str
    link_key: str | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    @classmethod
    def from_settings(cls, settings: Settings, *, environment: str) -> Runtime:
        """The runtime the CLI and the API use: Logfire, the model key, the store, Graph and the facts."""
        configure_logfire(environment=environment)
        if settings.anthropic_api_key:  # the Anthropic provider reads it from the environment
            os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)
        if settings.database_url.startswith("sqlite:///"):
            (REPO / "data").mkdir(exist_ok=True)
        return cls(store=Store(settings.database_url), graph=graph_from_settings(settings),
                   facts=load_client_facts(settings.domain), domain=settings.domain,
                   link_key=ensure_link_key(settings))


def transition(store: Store, run_id: int, to: RunState, **detail: Any) -> None:
    current = store.get_run(run_id).state
    if to not in TRANSITIONS.get(current, set()):
        raise RunError(f"run {run_id} can't go from {current} to {to}")
    store.set_state(run_id, to, **detail)


# ------------------------------------------------------------------ what a run knows, from its events


def load_plan(store: Store, run_id: int) -> Plan:
    event = store.last_event(run_id, "plan")
    if event is None:
        raise RunError(f"run {run_id} has no plan")
    return Plan.model_validate(event.payload["plan"])


def load_triage(store: Store, run_id: int) -> Triage:
    event = store.last_event(run_id, "triage")
    if event is None:
        raise RunError(f"run {run_id} has no triage")
    return Triage.model_validate(event.payload["triage"])


def effective_plan(store: Store, run_id: int, domain: str) -> Plan:
    """The plan as approved: the proposed plan with the approver's answers applied."""
    approval = store.last_event(run_id, "approval")
    return apply_answers(load_plan(store, run_id), approval.payload["answers"] if approval else {}, domain)


def findings(store: Store, run_id: int) -> list[str]:
    return [e.payload["text"] for e in store.events_of(run_id, "finding")]


# ------------------------------------------------------------------ approver's answers


def apply_answers(plan: Plan, answers: dict[str, str], domain: str) -> Plan:
    """Apply the approver's answers to the confirm questions. They change values, never IDs or actions,
    so the gate's decisions still hold. Raises RunError on an unknown key or a value that doesn't fit."""
    if unknown := set(answers) - set(ANSWER_KEYS):
        raise RunError(f"unknown answer {sorted(unknown)}; answers can set {', '.join(ANSWER_KEYS)}")
    data = plan.model_dump(mode="json", by_alias=True)
    steps = data["steps"]

    if upn := answers.get("upn_format"):
        upn = upn.strip() if "@" in upn else f"{upn.strip()}@{domain}"
        creates = [s for s in steps if s["action"] == "user.create"]
        if len(creates) != 1:
            raise RunError(f"upn_format needs exactly one user.create step, the plan has {len(creates)}")
        params = creates[0]["params"]
        old = {params["userPrincipalName"].casefold(): upn, params["mailNickname"].casefold(): upn.split("@")[0]}
        params["userPrincipalName"], params["mailNickname"] = upn, upn.split("@")[0]
        for s in steps:  # keep verify expectations in line with the new address
            s["expected"] = {k: old.get(v.casefold(), v) if isinstance(v, str) else v
                             for k, v in s["expected"].items()}

    if start := answers.get("start_date"):
        try:
            day = date.fromisoformat(start)
        except ValueError as exc:
            raise RunError(f"start_date must be YYYY-MM-DD, not {start!r}") from exc
        for s in steps:
            key = {"followup.schedule": "runAt", "tap.create": "startDateTime"}.get(s["action"])
            if key and s["params"].get(key):
                when = datetime.fromisoformat(s["params"][key])
                s["params"][key] = when.replace(year=day.year, month=day.month, day=day.day).isoformat()

    try:
        return Plan.model_validate(data)
    except ValidationError as exc:
        raise RunError(f"the answers don't fit the plan: {exc}") from exc


# ------------------------------------------------------------------ the phases


async def start(rt: Runtime, run_id: int, text: str, from_address: str, *,
                intake_model: Any = INTAKE_MODEL, investigator_model: Any = INVESTIGATOR_MODEL) -> RunState:
    """Triage, investigate and gate a new run; execute straight away if nothing needs approval."""
    triage, check = await run_intake(rt.store, run_id, text, from_address, rt.facts, model=intake_model)
    result = await investigate(rt.store, run_id, text, triage, requester_approved=check.approved, graph=rt.graph,
                               facts=rt.facts, domain=rt.domain, model=investigator_model)
    if result.outcome == "planned":
        gate.check(rt.store, run_id, result.plan, requester_approved=check.approved, intents=triage.intents,
                   playbooks=result.playbooks, facts=rt.facts, transcript=result.transcript,
                   findings=result.findings)
    return await proceed(rt, run_id)


async def proceed(rt: Runtime, run_id: int) -> RunState:
    """After the gate: a checked plan with nothing to approve runs now; anything else waits."""
    if rt.store.get_run(run_id).state == S.PLAN_CHECKED:
        await execute(rt, run_id, load_plan(rt.store, run_id))
    return rt.store.get_run(run_id).state


def approval_scopes(store: Store, run_id: int, facts: ClientFacts) -> list[str]:
    """Approver lists that must include whoever approves: each triaged intent's, plus `spend` for a purchase."""
    scopes = [i for i in load_triage(store, run_id).intents if i in facts.approvers]
    if any(e.payload["rule_id"] == "risk.spend" for e in store.events_of(run_id, "gate_decision")):
        scopes.append("spend")
    return scopes


def accept(rt: Runtime, run_id: int, *, by: str, answers: dict[str, str] | None = None,
           reject: bool = False) -> Plan | None:
    """Check and record an approval (or rejection), and move the run on: to EXECUTING with the plan to run
    (returned), or to ESCALATED on a reject (None). Synchronous, so nothing can slip in between the state
    check and the transition: a second approval of the same run is refused."""
    answers = answers or {}
    state = rt.store.get_run(run_id).state
    if state not in (S.AWAITING_APPROVAL, S.HELD):
        raise RunError(f"run {run_id} is {state}; only AWAITING_APPROVAL or HELD runs can be approved")
    by = by.strip().lower()
    if not by.count("@"):
        by = f"{by}@{rt.domain}".lower()
    missing = [s for s in approval_scopes(rt.store, run_id, rt.facts) if not rt.facts.is_approver(by, s)]
    if missing:
        raise RunError(f"{by} is not on the client's approver list for {', '.join(missing)}")
    if blocked := [e.payload["step_id"] for e in rt.store.events_of(run_id, "gate_decision")
                   if e.payload["decision"] == "block"]:
        raise RunError(f"the gate blocked {', '.join(blocked)}; a blocked plan can't be approved")

    plan = apply_answers(load_plan(rt.store, run_id), answers, rt.domain)  # fails before anything is recorded
    rt.store.append_event(run_id, "approval", {
        "by": by, "decision": "reject" if reject else "approve", "answers": answers,
        "released_hold": state == S.HELD,
    })
    if reject:
        transition(rt.store, run_id, S.ESCALATED, kind="rejected", reason=f"{by} rejected the plan",
                   findings=findings(rt.store, run_id))
        return None
    transition(rt.store, run_id, S.EXECUTING)
    return plan


async def approve(rt: Runtime, run_id: int, *, by: str, answers: dict[str, str] | None = None,
                  reject: bool = False) -> RunState:
    plan = accept(rt, run_id, by=by, answers=answers, reject=reject)
    if plan is not None:
        await execute(rt, run_id, plan)
    return rt.store.get_run(run_id).state


async def resume(rt: Runtime, run_id: int) -> RunState:
    state = rt.store.get_run(run_id).state
    if state != S.EXECUTING:
        raise RunError(f"run {run_id} is {state}; only an EXECUTING run can be resumed")
    await execute(rt, run_id, effective_plan(rt.store, run_id, rt.domain))
    return rt.store.get_run(run_id).state


async def execute(rt: Runtime, run_id: int, plan: Plan) -> None:
    if rt.store.get_run(run_id).state != S.EXECUTING:
        transition(rt.store, run_id, S.EXECUTING)
    execution = Execution(rt.store, run_id, rt.graph, rt.domain, link_key=rt.link_key, sleep=rt.sleep)
    try:
        result = await execution.run(plan)
    except SecretLeak as exc:
        transition(rt.store, run_id, S.ESCALATED, kind="secret_guard", reason=str(exc),
                   findings=findings(rt.store, run_id))
        return
    if result.ok:
        transition(rt.store, run_id, S.CLOSING)  # close-out (DoD probes, reply, PSA note) arrives on Day 8
        transition(rt.store, run_id, S.DONE)
        return
    done = [e.payload for e in rt.store.events_of(run_id, "step_verified")]
    by_id = {s.id: s for s in plan.steps}
    transition(
        rt.store, run_id, S.ESCALATED, kind="execution",
        reason=f"{result.failed_step}: {result.error}", failed_step=result.failed_step,
        done=[d["step_id"] for d in done], findings=findings(rt.store, run_id),
        # Offered to the technician as one "roll back this run" button, never run automatically (§9).
        rollback=[{"step_id": d["step_id"], "action": d["action"], "compensate": ACTIONS[d["action"]].compensate}
                  for d in reversed(done) if not d.get("skipped") and d["step_id"] in by_id],
    )


# ------------------------------------------------------------------ follow-ups


async def run_followup(rt: Runtime, followup_id: int) -> bool:
    """Run a scheduled follow-up now (the demo's "run now"). Its step events go to the run that scheduled it."""
    row = scheduler.get(rt.store, followup_id)
    if row.status == "done":
        raise RunError(f"follow-up {followup_id} already ran")
    action = FOLLOWUP_ACTIONS[row.kind]
    if action is None:  # a reminder for a technician; nothing to change
        scheduler.mark(rt.store, followup_id, "done")
        return True
    fields = ACTIONS[action].params.model_fields
    step = STEP_TYPES[action].model_validate({
        "id": f"{row.step_id}-run", "action": action, "rationale": f"scheduled follow-up {followup_id}",
        "params": {k: v for k, v in row.params.items() if k in fields}, "expected": {},
        "risk_class": ACTIONS[action].risk_class,
    })
    execution = Execution(rt.store, row.run_id, rt.graph, rt.domain, link_key=rt.link_key, sleep=rt.sleep)
    ok, _, _ = await execution.run_step(step, {})
    scheduler.mark(rt.store, followup_id, "done" if ok else "failed")
    return ok
