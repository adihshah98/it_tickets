"""Offboarding end to end against the fake tenant: the executor applies each write, then the DoD probes
re-check live state (Tech.MD §6.2). Companion to test_executor (onboarding) and test_dod."""

import pytest
from conftest import DOMAIN, LINK_KEY

from tech.core.models import Plan
from tech.execution import scheduler
from tech.execution.dod import DodContext, check
from tech.execution.executor import Execution
from tech.playbooks import load_client_facts, load_playbook

KEVIN = "11111111-2222-3333-4444-555555555555"
TAYLOR = "66666666-7777-8888-9999-000000000000"
GROUP_A = "aaaaaaaa-0000-0000-0000-000000000001"
GROUP_B = "aaaaaaaa-0000-0000-0000-000000000002"


@pytest.fixture
def run_id(store):
    return store.create_run(store.create_ticket("offboard Kevin", f"tgarcia@{DOMAIN}").id).id


@pytest.fixture
def execution(store, run_id, graph, sleeps):
    async def fake_sleep(seconds):
        sleeps.append(seconds)

    return Execution(store, run_id, graph, DOMAIN, link_key=LINK_KEY, sleep=fake_sleep)


@pytest.fixture
def seeded(tenant):
    """Kevin is a licensed leaver in two groups and owns GROUP_A; Taylor receives his handovers."""
    tenant.add_user("kevin", "Kevin Brooks", KEVIN)
    tenant.add_user("tgarcia", "Taylor Garcia", TAYLOR)
    tenant.members[GROUP_A] = {KEVIN}
    tenant.members[GROUP_B] = {KEVIN}
    tenant.owners[GROUP_A] = {KEVIN}
    return tenant


def offboard_plan() -> Plan:
    def s(sid, action, params, risk, deps=()):
        return {"id": sid, "action": action, "params": params, "depends_on": list(deps),
                "rationale": "offboard Kevin", "expected": {}, "risk_class": risk}

    return Plan.model_validate({
        "intent": "user.offboard", "summary": "Offboard Kevin Brooks",
        "assumptions": ["mailbox_disposition: auto-reply, forward to Taylor", "device_disposition: return",
                        "owned_resources_new_owner: Taylor", "effective_time: now"],
        "steps": [
            s("disable", "user.update", {"userId": KEVIN, "accountEnabled": False}, "access.revoke"),
            s("revoke", "sessions.revoke", {"userId": KEVIN}, "access.revoke", ("disable",)),
            s("reply", "mailbox.autoReply", {"userId": KEVIN, "message": "Kevin has left; contact Taylor."},
              "write.reversible", ("disable",)),
            s("forward", "mailbox.forwardRule", {"userId": KEVIN, "forwardTo": f"tgarcia@{DOMAIN}"},
              "data.access", ("disable",)),
            s("rm_a", "group.removeMember", {"groupId": GROUP_A, "userId": KEVIN}, "write.reversible", ("disable",)),
            s("rm_b", "group.removeMember", {"groupId": GROUP_B, "userId": KEVIN}, "write.reversible", ("disable",)),
            s("own", "group.addOwner", {"groupId": GROUP_A, "userId": TAYLOR}, "data.access", ("disable",)),
            s("review", "followup.schedule",
              {"kind": "license.review", "runAt": "2026-11-10T10:00:00Z", "userId": KEVIN}, "none", ("disable",)),
        ],
        "needs_technician": [{"item": "convert mailbox to shared / delegate access",
                              "reason": "needs Exchange PowerShell, outside Graph"}],
    })


async def no_sleep(_: float) -> None:
    pass


async def dod(store, run_id, graph, plan):
    ctx = DodContext(store, graph, run_id, plan, load_client_facts(DOMAIN), requester=f"tgarcia@{DOMAIN}",
                     sleep=no_sleep)
    return {p.probe: p for p in await check(ctx, load_playbook("user.offboard", DOMAIN))}


async def test_a_clean_offboard_applies_every_write(execution, store, run_id, graph, seeded):
    plan = offboard_plan()
    result = await execution.run(plan)

    assert result.ok, result.error
    assert seeded.users[KEVIN]["accountEnabled"] is False
    assert seeded.users[KEVIN]["signInSessionsValidFromDateTime"] is not None
    assert KEVIN not in seeded.members[GROUP_A] and KEVIN not in seeded.members[GROUP_B]
    assert TAYLOR in seeded.owners[GROUP_A]
    assert seeded.mailbox[KEVIN]["automaticRepliesSetting"]["status"] == "alwaysEnabled"
    assert seeded.rules[KEVIN][0]["actions"]["forwardTo"][0]["emailAddress"]["address"] == f"tgarcia@{DOMAIN}"
    assert scheduler.for_step(store, run_id, "review").kind == "license.review"


async def test_the_dod_passes_after_a_clean_offboard(execution, store, run_id, graph, seeded):
    plan = offboard_plan()
    await execution.run(plan)
    probes = await dod(store, run_id, graph, plan)

    assert all(p.ok for p in probes.values()), {n: p.detail for n, p in probes.items() if not p.ok}
    assert probes["account_disabled"].ok and probes["sessions_revoked"].ok
    assert probes["removed_from_groups"].ok and probes["ownership_transferred"].ok
    assert probes["forward_rule_active"].ok and probes["auto_reply_set"].ok
    assert probes["followup_scheduled"].ok


async def test_the_dod_fails_when_a_group_was_not_removed(execution, store, run_id, graph, seeded):
    plan = offboard_plan()
    seeded.fail("DELETE", f"/groups/{GROUP_B}/members/", 500, 500, 500, 500)  # exhaust retries on rm_b
    await execution.run(plan)
    probes = await dod(store, run_id, graph, plan)

    assert not probes["removed_from_groups"].ok and GROUP_B in probes["removed_from_groups"].detail


def test_a_complete_offboard_plan_passes_the_linter():
    """The plan is grounded and complete, so rules 1-7 accept it (rule 4 sees every DoD producer)."""
    from tech.graph.reads import Transcript
    from tech.safety.lint import lint

    t = Transcript()  # the leaver's ids were observed during investigation
    t.record("users.get", {"user_id": KEVIN}, {"id": KEVIN, "displayName": "Kevin Brooks"})
    t.record("users.memberOf", {"user_id": KEVIN}, [{"id": GROUP_A}, {"id": GROUP_B}])
    t.record("users.ownedObjects", {"user_id": KEVIN}, [{"id": GROUP_A}])
    t.record("users.licenseDetails", {"user_id": KEVIN}, [{"skuId": GROUP_B}])  # any observed id
    t.record("mailboxSettings.get", {"user_id": KEVIN}, {"mailbox": True})
    t.record("messageRules.list", {"user_id": KEVIN}, [])
    t.observed_ids |= {TAYLOR.lower()}  # the new owner, observed as a user
    playbooks = {"user.offboard": load_playbook("user.offboard", DOMAIN)}
    assert lint(offboard_plan(), t, playbooks, load_client_facts(DOMAIN)) == []
