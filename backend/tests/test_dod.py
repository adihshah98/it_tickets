"""DoD probes against the fake tenant: what passes after a clean run, and each way a check fails."""

from conftest import ACCOUNTING, DOMAIN, FINANCE_FILES, MARCUS, RDS_USERS, SOPHIE, TAYLOR, group, plan_with, step

from tech.core.models import Plan, TechnicianItem
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


async def test_user_in_groups_checks_each_step_s_user_when_none_was_created(rt, gated, tenant):
    """A learned group-add playbook (Build-Day20 live): no user.create, so each group.addMember step's own
    user must be in its group. Before, the probe failed every such run with "no user was created"."""
    tenant.add_group(FINANCE_FILES, "Finance Files", "security")
    tenant.add_group(ACCOUNTING, "Accounting", "m365")
    plan = Plan.model_validate({"intent": "site.grant_access", "summary": "add Marcus", "steps": [
        step("add", "group.addMember", {"groupId": FINANCE_FILES, "userId": MARCUS, "groupName": "Finance Files"},
             "write.reversible")]})
    run_id = gated()
    check = dod.check(ctx(rt, run_id, plan), playbook(), only=("user_in_groups",))

    assert not (await check)[0].ok  # not a member yet
    tenant.members.setdefault(FINANCE_FILES, set()).add(MARCUS)
    probe = (await dod.check(ctx(rt, run_id, plan), playbook(), only=("user_in_groups",)))[0]
    assert probe.ok and probe.detail == "in all 1"

    nothing = plan.model_copy(update={"steps": []})  # no user and no group add: still a failure, as before
    assert not (await dod.check(ctx(rt, run_id, nothing), playbook(), only=("user_in_groups",)))[0].ok


async def test_user_property_checks_each_update_s_fields_when_no_user_was_created(rt, gated, tenant):
    """A learned job-title playbook (Build-Day20 live): no user.create, so each user.update step's own user
    must show the fields that step set."""
    plan = Plan.model_validate({"intent": "user.update_title", "summary": "retitle Marcus", "steps": [
        step("title", "user.update", {"userId": MARCUS, "jobTitle": "Lead"}, "write.reversible")]})
    run_id = gated()

    assert not (await dod.check(ctx(rt, run_id, plan), playbook(), only=("user_property",)))[0].ok
    tenant.users[MARCUS]["jobTitle"] = "Lead"
    probe = (await dod.check(ctx(rt, run_id, plan), playbook(), only=("user_property",)))[0]
    assert probe.ok and probe.detail == "jobTitle=Lead"


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


# ------------------------------------------------------------------ file restore and the plain reply (Day 18)


def test_every_shipped_reply_probe_still_requires_the_one_time_link():
    for intent in ("user.onboard", "mfa.reset"):
        [item] = [i for i in load_playbook(intent, DOMAIN).definition_of_done if "message_sent" in i]
        assert item["message_sent"]["via"] == "one_time_link"


async def test_message_sent_without_via_needs_only_the_reply(rt, restore_gated):
    run_id, plan, _ = restore_gated()
    check = dod.PROBES["message_sent"]
    assert not (await check(ctx(rt, run_id, plan, SOPHIE), {})).ok                  # no reply yet
    rt.store.append_event(run_id, "message_sent", {"kind": "reply", "to": SOPHIE, "subject": "s", "text": "t"})
    assert (await check(ctx(rt, run_id, plan, SOPHIE), {})).ok
    linked = await check(ctx(rt, run_id, plan, SOPHIE), {"via": "one_time_link"})
    assert not linked.ok and "no one-time link" in linked.detail


async def test_version_restored_compares_the_current_bytes_with_the_chosen_version(rt, restore_gated, tenant):
    run_id, plan, item = restore_gated()
    probe = dod.PROBES["version_restored"]
    before = await probe(ctx(rt, run_id, plan, SOPHIE), {})
    assert not before.ok and "current content isn't version 2.0's" in before.detail
    await rt.graph.post(f"/drives/{tenant.files[item]['drive']}/items/{item}/versions/2.0/restoreVersion")
    after = await probe(ctx(rt, run_id, plan, SOPHIE), {})
    assert after.ok and after.detail == "Q3 Parking Rates.xlsx is version 2.0 again (now 4.0)"


async def test_version_restored_fails_without_a_restore_step(rt, executed):
    run_id, plan = await executed()
    assert (await dod.PROBES["version_restored"](ctx(rt, run_id, plan), {})).detail == (
        "no file.restoreVersion step in this run")
