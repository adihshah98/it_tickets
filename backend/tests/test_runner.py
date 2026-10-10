"""Runner: approval (answers, approver check, hold release, reject), execution outcomes, resume, follow-ups."""

import pytest
from conftest import DOMAIN, FINANCE_FILES, LINK_KEY, SPB, marcus_transcript, plan_with
from fake_tenant import TAP_VALUE

from tech import runner
from tech.core.models import Plan, Requester, Triage, TriageEntities
from tech.core.store import RunState
from tech.execution import scheduler
from tech.runner import RunError, Runtime
from tech.safety import gate

TAYLOR = f"tgarcia@{DOMAIN}"


@pytest.fixture
def rt(store, graph, facts, sleeps):
    async def fake_sleep(seconds):
        sleeps.append(seconds)

    return Runtime(store=store, graph=graph, facts=facts, domain=DOMAIN, link_key=LINK_KEY, sleep=fake_sleep)


@pytest.fixture
def gated(rt, playbooks):
    """A run that went through triage, investigation and the real gate, as `tech run` leaves it."""

    def make(plan: Plan | None = None, *, requester: str = TAYLOR, approved: bool = True) -> int:
        plan = plan or plan_with()
        store = rt.store
        run_id = store.create_run(store.create_ticket("new hire Priya", requester).id).id
        triage = Triage(intents=["user.onboard"], entities=TriageEntities(),
                        requester=Requester(name="Taylor", email=requester))
        store.append_event(run_id, "triage", {"triage": triage.model_dump(mode="json"),
                                              "requester_approved": approved, "requester_scopes": {}})
        for state in (RunState.TRIAGED, RunState.INVESTIGATING):
            store.set_state(run_id, state)
        store.append_event(run_id, "finding", {"text": "Marcus is in Accounting", "t": 1.0})
        store.append_event(run_id, "plan", {"plan": plan.model_dump(mode="json", by_alias=True), "usage": {}})
        store.set_state(run_id, RunState.PLAN_PROPOSED)
        gate.check(store, run_id, plan, requester_approved=approved, intents=["user.onboard"], playbooks=playbooks,
                   facts=rt.facts, transcript=marcus_transcript())
        return run_id

    return make


def state(rt, run_id):
    return rt.store.get_run(run_id).state


def last_state(rt, run_id):
    return rt.store.last_event(run_id, "state").payload


async def test_approve_executes_the_plan_to_done(rt, gated, tenant):
    run_id = gated()
    assert state(rt, run_id) == RunState.AWAITING_APPROVAL

    assert await runner.approve(rt, run_id, by="tgarcia") == RunState.DONE
    states = [e.payload["to"] for e in rt.store.events_of(run_id, "state")]
    assert states[-4:] == ["AWAITING_APPROVAL", "EXECUTING", "CLOSING", "DONE"]
    assert tenant.user_by_upn(f"priya.nair@{DOMAIN}")
    assert all(TAP_VALUE not in str(e.payload) for e in rt.store.events(run_id))


async def test_the_upn_answer_changes_the_created_address(rt, gated, tenant):
    run_id = gated()

    await runner.approve(rt, run_id, by=TAYLOR, answers={"upn_format": "pnair"})

    user = tenant.user_by_upn(f"pnair@{DOMAIN}")
    assert user and user["mailNickname"] == "pnair"
    assert not tenant.user_by_upn(f"priya.nair@{DOMAIN}")
    assert rt.store.last_event(run_id, "approval").payload["answers"] == {"upn_format": "pnair"}


async def test_the_upn_answer_also_updates_verify_expectations(rt, gated, tenant):
    plan = plan_with()
    plan.steps[0].expected = {"userPrincipalName": f"priya.nair@{DOMAIN}"}
    run_id = gated(plan)

    assert await runner.approve(rt, run_id, by=TAYLOR, answers={"upn_format": f"pnair@{DOMAIN}"}) == RunState.DONE


async def test_the_start_date_answer_moves_the_follow_up(rt, gated, tenant):
    run_id = gated()

    await runner.approve(rt, run_id, by=TAYLOR, answers={"start_date": "2026-10-19"})

    followup = scheduler.for_step(rt.store, run_id, "assign_license_monday")
    assert followup.run_at.date().isoformat() == "2026-10-19" and followup.run_at.hour == 10


async def test_unknown_or_bad_answers_are_refused_before_anything_is_recorded(rt, gated, tenant):
    run_id = gated()
    for answers in ({"password": "x"}, {"start_date": "next monday"}, {"upn_format": "priya nair"}):
        with pytest.raises(RunError):
            await runner.approve(rt, run_id, by=TAYLOR, answers=answers)
    assert rt.store.last_event(run_id, "approval") is None and state(rt, run_id) == RunState.AWAITING_APPROVAL


async def test_a_non_approver_cannot_approve(rt, gated, tenant):
    run_id = gated()
    with pytest.raises(RunError, match="approver list"):
        await runner.approve(rt, run_id, by="jlee")
    assert state(rt, run_id) == RunState.AWAITING_APPROVAL


async def test_a_done_run_cannot_be_approved_again(rt, gated, tenant):
    run_id = gated()
    await runner.approve(rt, run_id, by=TAYLOR)
    with pytest.raises(RunError, match="DONE"):
        await runner.approve(rt, run_id, by=TAYLOR)


async def test_an_approver_releases_a_held_run(rt, gated, tenant):
    run_id = gated(requester=f"jlee@{DOMAIN}", approved=False)
    assert state(rt, run_id) == RunState.HELD

    assert await runner.approve(rt, run_id, by=TAYLOR) == RunState.DONE
    assert rt.store.last_event(run_id, "approval").payload["released_hold"] is True


async def test_reject_escalates_without_touching_the_tenant(rt, gated, tenant):
    run_id = gated()

    assert await runner.approve(rt, run_id, by=TAYLOR, reject=True) == RunState.ESCALATED
    assert last_state(rt, run_id)["kind"] == "rejected"
    assert not tenant.calls


async def test_a_plan_with_nothing_to_approve_runs_straight_away(rt, gated, tenant):
    run_id = gated(plan_with(drop=("buy_seat", "assign_license_monday", "tap", "send_link")))
    assert state(rt, run_id) == RunState.PLAN_CHECKED

    assert await runner.proceed(rt, run_id) == RunState.DONE


async def test_a_failed_step_escalates_with_a_rollback_offer(rt, gated, tenant):
    tenant.fail("POST", f"/groups/{FINANCE_FILES}", 403, message="Insufficient privileges")
    run_id = gated()

    assert await runner.approve(rt, run_id, by=TAYLOR) == RunState.ESCALATED
    escalation = last_state(rt, run_id)
    assert escalation["kind"] == "execution" and escalation["failed_step"] == "add_finance_files"
    assert escalation["done"] == ["create_user", "buy_seat", "add_accounting"]
    assert [r["step_id"] for r in escalation["rollback"]] == ["add_accounting", "buy_seat", "create_user"]
    assert escalation["findings"] == ["Marcus is in Accounting"]


async def test_resume_finishes_an_interrupted_execution(rt, gated, tenant):
    run_id = gated()
    rt.store.append_event(run_id, "approval", {"by": TAYLOR, "decision": "approve",
                                               "answers": {"upn_format": "pnair"}, "released_hold": False})
    rt.store.set_state(run_id, RunState.EXECUTING)  # the process died here

    assert await runner.resume(rt, run_id) == RunState.DONE
    assert tenant.user_by_upn(f"pnair@{DOMAIN}")  # the approver's answers still apply


async def test_resume_needs_an_executing_run(rt, gated, tenant):
    with pytest.raises(RunError, match="EXECUTING"):
        await runner.resume(rt, gated())


async def test_run_now_assigns_the_scheduled_license(rt, gated, tenant):
    run_id = gated()
    await runner.approve(rt, run_id, by=TAYLOR)
    followup = scheduler.for_step(rt.store, run_id, "assign_license_monday")
    priya = tenant.user_by_upn(f"priya.nair@{DOMAIN}")["id"]
    assert SPB not in tenant.licenses.get(priya, set())

    assert await runner.run_followup(rt, followup.id)

    assert SPB in tenant.licenses[priya]
    assert scheduler.get(rt.store, followup.id).status == "done"
    assert rt.store.last_event(run_id, "step_verified").payload["step_id"] == "assign_license_monday-run"
    with pytest.raises(RunError, match="already ran"):
        await runner.run_followup(rt, followup.id)


def test_transitions_refuse_skipping_states(rt, store):
    run_id = store.create_run(store.create_ticket("t", TAYLOR).id).id
    with pytest.raises(RunError):
        runner.transition(store, run_id, RunState.EXECUTING)
    runner.transition(store, run_id, RunState.ESCALATED)
    with pytest.raises(RunError):
        runner.transition(store, run_id, RunState.TRIAGED)
