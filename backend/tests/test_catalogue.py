import pytest

from tech.core.models import STEP_TYPES, FollowupParams, UserUpdateParams
from tech.execution.catalogue import ACTIONS, FOLLOWUP_ACTIONS
from tech.playbooks import intents, load_playbook

USER = "8a590663-09f2-4ee8-ab1a-6195ca3943f9"


def test_catalogue_and_plan_schema_cover_the_same_actions():
    assert set(ACTIONS) == set(STEP_TYPES)
    for name, spec in ACTIONS.items():
        assert STEP_TYPES[name].model_fields["params"].annotation is spec.params


@pytest.mark.parametrize("intent", intents())
def test_every_allowed_action_is_in_the_catalogue(intent):
    playbook = load_playbook(intent, "example.com")
    assert set(playbook.allowed_actions) <= set(ACTIONS)


def test_every_onboarding_action_is_covered():
    assert set(load_playbook("user.onboard", "example.com").allowed_actions) == set(ACTIONS)


def test_every_action_has_a_verify_read_and_compensation():
    for spec in ACTIONS.values():
        assert spec.verify and spec.compensate and spec.implementation


@pytest.mark.parametrize(("action", "risk"), [
    ("user.create", "write.reversible"),
    ("license.purchase", "spend"),
    ("license.assign", "write.reversible"),
    ("group.addMember", "write.reversible"),
    ("tap.create", "security.credential"),
])
def test_risk_classes_match_tech_md(action, risk):
    assert ACTIONS[action].risk_class == risk


def test_blocking_sign_in_is_access_revoke():
    spec = ACTIONS["user.update"]
    assert spec.risk(UserUpdateParams(userId=USER, accountEnabled=False)) == "access.revoke"
    assert spec.risk(UserUpdateParams(userId=USER, usageLocation="US")) == "write.reversible"


def followup(kind: str, **kw) -> FollowupParams:
    return FollowupParams(kind=kind, runAt="2026-10-12T10:00:00Z", userId=USER, **kw)


def test_followup_takes_the_risk_of_what_it_will_do():
    spec = ACTIONS["followup.schedule"]
    assert spec.risk(followup("license.assign", skuId=USER)) == ACTIONS["license.assign"].risk_class
    assert spec.risk(followup("license.review")) == "none"
    assert spec.risk(followup("mfa.verifyNewMethod")) == "none"


def test_every_followup_kind_maps_to_a_catalogue_action_or_a_reminder():
    kinds = set(FollowupParams.model_fields["kind"].annotation.__args__)
    assert set(FOLLOWUP_ACTIONS) == kinds
    assert {a for a in FOLLOWUP_ACTIONS.values() if a} <= set(ACTIONS)
