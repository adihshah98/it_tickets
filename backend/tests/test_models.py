import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tech.core.models import (
    FollowupParams,
    LinkSendParams,
    Plan,
    Triage,
    UserCreateParams,
    UserUpdateParams,
    parse_ref,
)

SPIKE_PLANS = Path(__file__).parent / "fixtures" / "spike_plans"
USER = "8a590663-09f2-4ee8-ab1a-6195ca3943f9"


def spike_plan(name: str) -> dict:
    return json.loads((SPIKE_PLANS / f"{name}.json").read_text())


def errors(data: dict) -> set[tuple]:
    with pytest.raises(ValidationError) as exc:
        Plan.model_validate(data)
    return {(tuple(e["loc"]), e["type"]) for e in exc.value.errors()}


def assert_round_trips(plan: Plan) -> None:
    again = Plan.model_validate_json(plan.model_dump_json())
    assert again == plan
    assert again.model_dump(mode="json") == plan.model_dump(mode="json")


# ------------------------------------------------------------------ spike plans as fixtures


@pytest.mark.parametrize("name", ["mfa", "takeover"])
def test_spike_escalations_round_trip(name):
    plan = Plan.model_validate(spike_plan(name))
    assert plan.escalate and plan.steps == []
    assert_round_trips(plan)


def test_spike_onboard_plan_rejects_a_password_and_an_inline_tap_field():
    # The playbook forbids human-set passwords, and a TAP only travels as a reference.
    assert errors(spike_plan("onboard")) == {
        (("steps", 0, "user.create", "params", "passwordProfile"), "extra_forbidden"),
        (("steps", 7, "link.sendOneTime", "params", "content"), "extra_forbidden"),
        # 720 minutes is over the tenant's TAP policy maximum (480), so Graph would reject it.
        (("steps", 6, "tap.create", "params", "lifetimeInMinutes"), "less_than_equal"),
    }


def test_spike_onboard_plan_round_trips_once_fixed():
    data = spike_plan("onboard")
    del data["steps"][0]["params"]["passwordProfile"]
    data["steps"][6]["params"]["lifetimeInMinutes"] = 480
    link = data["steps"][7]["params"]
    content = link.pop("content")
    link |= {"tap": content["tap"], "includes": content["includes"], "note": content["notes"]}

    plan = Plan.model_validate(data)
    assert_round_trips(plan)
    assert [s.action for s in plan.steps] == [
        "user.create", "license.purchase", "group.addMember", "group.addMember", "group.addMember",
        "followup.schedule", "tap.create", "link.sendOneTime",
    ]
    # snake_case names from the model are accepted and serialized the Graph way
    group = plan.steps[2].params.model_dump()
    assert group == {"groupId": "97fbfe65-a8c2-4094-bc34-e1842aa3d038", "userId": "$ref:s1.output.id",
                     "groupName": "Northwind Parking"}
    assert plan.steps[5].params.kind == "license.assign"
    assert plan.steps[7].params.tap == "$ref:s7.output.tap"


def test_spike_injection_plan_only_rejects_an_invented_link_item():
    data = spike_plan("injection")
    assert errors(data) == {(("steps", 8, "link.sendOneTime", "params", "includes", 3), "literal_error")}
    data["steps"][8]["params"]["includes"].remove("tap_reference")
    plan = Plan.model_validate(data)
    assert_round_trips(plan)
    assert plan.out_of_scope_requests  # the Global Admin ask was quoted, not planned
    followup = plan.steps[7].params
    assert followup.runAt.isoformat().startswith("2026-10-12")  # spelled "when" by the model


# ------------------------------------------------------------------ params


def test_unknown_action_is_rejected():
    data = spike_plan("onboard")
    data["steps"] = [{"id": "s1", "action": "role.assign", "params": {}, "rationale": "x", "expected": {},
                      "risk_class": "write.reversible"}]
    assert errors(data) == {(("steps", 0), "union_tag_invalid")}


@pytest.mark.parametrize("bad", ["Marcus Webb", "group-accounting", "$ref:s1.output.tap", "ref:s1.output.id"])
def test_ids_must_be_guids_or_step_refs(bad):
    with pytest.raises(ValidationError):
        UserUpdateParams(userId=bad, department="Accounting")


def test_id_or_ref_accepts_both():
    assert UserUpdateParams(userId=USER, department="A").userId == USER
    assert UserUpdateParams(user_id="$ref:s1.output.id", department="A").userId == "$ref:s1.output.id"


def test_user_update_must_change_something():
    with pytest.raises(ValidationError, match="at least one"):
        UserUpdateParams(userId=USER)


def test_user_create_nickname_must_match_upn():
    base = {"displayName": "Priya Nair", "userPrincipalName": "priya.nair@x.com", "usageLocation": "US"}
    assert UserCreateParams(**base, mailNickname="priya.nair").accountEnabled is True
    with pytest.raises(ValidationError, match="mailNickname"):
        UserCreateParams(**base, mailNickname="pnair")


def test_link_tap_must_be_a_reference_not_a_value():
    with pytest.raises(ValidationError):
        LinkSendParams(to="t@x.com", userId=USER, tap="Xy12-abcd-TAP")
    with pytest.raises(ValidationError):
        LinkSendParams(to="t@x.com", userId=USER, password="hunter2")


def test_license_followup_needs_sku():
    with pytest.raises(ValidationError, match="skuId"):
        FollowupParams(kind="license.assign", runAt="2026-10-12T10:00:00Z", userId=USER)


def test_parse_ref():
    assert parse_ref("$ref:s1.output.id") == ("s1", "id")
    assert parse_ref("$ref:create_user.output.tap") == ("create_user", "tap")
    assert parse_ref(USER) is None
    assert parse_ref(3) is None


def test_triage_matches_tech_md_example():
    triage = Triage.model_validate({
        "intents": ["user.onboard"],
        "entities": {
            "new_user": {"display_name": "Priya Nair", "department": "Accounting", "start_date": "2026-10-12"},
            "peer_user_hint": "Marcus",
            "apps": ["Office", "Teams"],
        },
        "requester": {"name": "Taylor", "email": "tgarcia@northwindparking.onmicrosoft.com"},
        "out_of_scope_asks": ["make requester a Global Admin"],
        "missing_info": [],
    })
    assert triage.entities.new_user.start_date.isoformat() == "2026-10-12"
    assert Triage.model_validate_json(triage.model_dump_json()) == triage


@pytest.mark.parametrize("slug", ["user.onboard", "file.restore_version", "mailbox.auto_reply.set", "unknown"])
def test_an_intent_is_any_dot_separated_lowercase_slug(slug):
    data = {"intents": [slug], "entities": {}, "requester": {"name": "Taylor", "email": "t@example.com"}}
    assert Triage.model_validate(data).intents == [slug]


@pytest.mark.parametrize("slug", ["restore", "File.Restore", "file..restore", "file.restore-version", "file.restore_",
                                  "user.onboard ", "unknown.x.", ""])
def test_a_malformed_intent_is_rejected(slug):
    data = {"intents": [slug], "entities": {}, "requester": {"name": "Taylor", "email": "t@example.com"}}
    with pytest.raises(ValidationError):
        Triage.model_validate(data)
