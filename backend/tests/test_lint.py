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


# ------------------------------------------------------------------ rules 4-7


def test_rule4_a_missing_dod_producer_is_flagged(transcript, playbooks, facts):
    # Drop the tap.create and link steps: nothing can meet 'tap_active' or 'message_sent'.
    found = problems(plan_with(drop=("tap", "send_link")), transcript, playbooks, facts)
    assert any("definition of done 'tap_active'" in p and "tap.create" in p for p in found)
    assert any("definition of done 'message_sent'" in p for p in found)


def test_rule4_a_pause_for_the_requester_may_be_partial(transcript, playbooks, facts):
    # "Rehire?": nothing is done until the requester answers, so no step meets the DoD yet (eval #3).
    data = {**ONBOARD_PLAN, "steps": [], "questions_for_requester": ["Is Priya a rehire?"]}
    assert problems(Plan.model_validate(data), transcript, playbooks, facts) == []


def test_rule4_a_structural_dod_item_needs_no_step(transcript, playbooks, facts):
    # needs_technician_listed is satisfied by the plan structure, not a write, so dropping no step for
    # it is fine; the good plan already passes, proving the structural item isn't demanded as a step.
    assert problems(plan_with(), transcript, playbooks, facts) == []


def test_rule5_an_uncalled_must_investigate_tool_is_flagged(playbooks, facts):
    from tech.graph.reads import Transcript
    bare = Transcript()  # investigated nothing
    found = problems(plan_with(), bare, playbooks, facts)
    assert any("investigating with users.search" in p for p in found)


def test_rule6_an_unresolved_question_is_flagged(transcript, playbooks, facts):
    import copy

    from conftest import ONBOARD_PLAN
    data = copy.deepcopy(ONBOARD_PLAN)
    data["assumptions"] = []
    data["confirm_on_approval"] = []  # nothing resolves start_date / peer / upn_format
    found = problems(Plan.model_validate(data), transcript, playbooks, facts)
    assert any("resolve 'upn_format'" in p for p in found)


def test_rule6_a_pause_for_the_requester_waives_questions(transcript, playbooks, facts):
    import copy

    from conftest import ONBOARD_PLAN
    data = copy.deepcopy(ONBOARD_PLAN)
    data["assumptions"] = []
    data["confirm_on_approval"] = []
    data["questions_for_requester"] = ["Which peer should Priya be copied from?"]
    found = problems(Plan.model_validate(data), transcript, playbooks, facts)
    assert not any("resolve" in p for p in found)


def test_rule7_a_forward_dependency_is_flagged(transcript, playbooks, facts):
    # A step depending on one declared after it.
    forward = step("early", "tap.create", {"userId": NEW}, "security.credential", ("create_user", "late"))
    late = step("late", "tap.create", {"userId": NEW}, "security.credential", ("create_user",))
    found = problems(plan_with(forward, late, drop=("tap",)), transcript, playbooks, facts)
    assert any("depends_on 'late' comes later" in p for p in found)
