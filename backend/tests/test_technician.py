"""Technician console steps ("Do it myself", Build-Day20 part 1): grounding, the gate's technician path,
the executor's actor, and the routes. The run is an escalated one whose reads saw Marcus and his groups."""

import uuid

import httpx
import pytest
from conftest import (
    ACCOUNTING,
    DOMAIN,
    FINANCE_FILES,
    IT_ADMINS,
    MARCUS,
    RDS_USERS,
    TAYLOR,
    drafts_model,
    group,
    marcus_transcript,
    step,
)
from eval_harness import bypasses
from pydantic_ai.models.function import FunctionModel

from tech import api, runner
from tech.cli import parse_params
from tech.core.models import STEP_TYPES, Requester, Triage, TriageEntities
from tech.core.store import RunState
from tech.graph.reads import Transcript
from tech.learning.sources import from_run
from tech.playbooks import load_client_facts
from tech.safety import gate

ME = f"technician:aditya@{DOMAIN}".lower()


@pytest.fixture
def escalated(rt, tenant):
    """A no-playbook run the investigator escalated, after reading Marcus and his groups (as tool_call events)."""
    tenant.add_group(ACCOUNTING, "Accounting", "m365")
    tenant.add_group(FINANCE_FILES, "Finance Files", "security")
    tenant.add_group(IT_ADMINS, "IT Admins", "security", role_assignable=True)
    store = rt.store
    run_id = store.create_run(store.create_ticket("give Marcus the finance share", TAYLOR).id).id
    triage = Triage(intents=["unknown"], entities=TriageEntities(), requester=Requester(name="Taylor", email=TAYLOR))
    store.append_event(run_id, "triage", {"triage": triage.model_dump(mode="json"), "requester_approved": True,
                                          "requester_scopes": {}})
    for tool, args, result in [
        ("users.search", {"name": "Marcus"}, [{"id": MARCUS, "displayName": "Marcus Webb"}]),
        ("users.memberOf", {"user_id": MARCUS}, [group(ACCOUNTING, "Accounting", "m365"),
                                                 group(FINANCE_FILES, "Finance Files", "security"),
                                                 group(IT_ADMINS, "IT Admins", "security", role_assignable=True)]),
    ]:
        store.append_event(run_id, "tool_call", {"tool": tool, "args": args, "ok": True, "error": None,
                                                 "result": result, "t": 1.0})
    for state in (RunState.TRIAGED, RunState.INVESTIGATING):
        store.set_state(run_id, state)
    store.set_state(run_id, RunState.ESCALATED, kind="no_playbook", reason="not sure Taylor may grant it")
    return run_id


def writes(tenant) -> list[str]:
    return [f"{m} {p}" for m, p, _ in tenant.calls if m != "GET"]


def payloads(rt, run_id, kind):
    return [e.payload for e in rt.store.events_of(run_id, kind)]


# ------------------------------------------------------------------ the step


async def test_a_technician_step_is_gated_run_and_verified_as_theirs(rt, tenant, escalated):
    ok, step_id, error = await runner.technician_step(
        rt, escalated, "group.addMember", {"groupId": FINANCE_FILES, "userId": MARCUS}, by="aditya")

    assert (ok, step_id, error) == (True, "t1", None)
    assert MARCUS in tenant.members[FINANCE_FILES]
    [decision] = payloads(rt, escalated, "gate_decision")
    assert (decision["decision"], decision["rule_id"], decision["actor"]) == ("approve", "technician.self", ME)
    assert payloads(rt, escalated, "step_started")[0]["actor"] == ME
    assert payloads(rt, escalated, "step_verified")[0]["actor"] == ME
    assert rt.store.get_run(escalated).state == RunState.ESCALATED  # closing is Part 2's
    assert bypasses(rt.store.events(escalated)) == []


async def test_a_console_step_names_its_group_from_the_reads(rt, tenant, escalated):
    """Build-Day21 decision 5: run 21's reply said "the requested group" because the step had only GUIDs."""
    supervisors = tenant.add_group(str(uuid.uuid4()), "Parking Supervisors", "security")
    await runner.technician_step(rt, escalated, "group.addMember", {"groupId": FINANCE_FILES, "userId": MARCUS},
                                 by="aditya")
    await runner.technician_step(rt, escalated, "group.addMember", {"groupId": supervisors, "userId": MARCUS},
                                 by="aditya")  # a typed ID: named by its confirming read
    await runner.technician_step(rt, escalated, "group.addMember",
                                 {"groupId": ACCOUNTING, "userId": MARCUS, "groupName": "Accounting team"}, by="aditya")

    assert [p["params"]["groupName"] for p in payloads(rt, escalated, "step_started")] == [
        "Finance Files", "Parking Supervisors", "Accounting team"]  # a name the technician gave is kept


async def test_the_next_step_gets_the_next_id(rt, tenant, escalated):
    await runner.technician_step(rt, escalated, "group.addMember", {"groupId": FINANCE_FILES, "userId": MARCUS},
                                 by="aditya")
    _, step_id, _ = await runner.technician_step(rt, escalated, "group.addMember",
                                                 {"groupId": ACCOUNTING, "userId": MARCUS}, by="aditya")
    assert step_id == "t2"


async def test_a_privileged_group_is_still_blocked_and_nothing_is_written(rt, tenant, escalated):
    ok, _, error = await runner.technician_step(
        rt, escalated, "group.addMember", {"groupId": IT_ADMINS, "userId": MARCUS}, by="aditya")

    assert not ok and "privileged group" in error
    assert payloads(rt, escalated, "gate_decision")[0]["rule_id"] == "never.privileged_group"
    assert writes(tenant) == [] and not payloads(rt, escalated, "step_started")


async def test_a_credential_action_isnt_in_the_console_yet(rt, tenant, escalated):
    ok, _, error = await runner.technician_step(rt, escalated, "tap.create", {"userId": MARCUS}, by="aditya")
    assert not ok and "admin center" in error
    assert payloads(rt, escalated, "gate_decision")[0]["rule_id"] == "technician.not_in_console"
    assert writes(tenant) == []


async def test_an_id_no_read_saw_is_confirmed_with_one_read(rt, tenant, escalated):
    supervisors = tenant.add_group(str(uuid.uuid4()), "Parking Supervisors", "security")

    ok, _, _ = await runner.technician_step(rt, escalated, "group.addMember",
                                            {"groupId": supervisors, "userId": MARCUS}, by="aditya")

    assert ok
    [confirm] = [c for c in payloads(rt, escalated, "tool_call") if c["tool"] == "directoryObjects.get"]
    assert confirm["actor"] == ME and confirm["result"] | {} == {
        "id": supervisors, "type": "group", "displayName": "Parking Supervisors", "kind": "security",
        "roleAssignable": False}


async def test_an_id_that_isnt_in_the_tenant_is_refused(rt, tenant, escalated):
    with pytest.raises(runner.RunError, match="isn't a user or group in the tenant"):
        await runner.technician_step(rt, escalated, "group.addMember",
                                     {"groupId": str(uuid.uuid4()), "userId": MARCUS}, by="aditya")
    assert not payloads(rt, escalated, "gate_decision") and writes(tenant) == []
    assert payloads(rt, escalated, "tool_call")[-1]["ok"] is False  # the failed confirming read is on record


@pytest.mark.parametrize("action, params, refusal", [
    ("group.nuke", {}, "not in the write catalogue"),
    ("group.addMember", {"groupId": FINANCE_FILES}, "params don't fit"),
    ("group.addMember", {"groupId": FINANCE_FILES, "userId": "$ref:create_user.output.id"}, "stands alone"),
])
async def test_a_step_that_cant_be_run_is_refused_before_anything_is_recorded(rt, escalated, action, params,
                                                                              refusal):
    with pytest.raises(runner.RunError, match=refusal):
        await runner.technician_step(rt, escalated, action, params, by="aditya")
    assert not payloads(rt, escalated, "gate_decision")


async def test_only_an_escalated_run_gets_the_console(rt, tenant, gated):
    run_id = gated()  # awaiting approval: the agent's plan is waiting
    with pytest.raises(runner.RunError, match="a technician works on an ESCALATED run"):
        await runner.technician_step(rt, run_id, "group.addMember", {"groupId": FINANCE_FILES, "userId": MARCUS},
                                     by="aditya")


async def test_agent_steps_carry_no_actor(rt, tenant, gated):
    run_id = gated()
    await runner.approve(rt, run_id, by=TAYLOR)
    assert not [e for e in rt.store.events(run_id) if e.type.startswith("step_") and "actor" in e.payload]


# ------------------------------------------------------------------ the gate's technician path


def technician_decision(action, params, risk, transcript=None):
    s = STEP_TYPES[action].model_validate(step("t1", action, params, risk))
    return gate.evaluate_technician(s, facts=load_client_facts(DOMAIN), transcript=transcript or marcus_transcript(),
                                    writes_so_far=0)


def test_a_revoke_is_the_technicians_call():
    d = technician_decision("user.update", {"userId": MARCUS, "accountEnabled": False}, "access.revoke")
    assert (d.decision, d.rule_id, d.risk_class) == ("approve", "technician.self", "access.revoke")


def test_client_group_rules_still_apply():
    d = technician_decision("group.addMember", {"groupId": RDS_USERS, "userId": MARCUS}, "write.reversible")
    assert (d.decision, d.rule_id) == ("block", "client_facts.group_handling")


def test_the_write_bound_still_applies(facts, transcript):
    s = STEP_TYPES["group.addMember"].model_validate(
        step("t41", "group.addMember", {"groupId": FINANCE_FILES, "userId": MARCUS}, "write.reversible"))
    d = gate.evaluate_technician(s, facts=facts, transcript=transcript, writes_so_far=gate.MAX_WRITES)
    assert (d.decision, d.rule_id) == ("block", "bounds.writes_per_run")


def test_a_transcript_rebuilds_from_events(rt, escalated):
    t = Transcript.from_events(rt.store.events(escalated))
    assert t.observed(MARCUS) and t.object(IT_ADMINS)["roleAssignable"] is True
    assert {o.get("displayName") for o in runner.observed_objects(rt.store, escalated)} >= {
        "Marcus Webb", "Finance Files", "IT Admins"}


# ------------------------------------------------------------------ the routes and the CLI


async def test_the_manual_step_route(rt, tenant, escalated, gated):
    app = api.create_app(rt, web_origin="http://ui.test")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test") as client:
        r = await client.post(f"/runs/{escalated}/manual-step", json={
            "action": "group.addMember", "params": {"groupId": FINANCE_FILES, "userId": MARCUS}, "by": "aditya"})
        assert r.status_code == 200 and r.json() == {"ok": True, "step_id": "t1", "error": None, "state": "ESCALATED"}

        r = await client.post(f"/runs/{escalated}/manual-step", json={
            "action": "group.addMember", "params": {"groupId": IT_ADMINS, "userId": MARCUS}, "by": "aditya"})
        assert r.status_code == 200 and r.json()["ok"] is False  # a block is an answer, not an error

        waiting = gated()
        r = await client.post(f"/runs/{waiting}/manual-step", json={
            "action": "group.addMember", "params": {"groupId": FINANCE_FILES, "userId": MARCUS}, "by": "aditya"})
        assert r.status_code == 409

        names = {o.get("displayName") for o in (await client.get(f"/runs/{escalated}/observed")).json()}
        assert {"Marcus Webb", "Finance Files"} <= names
        assert (await client.get("/runs/999/observed")).status_code == 404


def test_cli_params_read_json_values_and_text():
    assert parse_params(["userId=abc", "accountEnabled=false", "lifetimeInMinutes=60", 'tags=["a"]']) == {
        "userId": "abc", "accountEnabled": False, "lifetimeInMinutes": 60, "tags": ["a"]}


# ------------------------------------------------------------------ closing a run the technician finished (part 2)


async def add_marcus(rt, run_id):
    ok, _, _ = await runner.technician_step(rt, run_id, "group.addMember", {"groupId": FINANCE_FILES, "userId": MARCUS},
                                            by="aditya")
    assert ok


async def test_close_needs_a_verified_console_step(rt, tenant, escalated, gated):
    with pytest.raises(runner.RunError, match="no verified console step"):
        await runner.technician_close(rt, escalated, by="aditya")
    with pytest.raises(runner.RunError, match="only an ESCALATED run"):
        await runner.technician_close(rt, gated(), by="aditya")


async def test_closing_runs_the_dod_reply_and_note_then_done(rt, tenant, escalated):
    rt.closeout_model = model = drafts_model(reply="Hi Taylor, Marcus now has the finance share.",
                                             note="Added Marcus to Finance Files (technician).")
    await add_marcus(rt, escalated)

    assert await runner.technician_close(rt, escalated, by="aditya", note="Taylor confirmed by phone") == RunState.DONE

    states = [e.payload for e in rt.store.events_of(escalated, "state")]
    assert [s["to"] for s in states[-3:]] == ["ESCALATED", "CLOSING", "DONE"]
    assert (states[-2]["by"], states[-2]["note"]) == (f"aditya@{DOMAIN}".lower(), "Taylor confirmed by phone")
    assert [(c["probe"], c["ok"]) for c in payloads(rt, escalated, "done_check")] == [("message_sent", True)]
    assert {m["kind"] for m in payloads(rt, escalated, "message_sent")} == {"reply", "psa_note"}
    assert payloads(rt, escalated, "time_entry")
    prompt = str(model.prompts[0])
    assert "technician_note" in prompt and "Taylor confirmed by phone" in prompt
    assert bypasses(rt.store.events(escalated)) == []


async def test_a_failed_dod_sends_the_run_back_to_escalated(rt, tenant, escalated):
    def broken(messages, info):
        raise TimeoutError("close-out model timed out")

    rt.closeout_model = FunctionModel(broken)  # no reply drafted, so message_sent fails
    await add_marcus(rt, escalated)

    assert await runner.technician_close(rt, escalated, by="aditya") == RunState.ESCALATED
    last = rt.store.last_event(escalated, "state").payload
    assert (last["kind"], last["failed_probes"]) == ("dod", ["message_sent"])


async def test_a_closed_console_run_can_be_saved_as_a_technician_run(rt, tenant, escalated):
    await add_marcus(rt, escalated)
    assert not runner.can_save_playbook(rt.store, escalated)  # not until it's closed
    await runner.technician_close(rt, escalated, by="aditya", note="Taylor confirmed by phone")

    assert runner.can_save_playbook(rt.store, escalated)
    assert rt.library.successful_run(escalated)  # the technician's own action stands in for an approval
    source = from_run(rt.store, escalated)
    assert source.kind == "technician_run" and source.steps[0].actor == ME
    assert source.notes == [f"aditya@{DOMAIN}".lower() + ": Taylor confirmed by phone"]
    assert "directoryObjects.get" not in {r.tool for r in source.reads}


async def test_the_snapshot_shows_the_console_steps(rt, tenant, escalated):
    await add_marcus(rt, escalated)
    await runner.technician_step(rt, escalated, "group.addMember", {"groupId": IT_ADMINS, "userId": MARCUS},
                                 by="aditya")
    shown = api.snapshot(rt.store, escalated, "http://ui.test", DOMAIN, rt.library)["technician_steps"]
    assert [(t["step_id"], t["action"], t["status"], t["rule_id"]) for t in shown] == [
        ("t1", "group.addMember", "verified", "technician.self"),
        ("t2", "group.addMember", "blocked", "never.privileged_group")]
    assert shown[0]["actor"] == ME and shown[0]["params"]["groupId"] == FINANCE_FILES


async def test_the_close_route(rt, tenant, escalated):
    app = api.create_app(rt, web_origin="http://ui.test")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test") as client:
        assert (await client.post(f"/runs/{escalated}/close", json={"by": "aditya"})).status_code == 409
        await add_marcus(rt, escalated)
        r = await client.post(f"/runs/{escalated}/close", json={"by": "aditya", "note": "done"})
        assert r.status_code == 202
        await app.state.tasks.drain()
    assert rt.store.get_run(escalated).state == RunState.DONE
