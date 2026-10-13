"""DoD probes against the fake tenant: what passes after a clean run, and each way a check fails."""

from conftest import DOMAIN, FINANCE_FILES, MARCUS, RDS_USERS, TAYLOR, group, plan_with

from tech.core.models import TechnicianItem
from tech.execution import dod
from tech.playbooks import load_playbook

GRAPH_PROBES = ["user_exists", "user_property", "user_has_sku", "user_in_groups", "tap_active",
                "needs_technician_listed"]


def ctx(rt, run_id, plan, requester=TAYLOR):
    return dod.DodContext(rt.store, rt.graph, run_id, plan, rt.facts, requester, sleep=rt.sleep)


def playbook():
    return load_playbook("user.onboard", DOMAIN)


def by_name(probes):
    return {p.probe: p for p in probes}


def priya(tenant):
    return tenant.user_by_upn(f"priya.nair@{DOMAIN}")["id"]


async def test_a_clean_run_passes_every_graph_probe(rt, executed):
    run_id, plan = await executed()
    probes = await dod.check(ctx(rt, run_id, plan), playbook(), exclude=("message_sent",))
    assert [p.probe for p in probes] == GRAPH_PROBES
    assert all(p.ok for p in probes), [p for p in probes if not p.ok]
    assert "follow-up" in by_name(probes)["user_has_sku"].detail


async def test_a_missing_group_fails_user_in_groups(rt, executed, tenant):
    run_id, plan = await executed()
    tenant.members[FINANCE_FILES].discard(priya(tenant))

    probe = by_name(await dod.check(ctx(rt, run_id, plan), playbook(), only=("user_in_groups",)))["user_in_groups"]
    assert not probe.ok and FINANCE_FILES in probe.detail


async def test_user_in_groups_rereads_while_the_directory_lags(rt, executed, tenant, sleeps):
    run_id, plan = await executed()
    tenant.stale_member_reads = 2  # verify saw the memberships; the probe's first two reads don't (rehearsal run 9)
    sleeps.clear()

    probe = (await dod.check(ctx(rt, run_id, plan), playbook(), only=("user_in_groups",)))[0]
    assert probe.ok, probe.detail
    assert sleeps == [2.0, 4.0]


async def test_a_missing_group_still_fails_after_the_rereads(rt, executed, tenant, sleeps):
    run_id, plan = await executed()
    tenant.members[FINANCE_FILES].discard(priya(tenant))
    sleeps.clear()

    assert not (await dod.check(ctx(rt, run_id, plan), playbook(), only=("user_in_groups",)))[0].ok
    assert sleeps == [2.0, 4.0, 8.0]  # bounded: about 14 s, then it fails as before


async def test_no_license_and_no_follow_up_fails(rt, executed):
    run_id, plan = await executed(plan_with(drop=("assign_license_monday",)))
    probe = (await dod.check(ctx(rt, run_id, plan), playbook(), only=("user_has_sku",)))[0]
    assert not probe.ok and "no license" in probe.detail


async def test_without_a_created_user_the_user_probes_fail(rt, gated, tenant):
    plan = plan_with()
    run_id = gated(plan)
    probes = by_name(await dod.check(ctx(rt, run_id, plan), playbook(), only=("user_exists", "tap_active")))
    assert not probes["user_exists"].ok and "user.create" in probes["user_exists"].detail
    assert not probes["tap_active"].ok


async def test_a_peer_on_the_hosted_desktop_must_be_handed_to_a_technician(rt, executed):
    run_id, plan = await executed()
    rt.store.append_event(run_id, "tool_call", {"tool": "users.memberOf", "args": {"user_id": MARCUS}, "ok": True,
                                                "error": None, "result": [group(RDS_USERS, "RDS-Users", "security")],
                                                "t": 1.0})
    only = ("needs_technician_listed",)
    probe = (await dod.check(ctx(rt, run_id, plan), playbook(), only=only))[0]
    assert not probe.ok and "RDS-Users" in probe.detail

    listed = plan.model_copy(update={"needs_technician": [
        TechnicianItem(item="Hosted desktop access on NW-TS-01", reason="not in Graph")]})
    assert (await dod.check(ctx(rt, run_id, listed), playbook(), only=only))[0].ok


async def test_message_sent_needs_the_link_and_a_reply_and_ignores_case(rt, executed):
    run_id, plan = await executed()
    only = ("message_sent",)
    probe = (await dod.check(ctx(rt, run_id, plan), playbook(), only=only))[0]
    assert not probe.ok and "no reply" in probe.detail

    rt.store.append_event(run_id, "message_sent", {"kind": "reply", "to": TAYLOR, "subject": "s", "text": "t"})
    assert (await dod.check(ctx(rt, run_id, plan, requester=TAYLOR.upper()), playbook(), only=only))[0].ok


async def test_message_sent_accepts_a_link_to_the_approver_who_released_a_hold(rt, executed):
    run_id, plan = await executed()
    rt.store.append_event(run_id, "approval", {"by": TAYLOR, "decision": "approve", "answers": {},
                                               "released_hold": True})
    rt.store.append_event(run_id, "message_sent", {"kind": "reply", "to": "jlee@x", "subject": "s", "text": "t"})
    probe = await dod.check(ctx(rt, run_id, plan, requester=f"jlee@{DOMAIN}"), playbook(), only=("message_sent",))
    assert probe[0].ok, probe[0].detail


async def test_an_unknown_probe_is_a_failed_check_not_a_crash(rt, executed):
    run_id, plan = await executed()
    book = playbook().model_copy(update={"definition_of_done": [{"user_is_happy": {}}]})
    assert (await dod.check(ctx(rt, run_id, plan), book))[0] == dod.Probe("user_is_happy", False,
                                                                       "no probe for 'user_is_happy'")


async def test_a_graph_error_is_a_failed_check(rt, executed, tenant):
    run_id, plan = await executed()
    tenant.fail("GET", f"/users/{priya(tenant)}", 403)
    probe = (await dod.check(ctx(rt, run_id, plan), playbook(), only=("user_exists",)))[0]
    assert not probe.ok and "403" in probe.detail
