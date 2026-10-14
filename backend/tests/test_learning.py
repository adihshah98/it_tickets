"""Learning cases (Build-W4 "L1-L4"), end to end offline. Each spans several tickets in one FakeTenant
(eval_harness.offline), so they are tests rather than one-ticket entries in cases.yaml (Build-Day20
decision 12). The drafter is conftest.drafter_model; everything else is the real runner.

L1  a technician's console run -> close -> save as playbook (A3) -> the next ticket of that kind runs hands-free.
"""

from conftest import drafter_model
from eval_harness import bypasses, fill, offline, start_ticket

from tech import runner
from tech.core.store import RunState
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
