"""Learning cases (Build-W4 "L1-L4"), end to end offline. Each spans several tickets in one FakeTenant
(eval_harness.offline), so they are tests rather than one-ticket entries in cases.yaml (Build-Day20
decision 12). The drafter is conftest.drafter_model; everything else is the real runner.

L1  a technician's console run -> close -> save as playbook (A3) -> the next ticket of that kind runs hands-free.
L2  a fix made in the Entra admin center -> audit-log draft -> A2 at most -> the next ticket waits for one
    approval -> after that successful run, A3 is allowed.
L3  a written playbook (/playbooks/new) -> A2 at most -> the next ticket waits for one approval -> then A3.
L4  L1 through the HTTP routes the workspace and review screen call (Build-Day21 decision 17): manual step,
    close, save as playbook, check, approve at A3, and the next ticket runs hands-free under the learned badge.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest
from conftest import DOMAIN, drafter_model
from eval_harness import bypasses, fill, offline, start_ticket
from fake_tenant import audit_add_member, audit_noise

from tech import api, runner
from tech.core.store import RunState
from tech.playbooks import PlaybookRejected
from tech.safety.policy import UNIVERSAL_NEVER

SITE = "site.grant_access"

# What the drafter is expected to learn from the console run (the drafter itself is tested in test_drafter.py).
SITE_PLAYBOOK = f"""\
intent: {SITE}
goal: The person named in the ticket can open the team site the requester asked for.
must_investigate:
  - users.search(person)          # the person to add
  - users.memberOf(requester)     # the team sites (M365 groups) the requester's own groups give
definition_of_done:
  - message_sent: {{to: "$requester"}}
allowed_actions: [group.addMember]
gotchas:
  - A team site's access is its Microsoft 365 group; add the person to that group, not to a security group.
never:
""" + "".join(f"  - {line}\n" for line in [*UNIVERSAL_NEVER, "Add anyone to a privileged or admin group."])

# The first ticket: no playbook, and the investigator escalates (it can't tell Taylor may grant site access).
JORDAN = {
    "from": "tgarcia",
    "ticket": "Please give Jordan Lee access to the Northwind Operations site.",
    "triage": {"intents": ["unknown"], "entities": {}},
    "reads": [["users_search", {"name": "Jordan"}], ["users_member_of", {"user_id": "${tgarcia}"}]],
    "plan": {"intent": SITE, "summary": "Jordan Lee needs the Northwind Operations site", "steps": [],
             "escalate": True, "escalation_reason": "Not sure Taylor may grant access to this site",
             "diagnosis": "Jordan isn't in Northwind Operations, the site's M365 group",
             "suggested_next_step": "Confirm with Taylor, then add Jordan to Northwind Operations"},
}
# The second: the same kind of ticket, now with the learned playbook.
LUIS = {
    "from": "tgarcia",
    "ticket": "Can Luis Ortega get into the Northwind Operations site too?",
    "triage": {"intents": [SITE], "entities": {}},
    "reads": [["users_search", {"name": "Luis"}], ["users_member_of", {"user_id": "${tgarcia}"}]],
    "plan": {"intent": SITE, "summary": "Add Luis Ortega to Northwind Operations", "steps": [
        {"id": "add", "action": "group.addMember", "params": {"groupId": "${nw_ops}", "userId": "${luis.ortega}"}}]},
}


async def test_l1_a_console_run_becomes_a_playbook_the_next_ticket_runs_hands_free():
    async with offline() as off:
        rt, ids = off.rt, off.ids

        # 1. no playbook; the investigator escalates
        first, state, _ = await start_ticket(off, fill(JORDAN, ids))
        assert state == RunState.ESCALATED

        # 2. the technician does it in the console, and closes the run
        ok, _, error = await runner.technician_step(rt, first, "group.addMember",
                                                    {"groupId": ids["nw_ops"], "userId": ids["jlee"]}, by="aditya")
        assert ok, error
        assert await runner.technician_close(rt, first, by="aditya", note="Taylor confirmed on the phone") == (
            RunState.DONE)
        assert ids["jlee"] in off.tenant.members[ids["nw_ops"]]

        # 3. save as playbook, straight to A3 (a technician run is a successful run behind it)
        rt.drafter_model = drafter_model(SITE_PLAYBOOK)
        draft = await runner.draft_playbook(rt, first)
        assert draft.ok and draft.intent == SITE and draft.source.kind == "technician_run", draft.problems
        assert "Taylor confirmed on the phone" in str(draft.source.notes)
        row = rt.library.approve(SITE, draft.yaml, autonomy="A3", by="aditya", source="technician_run",
                                 source_run_id=first)
        assert (row.version, rt.library.autonomy()[SITE]) == (1, "A3")

        # 4. the next ticket of this kind: the playbook, A3, no approval, DONE
        second, state, _ = await start_ticket(off, fill(LUIS, ids))
        assert state == RunState.DONE
        plan = rt.store.last_event(second, "plan").payload
        assert plan["no_playbook"] is False and plan["playbook_versions"] == {SITE: 1}
        [decision] = [e.payload for e in rt.store.events_of(second, "gate_decision")]
        assert (decision["decision"], decision["rule_id"]) == ("auto", "autonomy.a3.write.reversible")
        assert not rt.store.events_of(second, "approval")
        assert ids["luis.ortega"] in off.tenant.members[ids["nw_ops"]]

        for run_id in (first, second):
            assert bypasses(rt.store.events(run_id)) == []


async def test_l2_an_admin_center_fix_becomes_an_a2_playbook_from_the_audit_logs():
    async with offline() as off:
        rt, ids, tenant = off.rt, off.ids, off.tenant

        # 1. no playbook; the investigator escalates
        first, state, _ = await start_ticket(off, fill(JORDAN, ids))
        assert state == RunState.ESCALATED

        # 2. the technician fixes it in the Entra admin center (and resets a password while there)
        at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        tenant.members[ids["nw_ops"]].add(ids["jlee"])
        jordan = f"jlee@{DOMAIN}"
        tenant.audits += [audit_add_member(at, ids["jlee"], jordan, ids["nw_ops"], "Northwind Operations"),
                          *audit_noise(at, ids["jlee"], jordan)]

        # 3. "I fixed it in the admin center": the audit logs give a draft with only the mapped change
        rt.drafter_model = drafter_model(SITE_PLAYBOOK)
        draft = await runner.audit_draft(rt, first, by="aditya")
        assert draft.ok and draft.source.kind == "audit_log", draft.problems
        assert [s.action for s in draft.source.steps] == ["group.addMember"]
        found = rt.store.last_event(first, "audit_observed").payload
        assert [u["activity"] for u in found["unmapped"]] == ["Reset user password"]
        assert rt.store.get_run(first).state == RunState.ESCALATED

        # 4. A2 at most; and the draft can't be widened at approval
        with pytest.raises(PlaybookRejected, match="A3 needs"):
            rt.library.approve(SITE, draft.yaml, autonomy="A3", by="aditya", source="audit_log", source_run_id=first)
        widened = draft.yaml.replace("[group.addMember]", "[group.addMember, user.update]")
        with pytest.raises(PlaybookRejected, match="never did"):
            rt.library.approve(SITE, widened, autonomy="A2", by="aditya", source="audit_log", source_run_id=first)
        rt.library.approve(SITE, draft.yaml, autonomy="A2", by="aditya", source="audit_log", source_run_id=first)
        assert rt.library.current_ceiling(SITE) == "A2"

        # 5. the next ticket uses the playbook, and at A2 an unproven playbook waits for a person
        second, state, _ = await start_ticket(off, fill(LUIS, ids))
        assert state == RunState.AWAITING_APPROVAL
        [decision] = [e.payload for e in rt.store.events_of(second, "gate_decision")]
        assert (decision["decision"], decision["rule_id"]) == ("approve", "autonomy.unproven")
        assert await runner.approve(rt, second, by="tgarcia") == RunState.DONE

        # 6. one successful approved run on this version: now it may be A3
        assert rt.library.current_ceiling(SITE) == "A3"
        rt.library.set_autonomy(SITE, "A3", by="aditya")

        for run_id in (first, second):
            assert bypasses(rt.store.events(run_id)) == []


@asynccontextmanager
async def http(rt):
    app = api.create_app(rt, web_origin="http://ui.test")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test") as client:
        yield app, client


async def test_l3_a_written_playbook_is_a2_until_a_run_on_it_succeeds():
    async with offline() as off, http(off.rt) as (app, client):
        rt, ids = off.rt, off.ids

        # 1. /playbooks/new: intent, plain-English steps, allowed actions -> a draft (nothing saved)
        rt.drafter_model = drafter_model(SITE_PLAYBOOK)
        draft = (await client.post("/playbooks", json={
            "intent": SITE, "text": "Add the person to the team site's Microsoft 365 group. Never an admin group.",
            "allowed_actions": ["group.addMember"], "by": "aditya"})).json()
        assert draft["ok"] and draft["source"] == "written" and draft["source_run_id"] is None, draft["problems"]

        # 2. the review screen's check: no problems, ceiling A2; so A3 is refused and A2 approved
        check = (await client.post(f"/playbooks/{SITE}/check", json={"yaml": draft["yaml"], "source": "written"}))
        assert check.json() == {"problems": [], "ceiling": "A2"}
        body = {"yaml": draft["yaml"], "by": "aditya", "source": "written"}
        assert (await client.post(f"/playbooks/{SITE}/approve", json=body | {"autonomy": "A3"})).status_code == 422
        assert (await client.post(f"/playbooks/{SITE}/approve", json=body | {"autonomy": "A2"})).status_code == 201

        # 3. the next ticket uses it, and an unproven playbook waits for a person
        second, state, _ = await start_ticket(off, fill(LUIS, ids))
        assert state == RunState.AWAITING_APPROVAL
        [decision] = [e.payload for e in rt.store.events_of(second, "gate_decision")]
        assert (decision["decision"], decision["rule_id"]) == ("approve", "autonomy.unproven")
        snap = (await client.get(f"/runs/{second}")).json()
        assert snap["no_playbook"] is False
        assert snap["playbooks_used"] == [{"intent": SITE, "version": 1, "source": "written", "source_run_id": None}]
        assert (await client.post(f"/runs/{second}/approval", json={"by": "tgarcia"})).status_code == 202
        await app.state.tasks.drain()
        assert rt.store.get_run(second).state == RunState.DONE

        # 4. one successful approved run on this version: the library lets it be A3
        assert (await client.get(f"/playbooks/{SITE}")).json()["ceiling"] == "A3"
        r = await client.post(f"/playbooks/{SITE}/autonomy", json={"level": "A3", "by": "aditya"})
        assert r.status_code == 200 and r.json()["autonomy"] == "A3"
        assert bypasses(rt.store.events(second)) == []


async def test_l4_a_console_run_through_the_screens_routes_becomes_a_hands_free_playbook():
    async with offline() as off, http(off.rt) as (app, client):
        rt, ids = off.rt, off.ids

        # 1. no playbook; the investigator escalates; the workspace shows the banner and the diagnosis
        first, state, _ = await start_ticket(off, fill(JORDAN, ids))
        assert state == RunState.ESCALATED
        snap = (await client.get(f"/runs/{first}")).json()
        assert snap["no_playbook"] is True and snap["outcome"]["diagnosis"].startswith("Jordan isn't in")
        assert {o["displayName"] for o in (await client.get(f"/runs/{first}/observed")).json()} >= {"Jordan Lee"}

        # 2. "Do it myself": a console action from the catalogue, then Close
        console = {a["name"] for a in (await client.get("/catalogue")).json() if a["console"]}
        assert "group.addMember" in console
        r = await client.post(f"/runs/{first}/manual-step", json={
            "action": "group.addMember", "params": {"groupId": ids["nw_ops"], "userId": ids["jlee"]}, "by": "aditya"})
        assert r.json() == {"ok": True, "step_id": "t1", "error": None, "state": "ESCALATED"}
        r = await client.post(f"/runs/{first}/close", json={"by": "aditya", "note": "Taylor confirmed"})
        assert r.status_code == 202
        await app.state.tasks.drain()
        snap = (await client.get(f"/runs/{first}")).json()
        assert snap["run"]["state"] == "DONE" and snap["can_save_playbook"] is True
        assert snap["technician_steps"][0]["params"]["groupName"] == "Northwind Operations"

        # 3. "Save as playbook" -> the review screen: check (A3 allowed: a technician run is behind it), approve
        rt.drafter_model = drafter_model(SITE_PLAYBOOK)
        draft = (await client.post(f"/runs/{first}/playbook-draft", json={})).json()
        assert draft["ok"] and (draft["intent"], draft["source"], draft["source_run_id"]) == (
            SITE, "technician_run", first)
        assert (await client.get(f"/runs/{first}")).json()["playbook_draft"]["yaml"] == draft["yaml"]
        source = {"source": "technician_run", "source_run_id": first}
        check = await client.post(f"/playbooks/{SITE}/check", json={"yaml": draft["yaml"]} | source)
        assert check.json() == {"problems": [], "ceiling": "A3"}
        r = await client.post(f"/playbooks/{SITE}/approve", json={"yaml": draft["yaml"], "autonomy": "A3",
                                                                  "by": "aditya"} | source)
        assert r.status_code == 201 and (r.json()["version"], r.json()["autonomy"]) == (1, "A3")

        # 4. the next ticket: the learned playbook at A3, no approval, DONE, and the snapshot says so
        second, state, _ = await start_ticket(off, fill(LUIS, ids))
        assert state == RunState.DONE
        snap = (await client.get(f"/runs/{second}")).json()
        assert snap["approval"] is None and snap["no_playbook"] is False
        assert snap["playbooks_used"] == [
            {"intent": SITE, "version": 1, "source": "technician_run", "source_run_id": first}]

        # 5. the library demotes it in one click; the next one needs a person again
        r = await client.post(f"/playbooks/{SITE}/autonomy", json={"level": "A2", "by": "aditya"})
        assert r.json()["autonomy"] == "A2"
        third, state, _ = await start_ticket(off, fill(LUIS, ids) | {"ticket": "And Luis again, sorry"})
        assert state == RunState.AWAITING_APPROVAL

        for run_id in (first, second, third):
            assert bypasses(rt.store.events(run_id)) == []
