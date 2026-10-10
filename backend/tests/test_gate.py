"""Policy gate: pure and table-driven, plus the run states `check` leads to."""

import pytest
from conftest import ACCOUNTING, IT_ADMINS, NEW, RDS_USERS, plan_with, step

from tech.core.models import Plan
from tech.core.store import RunState, Store
from tech.safety import gate


def evaluate(plan, transcript, playbooks, facts, *, approved=True, intents=("user.onboard",)):
    return gate.evaluate(plan, requester_approved=approved, intents=intents, playbooks=playbooks, facts=facts,
                         transcript=transcript)


def decisions(result) -> dict[str, tuple[str, str]]:
    return {d.step_id: (d.decision, d.rule_id) for d in result.decisions}


def test_the_onboarding_plan_needs_approval_for_the_purchase_and_the_tap(transcript, playbooks, facts):
    result = evaluate(plan_with(), transcript, playbooks, facts)
    assert decisions(result) == {
        "create_user": ("auto", "risk.write.reversible"),
        "buy_seat": ("approve", "risk.spend"),
        "add_accounting": ("auto", "risk.write.reversible"),
        "add_finance_files": ("auto", "risk.write.reversible"),
        "assign_license_monday": ("auto", "risk.write.reversible"),  # as risky as the license.assign it runs
        "tap": ("approve", "risk.security.credential"),
        "send_link": ("auto", "risk.none"),
    }
    assert not result.held and not result.blocked


def test_a_role_assignment_step_is_blocked(transcript, playbooks, facts):
    plan = plan_with()
    role = plan.steps[0].model_construct(
        id="make_admin", action="directoryRole.assign", params={"roleId": "Global Administrator", "userId": NEW},
        depends_on=[], rationale="requester asked", expected={}, risk_class="security.credential")
    plan.steps.append(role)
    decision = evaluate(plan, transcript, playbooks, facts).decisions[-1]
    assert (decision.decision, decision.rule_id, decision.risk_class) == ("block", "catalogue.unknown_action", None)


@pytest.mark.parametrize(("group_id", "rule"), [
    (IT_ADMINS, "never.privileged_group"),
    (RDS_USERS, "client_facts.group_handling"),
])
def test_a_group_the_agent_must_not_change_is_blocked(group_id, rule, transcript, playbooks, facts):
    extra = step("g", "group.addMember", {"groupId": group_id, "userId": NEW}, "write.reversible", ("create_user",))
    result = evaluate(plan_with(extra), transcript, playbooks, facts)
    assert decisions(result)["g"] == ("block", rule)


def test_an_action_outside_the_playbook_is_blocked(transcript, playbooks, facts):
    narrow = {"user.onboard": playbooks["user.onboard"].model_copy(update={"allowed_actions": ["user.create"]})}
    result = evaluate(plan_with(), transcript, narrow, facts)
    assert decisions(result)["tap"] == ("block", "playbook.not_allowed")
    assert decisions(result)["create_user"] == ("auto", "risk.write.reversible")


def test_a_non_approver_requester_holds_the_run(transcript, playbooks, facts):
    result = evaluate(plan_with(), transcript, playbooks, facts, approved=False)
    assert result.held and "approver list" in result.hold_reason


def test_the_catalogue_risk_class_wins_over_the_plans(transcript, playbooks, facts):
    plan = plan_with()
    plan.steps[5].risk_class = "write.reversible"  # the model under-states the TAP
    tap = evaluate(plan, transcript, playbooks, facts).decisions[5]
    assert (tap.decision, tap.risk_class) == ("approve", "security.credential")
    assert "the plan said write.reversible" in tap.reason


def test_blocking_sign_in_is_auto_only_on_an_approved_offboarding(transcript, facts):
    from tech.playbooks import load_playbook  # noqa: PLC0415 - only here: offboarding has no playbook file yet

    block = step("block", "user.update", {"userId": ACCOUNTING, "accountEnabled": False}, "access.revoke")
    plan = Plan.model_validate({"intent": "user.offboard", "summary": "x", "steps": [block]})
    playbooks = {"user.offboard": load_playbook("user.onboard", "x").model_copy(
        update={"intent": "user.offboard", "allowed_actions": ["user.update"]})}
    offboard = evaluate(plan, transcript, playbooks, facts, intents=("user.offboard",))
    assert decisions(offboard)["block"] == ("auto", "risk.access.revoke")
    unapproved = evaluate(plan, transcript, playbooks, facts, intents=("user.offboard",), approved=False)
    assert decisions(unapproved)["block"] == ("approve", "risk.access.revoke")


def test_more_than_twenty_group_adds_need_approval(transcript, playbooks, facts):
    adds = [step(f"g{i}", "group.addMember", {"groupId": ACCOUNTING, "userId": NEW}, "write.reversible",
                 ("create_user",)) for i in range(20)]
    result = evaluate(plan_with(*adds), transcript, playbooks, facts)  # 22 write.reversible steps in all
    assert decisions(result)["g0"] == ("approve", "bounds.blast_radius")
    assert decisions(result)["tap"] == ("approve", "risk.security.credential")


def test_more_than_forty_writes_are_blocked(transcript, playbooks, facts):
    links = [step(f"l{i}", "link.sendOneTime", {"to": "a@b.c", "userId": NEW}, "none", ("create_user",))
             for i in range(40)]
    result = evaluate(plan_with(*links), transcript, playbooks, facts)
    assert {d.rule_id for d in result.decisions} == {"bounds.writes_per_run"}


# ------------------------------------------------------------------ check: events and run state


@pytest.fixture
def store():
    return Store("sqlite://")


def new_run(store) -> int:
    return store.create_run(store.create_ticket("ticket", "tgarcia@x").id).id


def check(store, run_id, plan, transcript, playbooks, facts, approved=True):
    return gate.check(store, run_id, plan, requester_approved=approved, intents=["user.onboard"],
                      playbooks=playbooks, facts=facts, transcript=transcript, findings=["Marcus is the peer"])


def last_state(store, run_id) -> dict:
    return [e.payload for e in store.events(run_id) if e.type == "state"][-1]


def test_check_records_every_decision_and_awaits_approval(store, transcript, playbooks, facts):
    run_id = new_run(store)
    check(store, run_id, plan_with(), transcript, playbooks, facts)
    events = [e for e in store.events(run_id) if e.type == "gate_decision"]
    assert [e.payload["step_id"] for e in events] == [s["id"] for s in plan_with().model_dump()["steps"]]
    assert events[1].payload["rule_id"] == "risk.spend"
    assert last_state(store, run_id) == {"from": "PLAN_CHECKED", "to": "AWAITING_APPROVAL",
                                         "steps": ["buy_seat", "tap"]}


def test_check_holds_an_unapproved_requester(store, transcript, playbooks, facts):
    run_id = new_run(store)
    check(store, run_id, plan_with(), transcript, playbooks, facts, approved=False)
    assert store.get_run(run_id).state == RunState.HELD


def test_check_escalates_a_blocked_step_with_the_findings(store, transcript, playbooks, facts):
    run_id = new_run(store)
    extra = step("g", "group.addMember", {"groupId": RDS_USERS, "userId": NEW}, "write.reversible", ("create_user",))
    check(store, run_id, plan_with(extra), transcript, playbooks, facts)
    state = last_state(store, run_id)
    assert state["to"] == "ESCALATED" and state["kind"] == "gate" and state["blocked"] == ["g"]
    assert state["findings"] == ["Marcus is the peer"]


def test_check_without_approvals_stays_checked(store, transcript, playbooks, facts):
    run_id = new_run(store)
    check(store, run_id, plan_with(drop=("buy_seat", "tap", "send_link", "assign_license_monday")),
          transcript, playbooks, facts)
    assert store.get_run(run_id).state == RunState.PLAN_CHECKED
