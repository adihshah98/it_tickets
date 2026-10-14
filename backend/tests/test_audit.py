"""Audit-log learning, "I fixed it in the admin center" (Build-Day20 part 3): which directoryAudits entries
count, how they map to catalogue actions, the confirming read, the polling, and the routes. Entries are
shaped as the Day 20 spike recorded them (fake_tenant.audit_*)."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import DOMAIN, FINANCE_FILES, MARCUS, drafter_model
from fake_tenant import ADMIN, audit_add_member, audit_noise, audit_update_user
from test_technician import escalated  # noqa: F401  (the fixture: an escalated run that read Marcus's groups)

from tech import api, runner
from tech.cli import audit_playbook
from tech.learning import audit_map
from tech.learning.audit_map import Unmapped
from tech.safety.policy import UNIVERSAL_NEVER

MARCUS_UPN = f"marcus@{DOMAIN}"
FINANCE_PLAYBOOK = """\
intent: unknown
goal: The person gets the file share they need.
must_investigate:
  - users.search(person)
definition_of_done:
  - message_sent: {to: "$requester"}
allowed_actions: [group.addMember]
never:
""" + "".join(f"  - {line}\n" for line in UNIVERSAL_NEVER)


def now(minutes: float = 0) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def admin_adds_marcus(tenant, at: str | None = None) -> None:
    """The technician adds Marcus to Finance Files in the admin center: the change, and its audit entry."""
    tenant.members.setdefault(FINANCE_FILES, set()).add(MARCUS)
    tenant.audits.append(audit_add_member(at or now(), MARCUS, MARCUS_UPN, FINANCE_FILES, "Finance Files"))


# ------------------------------------------------------------------ mapping and relevance


def test_an_admin_center_group_add_maps_to_group_add_member():
    step = audit_map.map_entry(audit_add_member(now(), MARCUS, MARCUS_UPN, FINANCE_FILES, "Finance Files"))
    assert (step.action, step.params) == ("group.addMember", {"groupId": FINANCE_FILES, "userId": MARCUS})
    assert step.actor == f"technician:{ADMIN['upn']}".lower() and "Entra admin center" in step.rationale


def test_a_job_title_change_maps_to_user_update():
    step = audit_map.map_entry(audit_update_user(now(), MARCUS, MARCUS_UPN, {"JobTitle": ("Clerk", "Lead")}))
    assert (step.action, step.params) == ("user.update", {"userId": MARCUS, "jobTitle": "Lead"})


@pytest.mark.parametrize("entry, reason", [
    (audit_update_user(now(), MARCUS, MARCUS_UPN, {"MobilePhone": ("1", "2")}), "user.update can't make"),
    (audit_noise(now(), MARCUS, MARCUS_UPN)[1], "not in the catalogue"),  # Reset user password
])
def test_changes_outside_the_catalogue_are_unmapped_with_a_reason(entry, reason):
    result = audit_map.map_entry(entry)
    assert isinstance(result, Unmapped) and reason in result.reason
    assert "DeviceToken" not in str(result.payload())


def test_only_a_persons_changes_in_the_window_to_observed_objects_count():
    since, until = datetime.now(UTC) - timedelta(minutes=5), datetime.now(UTC) + timedelta(minutes=5)
    observed = {MARCUS.lower(), FINANCE_FILES.lower()}
    mfa, password, passkey_read, trap = audit_noise(now(), MARCUS, MARCUS_UPN)
    change = audit_add_member(now(), MARCUS, MARCUS_UPN, FINANCE_FILES, "Finance Files")

    def counts(entry, **kw):
        return audit_map.relevant(entry, since=kw.get("since", since), until=until,
                                  observed=kw.get("observed", observed))

    assert counts(change) and counts(password)                  # a person, a write, in the window, on Marcus
    assert not counts(mfa)                                       # an app: the MFA service
    assert not counts(trap)                                      # `app` holds the admin, `user` is null
    assert not counts(passkey_read)                              # a read
    assert not counts(change, since=datetime.now(UTC) + timedelta(minutes=1))  # before the window
    assert not counts(change, observed={"someone-else"})         # not an object this run looked at
    ours = audit_add_member(now(), MARCUS, MARCUS_UPN, FINANCE_FILES, "Finance Files",
                            initiated={"user": None, "app": {"displayName": "northwind-ai-tech"}})
    assert not counts(ours)                                      # our own writes


# ------------------------------------------------------------------ audit_draft


async def test_the_audit_draft_learns_the_mapped_change_and_lists_the_rest(rt, tenant, escalated):  # noqa: F811
    rt.drafter_model = drafter_model(FINANCE_PLAYBOOK)
    admin_adds_marcus(tenant)
    tenant.audits += audit_noise(now(), MARCUS, MARCUS_UPN)

    draft = await runner.audit_draft(rt, escalated, by="aditya")

    found = rt.store.last_event(escalated, "audit_observed").payload
    assert found["status"] == "found"
    assert [(s["action"], s["params"], s["verified"]) for s in found["mapped"]] == [
        ("group.addMember", {"groupId": FINANCE_FILES, "userId": MARCUS}, {"memberOf": True})]
    assert [u["activity"] for u in found["unmapped"]] == ["Reset user password"]
    assert "DeviceToken" not in str([e.payload for e in rt.store.events(escalated)])
    assert draft.ok and draft.source.kind == "audit_log" and draft.source.allowed_actions == ["group.addMember"]
    assert rt.store.last_event(escalated, "playbook_drafted").payload["source"] == "audit_log"
    assert rt.store.get_run(escalated).state.value == "ESCALATED"  # learning doesn't close the run


async def test_a_change_already_undone_isnt_learned(rt, tenant, escalated):  # noqa: F811
    tenant.audits.append(audit_add_member(now(), MARCUS, MARCUS_UPN, FINANCE_FILES, "Finance Files"))  # no member

    assert await runner.audit_draft(rt, escalated, by="aditya") is None
    found = rt.store.last_event(escalated, "audit_observed").payload
    assert found["status"] == "found" and not found["mapped"] and len(found["undone"]) == 1
    assert not rt.store.events_of(escalated, "playbook_drafted")


async def test_nothing_in_the_audit_logs_times_out(rt, tenant, escalated, sleeps):  # noqa: F811
    assert await runner.audit_draft(rt, escalated, by="aditya") is None
    statuses = [e.payload["status"] for e in rt.store.events_of(escalated, "audit_observed")]
    assert statuses == ["waiting", "timed_out"]
    assert sleeps == [rt.audit_poll_s] * int(rt.audit_wait_s // rt.audit_poll_s)  # 20 waits, 21 polls


async def test_it_waits_one_more_poll_for_stragglers(rt, tenant, escalated, sleeps):  # noqa: F811
    rt.drafter_model = drafter_model(FINANCE_PLAYBOOK)
    admin_adds_marcus(tenant)
    await runner.audit_draft(rt, escalated, by="aditya")
    assert len(sleeps) == 1  # found on the first poll, read once more, done


async def test_a_change_from_before_the_escalation_doesnt_count(rt, tenant, escalated):  # noqa: F811
    admin_adds_marcus(tenant, at=now(-30))
    assert await runner.audit_draft(rt, escalated, by="aditya") is None
    assert rt.store.last_event(escalated, "audit_observed").payload["status"] == "timed_out"


async def test_only_an_escalated_run_is_read(rt, tenant, gated):
    with pytest.raises(runner.RunError, match="ESCALATED"):
        await runner.audit_draft(rt, gated(), by="aditya")


# ------------------------------------------------------------------ the route and the CLI


async def test_the_audit_draft_route(rt, tenant, escalated, gated):  # noqa: F811
    rt.drafter_model = drafter_model(FINANCE_PLAYBOOK)
    admin_adds_marcus(tenant)
    app = api.create_app(rt, web_origin="http://ui.test")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test") as client:
        assert (await client.post(f"/runs/{gated()}/audit-draft", json={"by": "aditya"})).status_code == 409
        assert (await client.post(f"/runs/{escalated}/audit-draft", json={"by": "aditya"})).status_code == 202
        await app.state.tasks.drain()
        shown = (await client.get(f"/runs/{escalated}")).json()
    assert shown["audit"]["status"] == "found" and shown["playbook_draft"]["source"] == "audit_log"
    found = rt.store.last_event(escalated, "audit_observed")
    assert shown["audit"]["at"] == found.created_at.isoformat()  # the web's waiting timer


async def test_the_cli_shows_what_it_learned_and_what_it_cant(rt, tenant, escalated, capsys):  # noqa: F811
    rt.drafter_model = drafter_model(FINANCE_PLAYBOOK)
    admin_adds_marcus(tenant)
    tenant.audits += audit_noise(now(), MARCUS, MARCUS_UPN)

    assert await audit_playbook(rt, escalated, by="aditya", ask=lambda q: "n") == 1  # shown, not saved
    out = capsys.readouterr().out
    assert "learned: group.addMember" in out and "can't learn: Reset user password" in out
