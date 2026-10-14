"""Run state machine (Tech.MD §3): drives a ticket from RECEIVED to DONE, ESCALATED or HELD.

    start     intake -> investigation (linter in the loop) -> gate. With nothing to approve, executes.
    approve   an approver approves (or rejects) the plan, with their answers to the confirm questions,
              then the executor runs it. Also releases a HELD run: the approver confirms the requester.
              The API calls the two halves itself: `accept` inline (errors go back to the caller), then
              `execute` as a background task.
    resume    carries on an execution a crashed process left in EXECUTING.
    close-out runs once the steps verify (CLOSING): DoD probes, reply and PSA note, time entry; a failed
              DoD escalates. A failed DoD or step also demotes any playbook the run used at A3 to A2.
    draft_playbook  "Save as playbook" on a finished no-playbook run: the drafter's YAML, not yet saved.
    technician_step "Do it myself" on an ESCALATED run: one catalogue action, grounded, gated (technician
              path) and run by the executor with actor technician:{email}. The run stays ESCALATED.
    audit_draft  "I fixed it in the admin center" on an ESCALATED run: poll the audit logs for the technician's
              changes, then draft a playbook from them (capped at A2). The run stays ESCALATED.
    technician_close  closes that run: ESCALATED -> CLOSING -> DONE, with the no-playbook DoD over the
              technician's verified steps and their note in the PSA note (or back to ESCALATED on a failed DoD).

Each call can run in a new process (the CLI): what a run needs is rebuilt from its events: the plan
from `plan`, the intents from `triage`, the approver's answers from `approval`, finished steps and
their outputs from `step_verified`. Every transition is checked against TRANSITIONS.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tech import closeout
from tech.agents import drafter
from tech.agents.closeout import MODEL as CLOSEOUT_MODEL
from tech.agents.intake import MODEL as INTAKE_MODEL
from tech.agents.intake import run_intake
from tech.agents.investigator import MODEL as INVESTIGATOR_MODEL
from tech.agents.investigator import investigate
from tech.core.config import REPO, Settings, ensure_link_key
from tech.core.models import GUID_RE, STEP_TYPES, Plan, Triage, parse_ref
from tech.core.observability import configure_logfire
from tech.core.store import RunState, Store
from tech.execution import links, mocks, scheduler
from tech.execution.catalogue import ACTIONS, FOLLOWUP_ACTIONS
from tech.execution.executor import Execution
from tech.graph.client import GraphClient, GraphError, graph_from_settings
from tech.graph.reads import Transcript, fetch
from tech.graph.writes import SecretLeak, Secrets
from tech.learning.sources import from_audit_log, from_run, proposed_intent
from tech.playbooks import ClientFacts, PlaybookLibrary, load_client_facts
from tech.safety import gate
from tech.safety.lint import file_problems

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
# The one way out of ESCALATED: a technician finished the job in the console (technician_close, Build-Day20).
TRANSITIONS[S.ESCALATED] = {S.CLOSING}

ANSWER_KEYS = ("upn_format", "start_date")

LEARNED_DIR = REPO / "backend" / "tests" / "evals" / "learned"  # Build-Day19 decision 9


class RunError(RuntimeError):
    """A request the run can't take in its current state (e.g. approving a DONE run)."""


@dataclass
class Runtime:
    """What runs need from outside: the store, the tenant, the client's facts and the playbook library.
    The library is built over the store, with the shipped playbooks synced in."""

    store: Store
    graph: GraphClient
    facts: ClientFacts
    domain: str
    link_key: str | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    closeout_model: Any = CLOSEOUT_MODEL
    drafter_model: Any = drafter.MODEL
    audit_poll_s: float = 30.0   # audit logs take 1-3 minutes to show a change (Build-Day20 spike)
    audit_wait_s: float = 600.0
    clean_sign_ins_for: str | None = None  # MFA demo only: settings.mfa_demo_clean_user
    learned_dir: Path | None = None  # where approved playbooks' source runs are recorded (None: not recorded)
    library: PlaybookLibrary = field(init=False)

    def __post_init__(self) -> None:
        self.library = PlaybookLibrary(self.store, self.domain, self.facts, learned_dir=self.learned_dir)
        self.library.sync_shipped()

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
                   link_key=ensure_link_key(settings), clean_sign_ins_for=settings.mfa_demo_clean_user,
                   learned_dir=LEARNED_DIR)


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


def can_save_playbook(store: Store, run_id: int) -> bool:
    """A finished run that changed something can become a playbook draft (Build-W4 "Save as playbook") when no
    playbook covered it, or a technician did the work in the console (an escalated or A1 run they finished:
    its steps may make a better playbook than the one that failed)."""
    plan = store.last_event(run_id, "plan")
    verified = [e.payload for e in store.events_of(run_id, "step_verified") if not e.payload.get("skipped")]
    no_playbook = plan is None or bool(plan.payload.get("no_playbook"))
    by_technician = any(v.get("actor", "").startswith("technician:") for v in verified)
    return store.get_run(run_id).state == S.DONE and bool(verified) and (no_playbook or by_technician)


async def draft_playbook(rt: Runtime, run_id: int, *, intent: str | None = None,
                         notes: list[str] | None = None) -> drafter.Draft:
    """"Save as playbook": draft one from this run (Build-Day19). `intent` names the job when the proposed
    one is wrong or `unknown`; `notes` are the technician's. Nothing is saved until PlaybookLibrary.approve."""
    if not can_save_playbook(rt.store, run_id):
        raise RunError(f"run {run_id} can't be saved as a playbook: it must be a finished run that changed "
                       "something, with no playbook or with a technician's console steps")
    source = from_run(rt.store, run_id, notes=notes or [])
    if intent:
        source = source.model_copy(update={"intent": intent})
    return await drafter.draft(rt.store, source, model=rt.drafter_model)


# ------------------------------------------------------------------ technician console steps ("Do it myself")


def person(by: str, domain: str) -> str:
    """An email for whoever acted: a bare name gets the tenant's domain."""
    by = by.strip().lower()
    return by if "@" in by else f"{by}@{domain}".lower()


def _strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return [value] if isinstance(value, str) else []


async def technician_step(rt: Runtime, run_id: int, action: str, params: dict[str, Any], *,
                          by: str) -> tuple[bool, str, str | None]:
    """Run one catalogue action for a technician on an ESCALATED run (Build-Day20). Returns (ok, step_id,
    error): a gate block or a failed verify is an answer, not an exception. Refused (RunError) before anything
    is recorded: the wrong state, an unknown action, params that don't validate, a `$ref`, or an ID that
    isn't in the run's reads and that a confirming read can't find."""
    state = rt.store.get_run(run_id).state
    if state != S.ESCALATED:
        raise RunError(f"run {run_id} is {state}; a technician works on an ESCALATED run")
    spec = ACTIONS.get(action)
    if spec is None:
        raise RunError(f"{action} is not in the write catalogue")
    if any(parse_ref(s) for s in _strings(params)):
        raise RunError("a console step stands alone: use IDs, not $ref: references to other steps")
    actor = f"technician:{person(by, rt.domain)}"
    taken = [e for e in rt.store.events_of(run_id, "gate_decision")
             if e.payload.get("actor", "").startswith("technician")]
    try:
        typed = spec.params.model_validate(params)
        step = STEP_TYPES[action].model_validate({
            "id": f"t{len(taken) + 1}", "action": action, "params": params, "expected": {},
            "rationale": f"done by {actor} in the console", "risk_class": spec.risk(typed)})
    except ValidationError as exc:
        raise RunError(f"params don't fit {action}: {exc.errors()[0]['msg']}") from exc

    transcript = Transcript.from_events(rt.store.events(run_id))
    step_params = step.params.model_dump(mode="json", by_alias=True)
    for guid in sorted({g.lower() for g in GUID_RE.findall(json.dumps(step_params))} - transcript.observed_ids):
        await _confirm(rt, run_id, transcript, guid, actor)
    if action == "file.restoreVersion" and (problems := file_problems(step_params, transcript)):
        raise RunError(" ".join(problems))

    writes = len(rt.store.events_of(run_id, "step_started"))
    decision = gate.evaluate_technician(step, facts=rt.facts, transcript=transcript, writes_so_far=writes)
    rt.store.append_event(run_id, "gate_decision", decision.model_dump(mode="json") | {"actor": actor})
    if decision.decision == "block":
        return False, step.id, decision.reason
    execution = Execution(rt.store, run_id, rt.graph, rt.domain, link_key=rt.link_key, sleep=rt.sleep, actor=actor)
    ok, _, error = await execution.run_step(step, {})
    return ok, step.id, error


async def _confirm(rt: Runtime, run_id: int, transcript: Transcript, guid: str, actor: str) -> None:
    """One read that grounds an ID the technician typed: recorded as a tool_call like the investigator's."""
    args = {"object_id": guid}
    try:
        result = await fetch(rt.graph, "directoryObjects.get", **args)
    except GraphError as exc:
        error = f"{exc.status} {exc.code}: {exc.message}"
        rt.store.append_event(run_id, "tool_call", {"tool": "directoryObjects.get", "args": args, "ok": False,
                                                    "error": error, "result": None, "t": 0.0, "actor": actor})
        raise RunError(f"{guid} isn't a user or group in the tenant ({error})") from exc
    rt.store.append_event(run_id, "tool_call", {"tool": "directoryObjects.get", "args": args, "ok": True,
                                                "error": None, "result": result, "t": 0.0, "actor": actor})
    transcript.record("directoryObjects.get", args, result)


def technician_steps(store: Store, run_id: int) -> list[dict[str, Any]]:
    """The technician's console steps that verified a change, as their step_started payloads (params as run)."""
    started = {e.payload["step_id"]: e.payload for e in store.events_of(run_id, "step_started")}
    return [started[v.payload["step_id"]] | {"verified": v.payload}
            for v in store.events_of(run_id, "step_verified")
            if v.payload.get("actor", "").startswith("technician:") and not v.payload.get("skipped")
            and v.payload["step_id"] in started]


def check_closable(store: Store, run_id: int) -> None:
    """technician_close's preconditions, separate so the API can refuse (409) before starting the close."""
    state = store.get_run(run_id).state
    if state != S.ESCALATED:
        raise RunError(f"run {run_id} is {state}; only an ESCALATED run a technician worked on can be closed")
    if not technician_steps(store, run_id):
        raise RunError(f"run {run_id} has no verified console step; do the work first (or leave it escalated)")


async def technician_close(rt: Runtime, run_id: int, *, by: str, note: str | None = None) -> RunState:
    """Close a run a technician finished in the console. The DoD is the no-playbook one over their verified
    steps (each step's verify already passed; this adds the outcome probes and the reply), never the playbook's:
    its probes expect plan fields the console steps don't have (Build-Day20 decision 6). The agent's plan stays
    the run's plan; the steps here are rebuilt from the events, in memory."""
    check_closable(rt.store, run_id)
    by = person(by, rt.domain)
    done = technician_steps(rt.store, run_id)
    agent_plan = rt.store.last_event(run_id, "plan")
    triaged = load_triage(rt.store, run_id).intents
    intent = proposed_intent(agent_plan.payload["plan"].get("intent") if agent_plan else None, triaged)
    steps = [STEP_TYPES[d["action"]].model_validate({
        "id": d["step_id"], "action": d["action"], "params": d["params"], "expected": {},
        "rationale": f"done by {d['actor']} in the console", "risk_class": ACTIONS[d["action"]].risk_class})
        for d in done]
    plan = Plan(intent=intent, summary=f"Finished by {by} in the console", steps=steps)
    transition(rt.store, run_id, S.CLOSING, by=by, note=note)
    failed = await closeout.close_out(rt.store, rt.graph, run_id, plan, facts=rt.facts, library=rt.library,
                                      secrets=Secrets(), model=rt.closeout_model, sleep=rt.sleep,
                                      definition=closeout.no_playbook_dod(plan), note=f"{by}: {note}" if note else None)
    if failed:
        transition(rt.store, run_id, S.ESCALATED, kind="dod", failed_probes=failed, findings=findings(rt.store, run_id),
                   reason=f"definition of done not met: {', '.join(failed)}")
    else:
        transition(rt.store, run_id, S.DONE)
    return rt.store.get_run(run_id).state


def check_auditable(store: Store, run_id: int) -> datetime:
    """audit_draft's precondition: an ESCALATED run. Returns when it escalated (the audit window's start)."""
    state = store.get_run(run_id).state
    if state != S.ESCALATED:
        raise RunError(f"run {run_id} is {state}; audit logs are read for an ESCALATED run a technician fixed")
    escalated = [e for e in store.events_of(run_id, "state") if e.payload["to"] == S.ESCALATED.value][-1]
    return links.as_utc(escalated.created_at)


async def audit_draft(rt: Runtime, run_id: int, *, by: str) -> drafter.Draft | None:
    """"I fixed it in the admin center" (Build-Day20): poll directoryAudits until the technician's changes show
    (then one more poll for stragglers) or audit_wait_s passes. Records `audit_observed` (status waiting, then
    found or timed_out) and, if a change can be learned from, drafts a playbook (`playbook_drafted`). Approve it
    with source audit_log: the library caps it at A2."""
    since = check_auditable(rt.store, run_id)
    by = person(by, rt.domain)
    rt.store.append_event(run_id, "audit_observed", {"status": "waiting", "since": since.isoformat(), "by": by})
    polls = int(rt.audit_wait_s // rt.audit_poll_s) + 1 if rt.audit_poll_s else 1
    findings, seen = None, False
    for attempt in range(polls):
        until = datetime.now(UTC)
        findings = await from_audit_log(rt.store, rt.graph, run_id, since=since, until=until)
        if findings.found and seen:
            break
        seen = findings.found
        if attempt < polls - 1:
            await rt.sleep(rt.audit_poll_s)
    if findings is None or not findings.found:
        rt.store.append_event(run_id, "audit_observed", {"status": "timed_out", "since": since.isoformat(),
                                                         "waited_s": rt.audit_wait_s})
        return None
    rt.store.append_event(run_id, "audit_observed", {"status": "found", "since": since.isoformat(),
                                                     "until": until.isoformat()} | findings.payload())
    if findings.source is None:  # nothing mapped: only changes we can't learn, or ones already undone
        return None
    return await drafter.draft(rt.store, findings.source, model=rt.drafter_model)


def observed_objects(store: Store, run_id: int) -> list[dict[str, Any]]:
    """The users, groups and files this run's reads saw, for the console's ID picker."""
    transcript = Transcript.from_events(store.events(run_id))
    objects = [{"id": oid} | seen for oid, seen in sorted(transcript.objects.items()) if seen.get("displayName")]
    files = [{"driveId": d, "itemId": i, "versions": v} for (d, i), v in transcript.files.items()]
    return objects + files


# ------------------------------------------------------------------ out-of-band confirmation (MFA reset)


def needs_oob(store: Store, run_id: int, plan: Plan) -> bool:
    """An MFA reset that writes a credential needs the subject confirmed out of band first (Tech.MD §6.3, §8)."""
    triage = store.last_event(run_id, "triage")
    if triage is None or "mfa.reset" not in triage.payload["triage"]["intents"]:
        return False
    return any(ACTIONS[s.action].risk(s.params) == "security.credential" for s in plan.steps if s.action in ACTIONS)


def oob_missing(store: Store, run_id: int, plan: Plan) -> bool:
    return needs_oob(store, run_id, plan) and store.last_event(run_id, "oob_confirmed") is None


def confirm_oob(rt: Runtime, run_id: int, *, by: str) -> None:
    """The demo's "Manager confirmed via SMS": record oob_confirmed on a waiting MFA reset."""
    state = rt.store.get_run(run_id).state
    if state not in (S.AWAITING_APPROVAL, S.HELD):
        raise RunError(f"run {run_id} is {state}; out-of-band confirmation is for a run waiting on approval")
    if not needs_oob(rt.store, run_id, load_plan(rt.store, run_id)):
        raise RunError(f"run {run_id} has no MFA credential step to confirm")
    if rt.store.last_event(run_id, "oob_confirmed") is not None:
        raise RunError(f"run {run_id} is already confirmed out of band")
    by = by.strip().lower()
    mocks.confirm_out_of_band(rt.store, run_id, by=by if "@" in by else f"{by}@{rt.domain}".lower())


# ------------------------------------------------------------------ approver's answers


def apply_answers(plan: Plan, answers: dict[str, str], domain: str) -> Plan:
    """Apply the approver's answers to the confirm questions. They change values, never IDs or actions,
    so the gate's decisions still hold. Raises RunError on an unknown key or a value that doesn't fit."""
    if unknown := set(answers) - set(ANSWER_KEYS):
        raise RunError(f"unknown answer {sorted(unknown)}; answers can set {', '.join(ANSWER_KEYS)}")
    data = plan.model_dump(mode="json", by_alias=True)
    steps = data["steps"]
    creates = [s for s in steps if s["action"] == "user.create"]
    if answers and len(creates) != 1:  # both answers are about a new hire; another plan can't take them
        raise RunError(f"{', '.join(sorted(answers))} needs exactly one user.create step, the plan has "
                       f"{len(creates)}")

    if upn := answers.get("upn_format"):
        upn = upn.strip() if "@" in upn else f"{upn.strip()}@{domain}"
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
    triage, check = await run_intake(rt.store, run_id, text, from_address, rt.facts, library=rt.library,
                                     model=intake_model)
    result = await investigate(rt.store, run_id, text, triage, requester_approved=check.approved, graph=rt.graph,
                               facts=rt.facts, domain=rt.domain, library=rt.library, model=investigator_model,
                               clean_sign_ins_for=rt.clean_sign_ins_for)
    if result.outcome == "planned":
        plan = result.plan
        gate.check(rt.store, run_id, plan, requester_approved=check.approved, intents=triage.intents,
                   playbooks=result.playbooks, facts=rt.facts, autonomy=rt.library.autonomy(),
                   unproven=rt.library.unproven(),
                   transcript=result.transcript, findings=result.findings)
        if not plan.steps and plan.questions_for_requester and rt.store.get_run(run_id).state in (S.PLAN_CHECKED,
                                                                                                  S.HELD):
            # Nothing can be done before the requester answers (e.g. "rehire?"). There is no waiting-on-
            # customer state yet (Week 5, with the PSA), so a technician takes the questions to them.
            transition(rt.store, run_id, S.ESCALATED, kind="questions",
                       reason="waiting on the requester: " + " ".join(plan.questions_for_requester),
                       questions=plan.questions_for_requester, findings=result.findings)
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
    by = person(by, rt.domain)
    missing = [s for s in approval_scopes(rt.store, run_id, rt.facts) if not rt.facts.is_approver(by, s)]
    if missing:
        raise RunError(f"{by} is not on the client's approver list for {', '.join(missing)}")
    if blocked := [e.payload["step_id"] for e in rt.store.events_of(run_id, "gate_decision")
                   if e.payload["decision"] == "block"]:
        raise RunError(f"the gate blocked {', '.join(blocked)}; a blocked plan can't be approved")

    plan = apply_answers(load_plan(rt.store, run_id), answers, rt.domain)  # fails before anything is recorded
    if not reject and oob_missing(rt.store, run_id, plan):  # so OOB can never land after a credential write
        raise RunError("this MFA reset needs out-of-band confirmation with the subject's manager before it can run")
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
    if oob_missing(rt.store, run_id, plan):  # accept() already refuses this; never write a credential without it
        transition(rt.store, run_id, S.ESCALATED, kind="oob", reason="MFA reset reached execution without "
                   "out-of-band confirmation; nothing was written", findings=findings(rt.store, run_id))
        return
    execution = Execution(rt.store, run_id, rt.graph, rt.domain, link_key=rt.link_key, sleep=rt.sleep)
    try:
        result = await execution.run(plan)
    except SecretLeak as exc:
        transition(rt.store, run_id, S.ESCALATED, kind="secret_guard", reason=str(exc),
                   findings=findings(rt.store, run_id))
        return
    if result.ok:
        transition(rt.store, run_id, S.CLOSING)
        failed = await closeout.close_out(rt.store, rt.graph, run_id, plan, facts=rt.facts, library=rt.library,
                                          secrets=execution.secrets, model=rt.closeout_model, sleep=rt.sleep)
        if failed:
            reason = f"definition of done not met: {', '.join(failed)}"
            transition(rt.store, run_id, S.ESCALATED, kind="dod", reason=reason, failed_probes=failed,
                       findings=findings(rt.store, run_id))
            rt.library.demote_on_failure(run_id, reason)
        else:
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
    rt.library.demote_on_failure(run_id, f"{result.failed_step}: {result.error}")


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
