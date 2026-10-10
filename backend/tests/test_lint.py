"""Plan linter, rules 1-3: pure, table-driven."""

import pytest
from conftest import (
    ACCOUNTING,
    ANNOUNCE_DL,
    IT_ADMINS,
    NEW,
    ONBOARD_PLAN,
    ORG_WIDE,
    RDS_USERS,
    plan_with,
    step,
)

from tech.core.models import Plan
from tech.safety.lint import lint

INVENTED = "00000000-1111-2222-3333-444444444444"


def problems(plan, transcript, playbooks, facts) -> list[str]:
    return lint(plan, transcript, playbooks, facts)


def test_the_live_onboarding_plan_is_clean(transcript, playbooks, facts):
    assert problems(plan_with(), transcript, playbooks, facts) == []


@pytest.mark.parametrize(("extra", "drop", "expect"), [
    # grounded
    (step("g", "group.addMember", {"groupId": INVENTED, "userId": NEW}, "write.reversible", ("create_user",)),
     (), f"{INVENTED} was not in any tool result"),
    (step("g", "group.addMember", {"groupId": ACCOUNTING, "userId": "$ref:later.output.id"}, "write.reversible"),
     (), "must refer to an earlier step"),
    (step("g", "group.addMember", {"groupId": ACCOUNTING, "userId": NEW}, "write.reversible"),
     (), "add 'create_user' to its depends_on"),
    (step("g", "group.addMember", {"groupId": ACCOUNTING, "userId": "$ref:buy_seat.output.id"}, "write.reversible",
          ("buy_seat",)), (), "must point to a user.create step, not license.purchase"),
    (step("l", "link.sendOneTime", {"to": "a@b.c", "userId": NEW, "tap": "$ref:create_user.output.tap"}, "none",
          ("create_user",)), (), "must point to a tap.create step"),
    # in scope: client facts and group kinds
    (step("g", "group.addMember", {"groupId": RDS_USERS, "userId": NEW}, "write.reversible", ("create_user",)),
     (), "RDS-Users: Marks hosted desktop"),
    (step("g", "group.addMember", {"groupId": ORG_WIDE, "userId": NEW}, "write.reversible", ("create_user",)),
     (), "Never mirror it"),
    (step("g", "group.addMember", {"groupId": ANNOUNCE_DL, "userId": NEW}, "write.reversible", ("create_user",)),
     (), "is a distribution list"),
    (step("g", "group.addMember", {"groupId": IT_ADMINS, "userId": NEW}, "write.reversible", ("create_user",)),
     (), "IT Admins is a privileged group"),
    # schema
    (step("tap", "tap.create", {"userId": NEW}, "security.credential", ("create_user",)),
     (), "Step id 'tap' is used twice"),
])
def test_a_bad_step_is_rejected_with_a_reason_the_model_can_act_on(extra, drop, expect, transcript, playbooks, facts):
    found = problems(plan_with(extra, drop=drop), transcript, playbooks, facts)
    assert len(found) == 1 and expect in found[0], found


def test_an_action_outside_the_playbook_is_out_of_scope(transcript, playbooks, facts):
    narrow = {"user.onboard": playbooks["user.onboard"].model_copy(update={"allowed_actions": [
        a for a in playbooks["user.onboard"].allowed_actions if a != "license.purchase"]})}
    [found] = problems(plan_with(), transcript, narrow, facts)
    assert found.startswith("Step buy_seat: license.purchase is not allowed for this ticket type")


def test_a_followup_that_would_run_a_disallowed_action_is_out_of_scope(transcript, playbooks, facts):
    narrow = {"user.onboard": playbooks["user.onboard"].model_copy(update={"allowed_actions": [
        a for a in playbooks["user.onboard"].allowed_actions if a != "license.assign"]})}
    [found] = problems(plan_with(), transcript, narrow, facts)
    assert "the follow-up would run license.assign" in found


def test_no_playbook_means_no_steps(transcript, facts):
    [found] = problems(plan_with(), transcript, {}, facts)
    assert "set escalate=true" in found


def test_an_escalation_passes(transcript, playbooks, facts):
    plan = Plan.model_validate(ONBOARD_PLAN | {"steps": [], "escalate": True, "escalation_reason": "rehire?"})
    assert problems(plan, transcript, {}, facts) == []


def test_ids_are_compared_ignoring_case(transcript, playbooks, facts):
    upper = step("g", "group.addMember", {"groupId": ACCOUNTING.upper(), "userId": NEW}, "write.reversible",
                 ("create_user",))
    assert problems(plan_with(upper, drop=("add_accounting",)), transcript, playbooks, facts) == []
