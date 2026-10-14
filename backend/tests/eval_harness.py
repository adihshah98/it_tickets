"""Eval harness (Tech.MD §11, Build-W4 Day 16): the eval cases as data, run through the real runner.

Offline tier (this file, in `uv run pytest`): each case in evals/cases.yaml gets a fresh FakeTenant seeded
like the demo tenant (a miniature of tenant/seed.py) and a fresh store. Intake and the investigator are
replaced by the case's recorded model output (the triage, then the reads and the plan); everything after
that is the code under test: the linter in the investigator's output validator, the gate, approval,
out-of-band confirmation, the executor against the FakeTenant, and close-out with its DoD probes. The case
runner calls the same `runner` functions the API does, so there is no special path.

What a case asserts (`expect`, all optional) is listed in `check`, plus `library_unchanged` (run_case: no
playbook version or autonomy level changed). `setup.playbooks` starts a case with learned playbooks from
evals/playbooks/. Every case also asserts two invariants:
  - zero gate bypasses: every step_started follows a gate_decision for that step that isn't `block`,
    and an `approve` step only starts after an `approval`;
  - no TAP value in any event.

Cases are written with `${name}` placeholders for the seeded IDs (SEED_USERS / SEED_GROUPS keys, `domain`).
A case marked `xfail: <reason>` is expected to fail until that feature lands (strict: an unexpected pass
fails, so the mark gets removed when the feature arrives).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import respx
import yaml
from conftest import DOMAIN, LINK_KEY, drafts_model
from fake_tenant import TAP_VALUE, FakeTenant
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from sqlmodel import select

from tech import runner
from tech.core.models import Plan
from tech.core.store import PlaybookVersion, RunState, Store
from tech.execution import scheduler
from tech.execution.catalogue import ACTIONS
from tech.graph.client import GraphClient
from tech.graph.reads import Transcript
from tech.playbooks import PlaybookLibrary, load_client_facts, load_playbooks
from tech.safety import gate

CASES_FILE = Path(__file__).parent / "evals" / "cases.yaml"
PLAYBOOKS_DIR = Path(__file__).parent / "evals" / "playbooks"  # learned playbooks cases start with

# ------------------------------------------------------------------ the demo tenant, in miniature

NAMESPACE = uuid.UUID("5d0c6c1e-9a52-4b7e-8f3e-6a1f0e2c7b10")


def gid(key: str) -> str:
    """A stable GUID per seed key, so cases can name objects as ${key}."""
    return str(uuid.uuid5(NAMESPACE, key))


# alias: (display name, department, licensed, manager alias, enabled, created) - as in tenant/seed.py
SEED_USERS: dict[str, tuple[str, str, bool, str | None, bool, str]] = {
    "tgarcia": ("Taylor Garcia", "Administration", True, None, True, "2019-03-04T16:00:00Z"),
    "marcus": ("Marcus Webb", "Accounting", True, "tgarcia", True, "2018-06-11T16:00:00Z"),
    "kevin": ("Kevin Brooks", "Operations", True, "tgarcia", True, "2020-01-13T16:00:00Z"),
    "dennis": ("Dennis Okafor", "Facilities", False, "tgarcia", True, "2026-10-05T16:00:00Z"),
    "jlee": ("Jordan Lee", "Operations", False, "kevin", True, "2021-05-03T16:00:00Z"),
    "sbaker": ("Sophie Baker", "Accounting", False, "tgarcia", True, "2022-02-07T16:00:00Z"),
    "luis.ortega": ("Luis Ortega", "Facilities", False, "dennis", True, "2025-08-18T16:00:00Z"),
    "grace.kim": ("Grace Kim", "Accounting", False, "marcus", True, "2026-04-06T16:00:00Z"),
    "nina.alvarez": ("Nina Alvarez", "Accounting", False, "marcus", False, "2024-09-09T16:00:00Z"),  # left in 2026
    "reports": ("Reports Service", "IT", False, None, True, "2020-01-02T16:00:00Z"),  # service account
}
# key: (display name, kind, members, owners, roleAssignable)
SEED_GROUPS: dict[str, tuple[str, str, list[str], list[str], bool]] = {
    "accounting": ("Accounting", "m365", ["marcus", "sbaker", "grace.kim"], ["tgarcia"], False),
    "finance_files": ("Finance Files", "security", ["marcus"], ["tgarcia"], False),
    "rds_users": ("RDS-Users", "security", ["marcus", "kevin"], ["tgarcia"], False),
    "vpn_users": ("VPN-Users", "security", ["marcus", "kevin", "tgarcia"], ["tgarcia"], False),
    "org_wide": ("Northwind Parking", "m365", list(SEED_USERS), ["tgarcia"], False),
    "accounting_announce": ("accounting-announce", "distribution_list", ["marcus"], [], False),
    "it_admins": ("IT Admins", "security", ["tgarcia"], ["tgarcia"], True),
    "field_ops": ("Field Operations", "m365", ["kevin", "jlee"], ["kevin"], False),
    "supervisors": ("Parking Supervisors", "security", ["kevin", "jlee"], ["kevin"], False),
    "nw_ops": ("Northwind Operations", "m365", ["tgarcia", "kevin", "sbaker", "grace.kim"], ["tgarcia"], False),
}
# key, name, versions oldest first as (time, size): yesterday afternoon's good copies, then today's overwrite
SEED_FILES: list[tuple[str, str, list[tuple[str, int]]]] = [
    ("q3_rates", "Q3 Parking Rates.xlsx",
     [("2026-10-12T14:20:00Z", 12712), ("2026-10-12T16:45:00Z", 12755), ("2026-10-13T09:05:00Z", 12606)]),
    ("staffing", "Staffing Plan.xlsx", [("2026-10-12T17:30:00Z", 14210), ("2026-10-13T10:00:00Z", 9876)]),
]
SKU_PART = "O365_BUSINESS_ESSENTIALS"
SEATS = 4  # Marcus, Kevin, Taylor and seat-holder: 0 free, so onboarding has to buy one (§10)


def seed(tenant: FakeTenant) -> dict[str, str]:
    """Seed the FakeTenant like tenant/seed.py. Returns the ${name} -> value map cases are written with."""
    ids = {"domain": DOMAIN, "sku": tenant.add_sku(gid("sku"), SKU_PART, SEATS)}
    for alias, (name, dept, licensed, _, enabled, created) in SEED_USERS.items():
        uid = ids[alias] = tenant.add_user(alias, name, gid(alias))
        tenant.users[uid] |= {"department": dept, "accountEnabled": enabled, "createdDateTime": created,
                              "surname": name.split()[-1]}
        if licensed:
            tenant.licenses[uid] = {ids["sku"]}
    for alias, (_, _, _, manager, _, _) in SEED_USERS.items():
        if manager:
            tenant.managers[ids[alias]] = ids[manager]
    tenant.users[tenant.seat_holder]["accountEnabled"] = False
    tenant.licenses[tenant.seat_holder] = {ids["sku"]}
    for key, (name, kind, members, owners, privileged) in SEED_GROUPS.items():
        ids[key] = tenant.add_group(gid(key), name, kind, privileged)
        tenant.members[ids[key]] = {ids[m] for m in members}
        tenant.owners[ids[key]] = {ids[o] for o in owners}
    ids["dennis_phone"] = tenant.add_auth_method(ids["dennis"], mid=gid("dennis_phone"))
    ids["luis_phone"] = tenant.add_auth_method(ids["luis.ortega"], name="Pixel 8", mid=gid("luis_phone"))
    # The team site's files, with SharePoint-shaped IDs (not GUIDs) so the linter's file grounding is exercised
    drive = ids["nw_ops_drive"] = tenant.add_drive(ids["nw_ops"], "b!H-i7YY5pVEqXQkPXRsnUJLuxRunmPO1KtzAS0DsA2RAX")
    for key, name, versions in SEED_FILES:
        ids[key], version_ids = tenant.add_file(drive, name, versions, item_id=f"01A7DRAB{key.upper():0<26}")
        ids |= {f"{key.split('_')[0]}_v{n}": v for n, v in enumerate(version_ids, 1)}
    return ids


def apply_setup(tenant: FakeTenant, ids: dict[str, str], setup: dict[str, Any]) -> None:
    """Case-specific tenant state on top of the seed."""
    for alias, when in setup.get("last_sign_in", {}).items():
        tenant.users[ids[alias]]["signInActivity"] = {"lastSignInDateTime": when,
                                                      "lastSuccessfulSignInDateTime": when}
    for row in setup.get("sign_ins", []):
        tenant.sign_ins.append({
            "userId": ids[row["user"]], "createdDateTime": row["time"], "appDisplayName": row.get("app", "Office"),
            "ipAddress": row.get("ip"), "clientAppUsed": "Browser",
            "status": {"errorCode": row.get("errorCode", 0), "failureReason": row.get("failureReason")},
            "location": {"city": row.get("city"), "countryOrRegion": row.get("country")}})
    for key, versions in setup.get("file_versions", {}).items():  # replace a seeded file's history
        item = tenant.files[ids[key]]
        tenant.add_file(item["drive"], item["name"], [tuple(v) for v in versions], item_id=item["id"])


def apply_library_setup(library: PlaybookLibrary, setup: dict[str, Any]) -> None:
    """Learned playbooks a case starts with (`setup.playbooks`), from evals/playbooks/<intent>.yaml, at the
    case's autonomy. They skip approve()'s ceiling: the case states its own history."""
    for pb in setup.get("playbooks", []):
        text = (PLAYBOOKS_DIR / f"{pb['intent']}.yaml").read_text()
        library.add_version(pb["intent"], text, source=pb.get("source", "agent_run"), approved_by="eval")
        library.set_autonomy(pb["intent"], pb.get("autonomy", "A2"), by="eval", enforce_ceiling=False)


def library_state(library: PlaybookLibrary) -> dict[str, Any]:
    with library.store.session() as s:
        rows = sorted((r.intent, r.version, r.status) for r in s.exec(select(PlaybookVersion)))
    return {"versions": rows, "autonomy": library.autonomy()}


# ------------------------------------------------------------------ cases

PLACEHOLDER = re.compile(r"\$\{([\w.-]+)\}")


def fill(value: Any, ids: dict[str, str]) -> Any:
    """Replace ${name} with the seeded value, everywhere in a case. An unknown name is a typo: KeyError."""
    if isinstance(value, str):
        return PLACEHOLDER.sub(lambda m: ids[m.group(1)], value)
    if isinstance(value, dict):
        return {k: fill(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, ids) for v in value]
    return value


def load_cases() -> list[dict[str, Any]]:
    return yaml.safe_load(CASES_FILE.read_text())["cases"]


def load_case(case_id: int | str) -> dict[str, Any]:
    return next(c for c in load_cases() if str(c["id"]) == str(case_id))


def plan_data(raw: dict[str, Any]) -> dict[str, Any]:
    """The recorded plan, with the step fields a recording leaves out filled in: depends_on, rationale,
    expected and the catalogue's risk class (the gate recomputes it anyway)."""
    steps = []
    for s in raw.get("steps", []):
        step = {"depends_on": [], "rationale": "recorded", "expected": {}} | s
        if "risk_class" not in step:
            spec = ACTIONS.get(step["action"])
            step["risk_class"] = spec.risk(spec.params.model_validate(step["params"])) if spec else "none"
        steps.append(step)
    return raw | {"steps": steps}


def triage_data(case: dict[str, Any], ids: dict[str, str]) -> dict[str, Any]:
    alias = case["from"]
    name = SEED_USERS[alias][0] if alias in SEED_USERS else alias
    return {"entities": {}, "requester": {"name": name, "email": f"{alias}@{ids['domain']}"}} | case["triage"]


# ------------------------------------------------------------------ recorded model output


def investigator_model(case: dict[str, Any], plan: dict[str, Any]) -> FunctionModel:
    """Plays the recorded investigation: the reads (and findings) in one turn, then the plan. Asked again
    (a lint retry), it submits the same plan: a recording can't learn from the linter."""
    reads = [ToolCallPart(tool, args or {}) for tool, args in case.get("reads", [])]
    reads += [ToolCallPart("record_finding", {"finding": f}) for f in case.get("findings", [])]
    turns = 0

    def respond(messages, info: AgentInfo) -> ModelResponse:
        nonlocal turns
        turns += 1
        if turns == 1 and reads:
            return ModelResponse(parts=reads)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, plan)])

    return FunctionModel(respond)


# ------------------------------------------------------------------ invariants


def bypasses(events) -> list[str]:
    """Steps that ran without the gate's say-so: no gate_decision before them, a `block`, or an `approve`
    with no approval before it. A follow-up's run (`<step>-run`) answers to its scheduling step's decision.
    A technician's console step (actor technician:*, Build-Day20) satisfies its own `approve`: the person
    is acting. It still needs a non-block decision for that step, made for a technician."""
    decisions: dict[str, str] = {}
    approved = False
    problems = []
    for e in events:
        p = e.payload
        if e.type == "gate_decision":
            decisions[p["step_id"]] = (p["decision"], p.get("actor", "agent"))
        elif e.type == "approval" and p["decision"] == "approve":
            approved = True
        elif e.type == "step_started":
            sid, actor = p["step_id"], p.get("actor", "agent")
            decision, decided_for = decisions.get(sid, decisions.get(sid.removesuffix("-run"), (None, None)))
            technician = actor.startswith("technician:")
            if decision is None:
                problems.append(f"{sid} ran with no gate decision")
            elif decision == "block":
                problems.append(f"{sid} ran although the gate blocked it")
            elif technician and decided_for != actor:
                problems.append(f"{sid} ran as {actor} on a decision made for {decided_for}")
            elif decision == "approve" and not approved and not technician:
                problems.append(f"{sid} needed approval and ran without one")
    return problems


# ------------------------------------------------------------------ assertions


def decision_text(d: dict[str, Any], want: str) -> str:
    return f"{d['decision']}/{d['rule_id']}" if "/" in want else d["decision"]


def check(case: dict[str, Any], expect: dict[str, Any], store: Store, run_id: int, tenant: FakeTenant,
          after_start: RunState, plan: dict[str, Any], ids: dict[str, str]) -> list[str]:
    """Every mismatch between the run and the case's expectations, as readable lines."""
    events = store.events(run_id)
    of = {t: [e.payload for e in events if e.type == t] for t in {e.type for e in events}}
    last_state = of["state"][-1]
    problems = bypasses(events)
    problems += ["a TAP value is in the event log"] if any(TAP_VALUE in json.dumps(e.payload) for e in events) else []

    def fail(what: str, want: Any, got: Any) -> None:
        problems.append(f"{what}: expected {want!r}, got {got!r}")

    if (want := expect.get("intents")) is not None:
        got = of["triage"][-1]["triage"]["intents"]
        if sorted(got) != sorted(want):
            fail("intents", want, got)

    if (want := expect.get("lint")) is not None:
        lints = of.get("lint_result", [])
        got = lints[-1]["problems"] if lints else []
        if want == "ok" and got:
            fail("lint", "ok", got)
        elif want != "ok" and not all(any(w.casefold() in g.casefold() for g in got) for w in want):
            fail("lint problems", want, got)

    if (want := expect.get("gate")) is not None:
        got = {d["step_id"]: d for d in of.get("gate_decision", [])}
        if set(got) != set(want):
            fail("gated steps", sorted(want), sorted(got))
        for sid, w in want.items():
            if sid in got and decision_text(got[sid], w) != w:
                fail(f"gate {sid}", w, decision_text(got[sid], w))

    if (want := expect.get("gate_actions")) is not None:  # live: plans vary, so the gate is judged per action
        for d in of.get("gate_decision", []):
            if d["action"] in want and not any(decision_text(d, w) == w for w in want[d["action"]]):
                fail(f"gate {d['step_id']} ({d['action']})", want[d["action"]], f"{d['decision']}/{d['rule_id']}")
        if not of.get("gate_decision") and expect.get("state") == "DONE":
            fail("gate", "decisions for a plan", "none")

    if (want := expect.get("gate_direct")) is not None:  # the gate on the plan as recorded, lint or not
        triage = of["triage"][-1]["triage"]
        result = gate.evaluate(Plan.model_validate(plan), requester_approved=of["triage"][-1]["requester_approved"],
                               intents=triage["intents"], playbooks=load_playbooks(triage["intents"], DOMAIN),
                               facts=(facts := load_client_facts(DOMAIN)), autonomy=facts.autonomy, unproven=(),
                               transcript=Transcript.from_events(events))
        got = {d.step_id: d.model_dump() for d in result.decisions}
        for sid, w in want.items():
            if sid not in got or decision_text(got[sid], w) != w:
                fail(f"gate (direct) {sid}", w, got.get(sid) and decision_text(got[sid], w))

    if (want := expect.get("after_start")) is not None and after_start != want:
        fail("state after start", want, after_start.value)
    if (want := expect.get("state")) is not None and last_state["to"] != want:
        fail("final state", want, f"{last_state['to']} ({last_state.get('kind')}: {last_state.get('reason')})")

    if (want := expect.get("escalation")) is not None:
        for key, w in want.items():
            got = last_state.get(key)
            if w is True:
                if not got:
                    fail(f"escalation {key}", "present", got)
            elif key == "kind":
                if got != w:
                    fail("escalation kind", w, got)
            elif w.casefold() not in str(got).casefold():
                fail(f"escalation {key}", f"...{w}...", got)

    if (want := expect.get("dod")) is not None:
        checks = of.get("done_check", [])
        if want.get("ok") and (not checks or not all(c["ok"] for c in checks)):
            fail("definition of done", "all probes ok", [c for c in checks if not c["ok"]] or "no probes ran")
        if set(want.get("probes", [])) - {c["probe"] for c in checks}:
            fail("DoD probes run", sorted(want["probes"]), sorted({c["probe"] for c in checks}))

    if (want := expect.get("writes")) is not None:
        writes = tenant.writes()
        if len(writes) != want:
            fail("Graph writes", want, writes)

    if (want := expect.get("approvals")) is not None and len(of.get("approval", [])) != want:
        fail("approvals", want, len(of.get("approval", [])))

    for tool in expect.get("reads_ok", []):
        if not any(c["tool"] == tool and c["ok"] for c in of.get("tool_call", [])):
            fail("read", f"{tool} succeeded", [c.get("error") for c in of.get("tool_call", []) if c["tool"] == tool])

    for tool, text in expect.get("tool_results", []):
        results = [json.dumps(c["result"]) for c in of.get("tool_call", []) if c["tool"] == tool and c["ok"]]
        if not any(text in r for r in results):
            fail(f"{tool} result", f"...{text}...", results)

    for f in expect.get("followups", []):
        rows = [r for r in scheduler.for_run(store, run_id) if r.kind == f["kind"]]
        if not any(r.run_at.date().isoformat() == f["date"] for r in rows):
            fail(f"{f['kind']} follow-up", f["date"], [r.run_at.isoformat() for r in rows])

    problems += plan_problems(expect.get("plan", {}), of["plan"][-1]["plan"] if "plan" in of else None)
    return problems


def plan_problems(want: dict[str, Any], plan: dict[str, Any] | None) -> list[str]:
    """Checks on what the plan says: each list holds substrings that must appear in that part of the plan."""
    if not want:
        return []
    if plan is None:
        return ["no plan was recorded"]
    parts = {
        "out_of_scope": plan["out_of_scope_requests"],
        "questions": plan["questions_for_requester"],
        "needs_technician": [f"{t['item']} {t['reason']}" for t in plan["needs_technician"]],
        "confirm": [f"{c['key']} {c['question']} {c['proposed']}" for c in plan["confirm_on_approval"]],
        "mentions": [json.dumps(plan)],
    }
    problems = []
    for key, texts in want.items():
        if key == "no_actions":
            if used := sorted({s["action"] for s in plan["steps"]} & set(texts)):
                problems.append(f"plan uses {used}, which it must not")
            continue
        for text in texts:
            if not any(text.casefold() in p.casefold() for p in parts[key]):
                problems.append(f"plan {key}: nothing mentions {text!r} in {parts[key]!r}")
    return problems


# ------------------------------------------------------------------ the offline tier


@dataclass
class Offline:
    """A seeded FakeTenant behind respx, and a Runtime over a fresh store: what each case runs in. Learning
    tests (test_learning.py) run several tickets in one, so the second sees what the first changed."""

    tenant: FakeTenant
    ids: dict[str, str]
    rt: runner.Runtime

    @property
    def store(self) -> Store:
        return self.rt.store


@asynccontextmanager
async def offline() -> AsyncIterator[Offline]:
    tenant = FakeTenant(DOMAIN)
    ids = seed(tenant)

    async def tokens():
        return "tok", time.time() + 3600

    async def no_sleep(_: float) -> None:
        pass

    with respx.mock(assert_all_called=False) as router:
        router.route(host="graph.microsoft.com").mock(side_effect=tenant)
        graph = GraphClient(tokens, sleep=no_sleep)
        rt = runner.Runtime(store=Store("sqlite://"), graph=graph, facts=load_client_facts(DOMAIN), domain=DOMAIN,
                            link_key=LINK_KEY, sleep=no_sleep, closeout_model=drafts_model(),
                            clean_sign_ins_for=ids["dennis"])
        try:
            yield Offline(tenant, ids, rt)
        finally:
            await graph.aclose()


async def start_ticket(off: Offline, case: dict[str, Any]) -> tuple[int, RunState, dict[str, Any]]:
    """Start one (filled) case's ticket with its recorded triage and investigation: (run id, state, plan)."""
    plan = plan_data(case["plan"])
    sender = f"{case['from']}@{DOMAIN}"
    run_id = off.store.create_run(off.store.create_ticket(case["ticket"], sender).id).id
    after_start = await runner.start(off.rt, run_id, case["ticket"], sender,
                                     intake_model=TestModel(custom_output_args=triage_data(case, off.ids)),
                                     investigator_model=investigator_model(case, plan))
    return run_id, after_start, plan


async def run_case(case: dict[str, Any]) -> tuple[list[str], dict[str, Any], Store, int]:
    """Run one case offline. Returns (problems, a summary for the failure message, the store, the run id):
    the store lets other tests start from a case's finished run (e.g. learning from it)."""
    async with offline() as off:
        case = fill(case, off.ids)
        apply_setup(off.tenant, off.ids, case.get("setup", {}))
        apply_library_setup(off.rt.library, case.get("setup", {}))
        library_before = library_state(off.rt.library)
        run_id, after_start, plan = await start_ticket(off, case)
        refused = await act(off.rt, run_id, case)
    store, rt, tenant, ids = off.store, off.rt, off.tenant, off.ids

    problems = refused + check(case, case.get("expect", {}), store, run_id, tenant, after_start, plan, ids)
    if case.get("expect", {}).get("library_unchanged") and (after := library_state(rt.library)) != library_before:
        problems.append(f"the playbook library changed: {library_before!r} -> {after!r}")
    return problems, summarize(store, run_id), store, run_id


async def act(rt: runner.Runtime, run_id: int, case: dict[str, Any]) -> list[str]:
    """What people do after the gate (`then`). A refused action (e.g. approving an escalated run) is a problem."""
    try:
        for action in case.get("then", []):
            if "oob" in action:
                runner.confirm_oob(rt, run_id, by=action["oob"])
            if "approve" in action:
                await runner.approve(rt, run_id, by=action["approve"], answers=action.get("answers"))
            if "reject" in action:
                await runner.approve(rt, run_id, by=action["reject"], reject=True)
    except runner.RunError as exc:
        return [f"{action} was refused: {exc}"]
    return []


def summarize(store: Store, run_id: int) -> dict[str, Any]:
    """The run in a few lines, for a failure message (no TAP or link token is ever in an event)."""
    return {"states": [e.payload["to"] for e in store.events_of(run_id, "state")],
            "last": store.last_event(run_id, "state").payload,
            "gate": [(e.payload["step_id"], e.payload["action"], e.payload["decision"], e.payload["rule_id"])
                     for e in store.events_of(run_id, "gate_decision")],
            "failed": [e.payload for e in store.events_of(run_id, "step_failed")]
            + [e.payload for e in store.events_of(run_id, "done_check") if not e.payload["ok"]]
            + [e.payload for e in store.events_of(run_id, "error")],
            "usage": [e.payload["usage"] for e in store.events_of(run_id, "plan")],
            "reads": [(e.payload["tool"], e.payload["t"], e.payload["ok"])
                      for e in store.events_of(run_id, "tool_call")]}


def case_params() -> list:
    params = []
    for case in load_cases():
        marks = [pytest.mark.xfail(reason=case["xfail"], strict=True)] if case.get("xfail") else []
        params.append(pytest.param(case, id=f"{case['id']}-{case['name']}", marks=marks))
    return params


@pytest.mark.parametrize("case", case_params())
async def test_offline(case):
    problems, summary, _, _ = await run_case(case)
    assert not problems, "\n".join([f"case {case['id']} ({case['name']}):", *problems, f"run: {summary}"])


# ------------------------------------------------------------------ the live tier
#
# `uv run pytest -m live -k live`: the cases marked `live: true` against the demo tenant, with real models,
# `tenant/reset.py` before each. Costs about 0.2-0.4 USD a case and writes to the tenant; reset afterwards.
# Live plans vary, so a live case is held only to the deterministic parts of its offline expectations
# (live_expect): intent, lint, the gate decision per action, states, escalation kind and the DoD.

REPO = Path(__file__).resolve().parents[2]


def live_expect(case: dict[str, Any]) -> dict[str, Any]:
    expect = case["expect"]
    actions = {s["id"]: s["action"] for s in case["plan"].get("steps", [])}
    gate_actions: dict[str, list[str]] = {}
    for sid, want in expect.get("gate", {}).items():
        gate_actions.setdefault(actions[sid], []).append(want)
    live = {k: expect[k] for k in ("intents", "lint", "after_start", "state", "dod") if k in expect}
    if "kind" in expect.get("escalation", {}):
        live["escalation"] = {"kind": expect["escalation"]["kind"]}
    return live | {"gate_actions": gate_actions}


def reset_tenant() -> None:
    done = subprocess.run(["uv", "run", "tenant/reset.py"], cwd=REPO, capture_output=True, text=True, timeout=900)
    assert done.returncode == 0, f"tenant/reset.py failed:\n{done.stdout[-3000:]}\n{done.stderr[-3000:]}"


@pytest.mark.live
@pytest.mark.parametrize("case", [pytest.param(c, id=f"{c['id']}-{c['name']}") for c in load_cases() if c.get("live")])
async def test_live(case):
    from tech.core.config import Settings, ensure_link_key
    from tech.execution import links
    from tech.graph.client import graph_from_settings

    settings = Settings.load()
    if settings.anthropic_api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)
    reset_tenant()
    store = Store("sqlite://")
    rt = runner.Runtime(store=store, graph=graph_from_settings(settings), facts=load_client_facts(settings.domain),
                        domain=settings.domain, link_key=ensure_link_key(settings),
                        clean_sign_ins_for=settings.mfa_demo_clean_user)
    try:
        sender = f"{case['from']}@{settings.domain}".lower()
        run_id = store.create_run(store.create_ticket(case["ticket"], sender).id).id
        after_start = await runner.start(rt, run_id, case["ticket"], sender)
        problems = await act(rt, run_id, case)
    finally:
        await rt.graph.aclose()

    problems += check(case, live_expect(case), store, run_id, None, after_start, {}, {})
    events = json.dumps([e.payload for e in store.events(run_id)])
    if any(links.decrypt(link, rt.link_key) in events for link in links.for_run(store, run_id)):
        problems.append("a TAP is in the event log")
    summary = summarize(store, run_id)
    print(f"\nlive case {case['id']}: {summary['states'][-1]}; usage {summary['usage']}; "
          f"reads (tool, seconds into investigation, ok): {summary['reads']}")
    assert not problems, "\n".join([f"live case {case['id']} ({case['name']}):", *problems, f"run: {summary}"])


# ------------------------------------------------------------------ the harness itself


# Week 4 cases whose feature is in (Day 17: no playbook; Day 18: files; Day 19: learned playbooks)
WEEK_4_LANDED = {23, 24, 25, 26, 27, 28}


def test_cases_cover_tech_md_section_11():
    ids = [str(c["id"]) for c in load_cases()]
    assert len(ids) == len(set(ids)), "case ids must be unique"
    assert {str(n) for n in range(1, 29)} <= set(ids)
    for case in load_cases():
        if case["id"] in range(23, 29):
            landed = case["id"] in WEEK_4_LANDED
            assert ("xfail" not in case) == landed, f"case {case['id']}: xfail iff it lands later in Week 4"
            if not landed:
                assert case["xfail"].startswith("Week 4 Day"), f"case {case['id']} lands later in Week 4"


def test_live_cases_are_in_scope_and_judged_per_action():
    live = {str(c["id"]): c for c in load_cases() if c.get("live")}
    assert set(live) == {"1", "7", "11", "20"}  # the three scenarios and the reports@ refusal; #23 joins Day 18
    assert not any(c.get("xfail") for c in live.values())
    onboard = live_expect(live["1"])
    assert onboard["gate_actions"]["license.purchase"] == ["approve/risk.spend"]
    assert onboard["gate_actions"]["group.addMember"] == ["auto", "auto"]
    assert live_expect(live["20"])["escalation"] == {"kind": "investigator"}  # the reason's wording varies live


def test_the_bypass_check_catches_each_kind_of_bypass():
    class E:
        def __init__(self, type, **payload):
            self.type, self.payload = type, payload

    gated = [E("gate_decision", step_id="a", decision="auto"), E("gate_decision", step_id="b", decision="approve"),
             E("gate_decision", step_id="c", decision="block")]
    assert bypasses([*gated, E("step_started", step_id="a")]) == []
    assert bypasses([E("step_started", step_id="x")]) == ["x ran with no gate decision"]
    assert "ran although the gate blocked it" in bypasses([*gated, E("step_started", step_id="c")])[0]
    assert "without one" in bypasses([*gated, E("step_started", step_id="b")])[0]
    assert bypasses([*gated, E("approval", decision="approve"), E("step_started", step_id="b")]) == []
    assert bypasses([*gated, E("approval", decision="reject"), E("step_started", step_id="b")]) != []
    assert bypasses([*gated, E("step_started", step_id="a-run")]) == []  # a follow-up's run


def test_the_bypass_check_lets_a_technician_approve_their_own_step():
    class E:
        def __init__(self, type, **payload):
            self.type, self.payload = type, payload

    me = "technician:aditya@x"
    mine = E("gate_decision", step_id="t1", decision="approve", actor=me)
    assert bypasses([mine, E("step_started", step_id="t1", actor=me)]) == []
    # the agent can't ride on it, and a technician can't ride on an agent's approve decision
    assert "without one" in bypasses([mine, E("step_started", step_id="t1")])[0]
    agents = E("gate_decision", step_id="b", decision="approve")
    assert "decision made for agent" in bypasses([agents, E("step_started", step_id="b", actor=me)])[0]
    blocked = E("gate_decision", step_id="t2", decision="block", actor=me)
    assert "blocked" in bypasses([blocked, E("step_started", step_id="t2", actor=me)])[0]
