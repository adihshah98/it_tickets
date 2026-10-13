"""Executor against a fake tenant: order, refs, idempotency, retries, deviations, and the TAP's path."""

import json

import pytest
from conftest import ACCOUNTING, DOMAIN, FINANCE_FILES, LINK_KEY, SPB, plan_with, step
from fake_tenant import TAP_VALUE

from tech.core.store import Store
from tech.execution import links, scheduler
from tech.execution.executor import Execution, compare, order
from tech.graph.writes import SecretLeak, Secrets


@pytest.fixture
def run_id(store: Store) -> int:
    return store.create_run(store.create_ticket("new hire", f"tgarcia@{DOMAIN}").id).id


@pytest.fixture
def execution(store, run_id, graph, sleeps):
    async def fake_sleep(seconds):
        sleeps.append(seconds)

    return Execution(store, run_id, graph, DOMAIN, link_key=LINK_KEY, sleep=fake_sleep)


def types(store, run_id, *wanted):
    return [(e.type, e.payload["step_id"]) for e in store.events(run_id) if e.type in wanted]


def all_payloads(store, run_id) -> str:
    return json.dumps([e.payload for e in store.events(run_id)])


async def test_onboarding_plan_runs_and_resolves_refs(execution, store, run_id, tenant):
    result = await execution.run(plan_with())

    assert result.ok, result.error
    priya = tenant.user_by_upn(f"priya.nair@{DOMAIN}")
    assert priya and priya["usageLocation"] == "US"
    assert priya["id"] in tenant.members[ACCOUNTING] and priya["id"] in tenant.members[FINANCE_FILES]
    assert SPB not in tenant.licenses[tenant.seat_holder]       # the mock purchase freed the seat
    assert len(tenant.taps[priya["id"]]) == 1
    assert [sid for t, sid in types(store, run_id, "step_verified")] == [
        "create_user", "buy_seat", "add_accounting", "add_finance_files", "assign_license_monday", "tap", "send_link"]
    assert not types(store, run_id, "step_failed")

    followup = scheduler.for_step(store, run_id, "assign_license_monday")
    assert followup.params["userId"] == priya["id"]             # the $ref was resolved before it was stored
    assert followup.run_at.date().isoformat() == "2026-10-12"


async def test_the_tap_reaches_only_the_encrypted_link(execution, store, run_id, tenant):
    await execution.run(plan_with())

    assert TAP_VALUE not in all_payloads(store, run_id)
    link = links.get(store, run_id, "send_link")
    assert TAP_VALUE not in link.ciphertext and links.decrypt(link, LINK_KEY) == TAP_VALUE
    assert link.to == f"tgarcia@{DOMAIN}"


async def test_new_user_gets_a_random_password_that_is_kept_nowhere(execution, store, run_id, tenant):
    await execution.run(plan_with())

    body = next(b for m, p, b in tenant.calls if (m, p) == ("POST", "/users"))
    password = body["passwordProfile"]["password"]
    assert len(password) >= 24 and body["passwordProfile"]["forceChangePasswordNextSignIn"]
    assert password not in all_payloads(store, run_id)


async def test_a_new_user_404_on_group_add_is_retried_not_a_deviation(execution, store, run_id, tenant, sleeps):
    tenant.fail("POST", f"/groups/{ACCOUNTING}", 404, 404)

    assert (await execution.run(plan_with())).ok
    assert sleeps[:2] == [4.0, 8.0]                              # the client's replication backoff
    retries = [e.payload for e in store.events_of(run_id, "step_retry")]
    assert [(r["step_id"], r["reason"], r["wait"]) for r in retries] == [
        ("add_accounting", "replication", 4.0), ("add_accounting", "replication", 8.0)]  # the UI's ↻ retried


async def test_verify_polls_while_the_directory_lags(execution, store, run_id, tenant, sleeps):
    tenant.stale_member_reads = 3  # the idempotency read and two verify reads miss the new membership

    result = await execution.run(plan_with(drop=("buy_seat", "add_finance_files", "assign_license_monday",
                                                 "tap", "send_link")))

    assert result.ok
    assert sleeps == [2.0, 4.0]
    assert not types(store, run_id, "step_failed")
    assert [(r.payload["reason"], r.payload["wait"]) for r in store.events_of(run_id, "step_retry")] == [
        ("verify", 2.0), ("verify", 4.0)]


async def test_an_existing_user_not_created_by_this_run_is_a_deviation(execution, store, run_id, tenant):
    tenant.add_user("priya.nair", "Priya Nair")

    result = await execution.run(plan_with())

    assert not result.ok and result.failed_step == "create_user"
    failed = store.last_event(run_id, "step_failed").payload
    assert failed["kind"] == "deviation" and "already exists" in failed["error"]
    assert not any(m == "POST" for m, _, _ in tenant.calls)       # nothing was written


async def test_a_verify_mismatch_against_expected_fails_the_step(execution, store, run_id, tenant):
    plan = plan_with()
    plan.steps[0].expected = {"usageLocation": "GB", "licensed": True}

    result = await execution.run(plan)

    assert result.failed_step == "create_user"
    failed = store.last_event(run_id, "step_failed").payload
    assert failed["kind"] == "verify" and "usageLocation" in failed["error"]


async def test_a_forbidden_write_stops_the_run_at_that_step(execution, store, run_id, tenant):
    tenant.fail("POST", f"/groups/{FINANCE_FILES}", 403, message="Insufficient privileges")

    result = await execution.run(plan_with())

    assert result.failed_step == "add_finance_files" and "403" in result.error
    assert [sid for _, sid in types(store, run_id, "step_verified")] == ["create_user", "buy_seat", "add_accounting"]


async def test_a_second_pass_skips_verified_steps(execution, store, run_id, tenant, graph):
    tenant.fail("POST", f"/groups/{FINANCE_FILES}", 403)
    await execution.run(plan_with())
    posts_before = sum(1 for m, p, _ in tenant.calls if (m, p) == ("POST", "/users"))

    again = Execution(store, run_id, graph, DOMAIN, link_key=LINK_KEY, sleep=execution.sleep)
    result = await again.run(plan_with())

    assert result.ok
    assert sum(1 for m, p, _ in tenant.calls if (m, p) == ("POST", "/users")) == posts_before == 1
    assert result.outputs["create_user"]["id"] == tenant.user_by_upn(f"priya.nair@{DOMAIN}")["id"]


async def test_a_purchase_already_done_is_skipped(execution, store, run_id, tenant):
    tenant.licenses[tenant.seat_holder] = set()

    assert (await execution.run(plan_with(drop=("create_user", "add_accounting", "add_finance_files",
                                                "assign_license_monday", "tap", "send_link")))).ok
    verified = store.last_event(run_id, "step_verified").payload
    assert verified["step_id"] == "buy_seat" and verified["skipped"] == "already true"


async def test_a_link_without_its_tap_in_memory_is_a_deviation(execution, store, run_id, tenant):
    # As after a crash: the TAP step was verified in an earlier process, so its value is gone.
    plan = plan_with()
    await execution.run(plan_with(drop=("send_link",)))
    fresh = Execution(store, run_id, execution.graph, DOMAIN, link_key=LINK_KEY, sleep=execution.sleep)

    result = await fresh.run(plan)

    assert result.failed_step == "send_link" and "not in memory" in result.error


def test_order_waits_for_referenced_steps():
    plan = plan_with()
    plan.steps.reverse()  # every dependency now comes later in the list
    ids = [s.id for s in order(plan)]
    assert ids.index("create_user") < ids.index("tap") < ids.index("send_link")
    assert ids.index("buy_seat") < ids.index("assign_license_monday")


def test_order_rejects_a_cycle():
    plan = plan_with(step("a", "user.update", {"userId": "$ref:b.output.id", "department": "X"}, "write.reversible",
                          ("b",)),
                     step("b", "user.update", {"userId": "$ref:a.output.id", "department": "Y"}, "write.reversible",
                          ("a",)))
    with pytest.raises(ValueError, match="cycle"):
        order(plan)


def test_compare_checks_known_keys_and_lists_the_rest():
    assert compare({"usageLocation": "us", "accountEnabled": "true", "licensed": True},
                   {"usageLocation": "US", "accountEnabled": True}) == ([], ["licensed"])


def test_compare_finds_an_expected_value_in_an_observed_list():
    # the plan expects one forward target; the rule read returns all of them (rehearsal run 12)
    to = ["tgarcia@NorthwindParking.onmicrosoft.com"]
    assert compare({"forwardTo": "tgarcia@northwindparking.onmicrosoft.com"}, {"forwardTo": to}) == ([], [])
    assert compare({"forwardTo": [f"tgarcia@{DOMAIN}".upper()]}, {"forwardTo": to}) == ([], [])
    assert compare({"forwardTo": "jlee@x.test"}, {"forwardTo": to})[0]
    assert compare({"forwardTo": []}, {"forwardTo": to})[0]


def test_the_guard_refuses_a_payload_with_a_secret():
    secrets = Secrets()
    secrets.watch("hunter2-secret")
    with pytest.raises(SecretLeak):
        secrets.guard({"note": "your code is hunter2-secret"})
