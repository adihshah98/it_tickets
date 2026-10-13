"""MFA reset against the fake tenant (Tech.MD §6.3): the authMethod.delete handler, the out-of-band
confirmation that must come before any credential write, the MFA DoD probes, and the demo's clean
sign-in mock. Companion to test_offboarding. The model-judgement cases (suspicious sign-ins, reports@,
vendor app, VPN) need the investigator and run in the Day 15 eval harness."""

from datetime import UTC, datetime, timedelta

import pytest
from conftest import DOMAIN, LINK_KEY, drafts_model

from tech import runner
from tech.core.models import Plan, Requester, Triage, TriageEntities
from tech.core.store import RunState
from tech.execution.dod import DodContext, check
from tech.execution.executor import Execution
from tech.graph.reads import Transcript, run_read
from tech.playbooks import load_client_facts, load_playbook, load_playbooks
from tech.safety import gate
from tech.safety.lint import lint

DENNIS = "4de26d55-a0ad-487f-837c-3d6bec07fce6"
PHONE = "16d4d5ae-4652-414a-88fb-e4949e345084"  # his Authenticator registration
DENNIS_UPN = f"dennis@{DOMAIN}"
TECH = f"tgarcia@{DOMAIN}"


@pytest.fixture
def seeded(tenant):
    tenant.add_user("dennis", "Dennis Okafor", DENNIS)
    tenant.add_auth_method(DENNIS, "microsoftAuthenticatorMethods", "iPhone", PHONE)
    return tenant


def s(sid, action, params, risk, deps=()):
    return {"id": sid, "action": action, "params": params, "depends_on": list(deps),
            "rationale": "reset Dennis's MFA", "expected": {}, "risk_class": risk}


def mfa_plan(method_type: str = "authenticator") -> Plan:
    return Plan.model_validate({
        "intent": "mfa.reset", "summary": "Reset Dennis Okafor's MFA: new phone, Authenticator lost",
        "assumptions": ["sign-ins are clean: one city, failures only since the phone was lost"],
        "steps": [
            s("remove_phone", "authMethod.delete",
              {"userId": DENNIS, "methodType": method_type, "methodId": PHONE}, "security.credential"),
            s("tap", "tap.create", {"userId": DENNIS, "lifetimeInMinutes": 60, "isUsableOnce": True},
              "security.credential", ("remove_phone",)),
            s("send_link", "link.sendOneTime", {"to": DENNIS_UPN, "userId": DENNIS, "tap": "$ref:tap.output.tap"},
              "none", ("tap",)),
            s("check_tomorrow", "followup.schedule",
              {"kind": "mfa.verifyNewMethod", "runAt": "2026-10-13T16:00:00Z", "userId": DENNIS}, "none", ("tap",)),
        ],
    })


def dennis_transcript() -> Transcript:
    t = Transcript()
    t.record("users.get", {"user_id": DENNIS}, {"id": DENNIS, "displayName": "Dennis Okafor"})
    t.record("authMethods.list", {"user_id": DENNIS},
             [{"id": PHONE, "type": "microsoftAuthenticatorAuthenticationMethod"}])
    t.record("signIns.list", {"user_id": DENNIS}, [])
    t.record("riskyUsers.get", {"user_id": DENNIS}, {"risky": False})
    return t


@pytest.fixture
def mfa_run(rt):
    """An MFA reset gated as `tech run` leaves it: Dennis asked for his own reset, so it awaits approval."""

    def make(plan: Plan | None = None, *, intents=("mfa.reset",)) -> int:
        plan = plan or mfa_plan()
        store = rt.store
        run_id = store.create_run(store.create_ticket("lost my phone, need MFA reset", DENNIS_UPN).id).id
        triage = Triage(intents=list(intents), entities=TriageEntities(),
                        requester=Requester(name="Dennis", email=DENNIS_UPN))
        store.append_event(run_id, "triage", {"triage": triage.model_dump(mode="json"),
                                              "requester_approved": True, "requester_scopes": {}})
        for state in (RunState.TRIAGED, RunState.INVESTIGATING):
            store.set_state(run_id, state)
        store.append_event(run_id, "plan", {"plan": plan.model_dump(mode="json", by_alias=True), "usage": {}})
        store.set_state(run_id, RunState.PLAN_PROPOSED)
        gate.check(store, run_id, plan, requester_approved=True, intents=list(intents),
                   playbooks=load_playbooks(list(intents), DOMAIN), facts=rt.facts, transcript=dennis_transcript())
        return run_id

    return make


async def dod(store, run_id, graph, plan, requester=DENNIS_UPN):
    ctx = DodContext(store, graph, run_id, plan, load_client_facts(DOMAIN), requester=requester,
                     sleep=no_sleep)
    return {p.probe: p for p in await check(ctx, load_playbook("mfa.reset", DOMAIN))}


# ------------------------------------------------------------------ authMethod.delete


async def no_sleep(_: float) -> None:
    pass


async def execute_plan(store, graph, plan: Plan):
    run_id = store.create_run(store.create_ticket("t", DENNIS_UPN).id).id
    return run_id, await Execution(store, run_id, graph, DOMAIN, link_key=LINK_KEY, sleep=no_sleep).run(plan)


def delete_only(method_type: str = "authenticator") -> Plan:
    plan = mfa_plan(method_type)
    return plan.model_copy(update={"steps": plan.steps[:1]})


async def test_delete_removes_the_method_and_is_idempotent(store, graph, seeded):
    run_id, result = await execute_plan(store, graph, delete_only())
    assert result.ok and seeded.auth_methods[DENNIS] == []
    [verified] = store.events_of(run_id, "step_verified")
    assert verified.payload["observed"]["methodPresent"] is False

    deletes = sum(1 for method, _, _ in seeded.calls if method == "DELETE")
    _, again = await execute_plan(store, graph, delete_only())
    assert again.ok  # already gone: `existing` returns, no second DELETE
    assert sum(1 for method, _, _ in seeded.calls if method == "DELETE") == deletes


async def test_delete_under_the_wrong_method_type_fails_verification(store, graph, seeded):
    _, result = await execute_plan(store, graph, delete_only("phone"))  # Graph 404s it under phoneMethods
    assert not result.ok and result.failed_step == "remove_phone"
    assert [m["id"] for m in seeded.auth_methods[DENNIS]] == [PHONE]  # the Authenticator is still there


# ------------------------------------------------------------------ the gate and out-of-band confirmation


def test_credential_steps_on_an_mfa_reset_are_labelled_oob_required(facts):
    result = gate.evaluate(mfa_plan(), requester_approved=True, intents=["mfa.reset"],
                           playbooks=load_playbooks(["mfa.reset"], DOMAIN), facts=facts,
                           transcript=dennis_transcript())
    by_step = {d.step_id: (d.decision, d.rule_id) for d in result.decisions}
    assert by_step["remove_phone"] == ("approve", "mfa.oob_required")
    assert by_step["tap"] == ("approve", "mfa.oob_required")
    assert by_step["send_link"][0] == "auto" and by_step["check_tomorrow"][0] == "auto"


def test_an_onboarding_tap_keeps_its_rule(transcript, playbooks, facts):
    from conftest import plan_with

    tap = next(d for d in gate.evaluate(plan_with(), requester_approved=True, intents=["user.onboard"],
                                        playbooks=playbooks, facts=facts, transcript=transcript).decisions
               if d.step_id == "tap")
    assert (tap.decision, tap.rule_id) == ("approve", "risk.security.credential")


async def test_approval_is_refused_until_out_of_band_confirmation(rt, mfa_run, seeded):
    run_id = mfa_run()
    assert rt.store.get_run(run_id).state == RunState.AWAITING_APPROVAL

    with pytest.raises(runner.RunError, match="out-of-band"):
        runner.accept(rt, run_id, by=TECH)
    assert rt.store.events_of(run_id, "approval") == []  # nothing recorded
    assert rt.store.get_run(run_id).state == RunState.AWAITING_APPROVAL
    assert [m["id"] for m in seeded.auth_methods[DENNIS]] == [PHONE]

    runner.confirm_oob(rt, run_id, by="tgarcia")
    assert rt.store.last_event(run_id, "oob_confirmed").payload["by"] == TECH.lower()  # as accept() records it
    assert runner.accept(rt, run_id, by=TECH) is not None
    assert rt.store.get_run(run_id).state == RunState.EXECUTING


async def test_a_reject_needs_no_confirmation(rt, mfa_run, seeded):
    run_id = mfa_run()
    assert runner.accept(rt, run_id, by=TECH, reject=True) is None
    assert rt.store.get_run(run_id).state == RunState.ESCALATED


async def test_confirmation_is_refused_off_an_mfa_reset_and_twice(rt, mfa_run, gated, seeded):
    with pytest.raises(runner.RunError, match="no MFA credential step"):
        runner.confirm_oob(rt, gated(), by="tgarcia")  # onboarding
    run_id = mfa_run()
    runner.confirm_oob(rt, run_id, by="tgarcia")
    with pytest.raises(runner.RunError, match="already confirmed"):
        runner.confirm_oob(rt, run_id, by="tgarcia")


async def test_execute_without_confirmation_escalates_and_writes_nothing(rt, mfa_run, seeded):
    """The backstop behind accept(): even a caller that skips it can't write a credential."""
    run_id = mfa_run()
    calls = len(seeded.calls)
    await runner.execute(rt, run_id, mfa_plan())
    run = rt.store.get_run(run_id)
    assert run.state == RunState.ESCALATED
    assert rt.store.last_event(run_id, "state").payload["kind"] == "oob"
    assert seeded.calls[calls:] == [] and [m["id"] for m in seeded.auth_methods[DENNIS]] == [PHONE]


async def test_the_api_button_confirms_then_approval_runs(rt, mfa_run, seeded):
    import httpx

    from tech import api

    app = api.create_app(rt, web_origin="http://ui.test")
    run_id = mfa_run()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test") as client:
        refused = await client.post(f"/runs/{run_id}/approval", json={"by": "tgarcia"})
        assert refused.status_code == 409 and "out-of-band" in refused.json()["detail"]
        assert (await client.get(f"/runs/{run_id}")).json()["oob"] == {"needed": True, "confirmed": None}
        assert (await client.post(f"/runs/{run_id}/oob", json={"by": "tgarcia"})).json() == {
            "state": "AWAITING_APPROVAL"}
        assert (await client.post(f"/runs/{run_id}/oob", json={"by": "tgarcia"})).status_code == 409  # twice
        confirmed = (await client.get(f"/runs/{run_id}")).json()["oob"]["confirmed"]
        assert confirmed | {"at": None} == {"by": TECH.lower(), "contact": "manager", "via": "sms", "at": None}
        assert (await client.post(f"/runs/{run_id}/approval", json={"by": "tgarcia"})).status_code == 202
        await app.state.tasks.drain()
    assert seeded.auth_methods[DENNIS] == []


# ------------------------------------------------------------------ end to end and the DoD


async def test_a_confirmed_reset_reaches_done_with_every_probe(rt, mfa_run, seeded):
    """Eval case 11 offline: OOB, approval, delete the lost phone, TAP by one-time link, follow-up."""
    rt.closeout_model = drafts_model(reply="Hi Dennis, open {{link}} once to set up Authenticator on your new phone.",
                                     note="Removed lost Authenticator; TAP sent by one-time link.",
                                     subject="Your MFA reset")
    run_id = mfa_run()
    runner.confirm_oob(rt, run_id, by="tgarcia")
    assert await runner.approve(rt, run_id, by=TECH) == RunState.DONE

    assert seeded.auth_methods[DENNIS] == [] and len(seeded.taps[DENNIS]) == 1
    probes = await dod(rt.store, run_id, rt.graph, mfa_plan())
    assert all(p.ok for p in probes.values()), {n: p.detail for n, p in probes.items() if not p.ok}
    assert set(probes) == {"oob_confirmed", "old_methods_removed", "tap_active", "message_sent", "followup_scheduled"}


async def test_the_link_may_go_to_the_subject_when_a_manager_asked(rt, mfa_run, seeded):
    """Taylor asked for Dennis's reset: the link goes to Dennis, never to the manager."""
    run_id = mfa_run()
    runner.confirm_oob(rt, run_id, by="tgarcia")
    plan = runner.accept(rt, run_id, by=TECH)
    assert (await Execution(rt.store, run_id, rt.graph, DOMAIN, link_key=LINK_KEY, sleep=rt.sleep).run(plan)).ok
    probes = await dod(rt.store, run_id, rt.graph, plan, requester=TECH)
    assert "no one-time link" not in probes["message_sent"].detail


async def test_oob_probe_fails_when_confirmation_came_after_a_credential_write(rt, mfa_run, seeded):
    run_id = mfa_run()
    rt.store.append_event(run_id, "step_started", {"step_id": "tap", "action": "tap.create"})
    runner.confirm_oob(rt, run_id, by="tgarcia")
    probes = await dod(rt.store, run_id, rt.graph, mfa_plan())
    assert not probes["oob_confirmed"].ok and "tap" in probes["oob_confirmed"].detail


async def test_old_methods_probe_waits_out_a_stale_read(store, graph, seeded):
    # live run 2: the delete verified, then the probe's read still listed the method
    run_id, result = await execute_plan(store, graph, delete_only())
    assert result.ok
    seeded.stale_method_reads = 2
    probes = await dod(store, run_id, graph, delete_only())
    assert probes["old_methods_removed"].ok and seeded.stale_method_reads == 0


async def test_old_methods_probe_fails_while_the_method_is_registered(rt, mfa_run, seeded):
    probes = await dod(rt.store, mfa_run(), rt.graph, mfa_plan())
    assert not probes["old_methods_removed"].ok and PHONE in probes["old_methods_removed"].detail


def test_a_complete_mfa_plan_passes_the_linter(facts):
    playbooks = {"mfa.reset": load_playbook("mfa.reset", DOMAIN)}
    assert lint(mfa_plan(), dennis_transcript(), playbooks, facts) == []


# ------------------------------------------------------------------ the demo's clean sign-in history


async def test_clean_sign_ins_replace_only_that_users_history(graph, seeded):
    created = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    seeded.users[DENNIS]["createdDateTime"] = created
    t = Transcript()
    rows = await run_read(graph, t, "signIns.list", clean_sign_ins_for=DENNIS.upper(), user_id=DENNIS)

    assert {(r["city"], r["ip"]) for r in rows} == {("Sacramento", "98.42.17.203")}  # one place throughout
    assert [r["errorCode"] for r in rows[:2]] == [500121, 500121]  # today: MFA fails since the phone was lost
    assert all(r["errorCode"] == 0 for r in rows[2:])
    assert all(r["time"] > created for r in rows)  # never before the account existed
    assert t.called("signIns.list")  # recorded like any read
    assert not any("auditLogs" in path for _, path, _ in seeded.calls)  # Graph's sign-in log wasn't read


async def test_other_users_and_a_disabled_setting_read_real_sign_ins(graph, seeded):
    seeded.sign_ins.append({
        "createdDateTime": "2026-10-09T21:13:00Z", "appDisplayName": "My Sign-Ins", "ipAddress": "2600:387::7",
        "status": {"errorCode": 50126, "failureReason": "Invalid password"},
        "location": {"countryOrRegion": "US", "city": "Fremont"}, "clientAppUsed": "Browser"})
    other = "11111111-2222-3333-4444-555555555555"
    for user_id, setting in ((other, DENNIS), (DENNIS, None)):
        [row] = await run_read(graph, Transcript(), "signIns.list", clean_sign_ins_for=setting, user_id=user_id)
        assert row["city"] == "Fremont" and row["errorCode"] == 50126
