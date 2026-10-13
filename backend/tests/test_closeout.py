"""Close-out: Sonnet's drafts (context, no secrets), then the orchestration (DoD, scanner, messages, time)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import DOMAIN, MARCUS, SOPHIE, TAYLOR, drafts_model, step
from fake_tenant import TAP_VALUE
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.function import FunctionModel

from tech import runner
from tech.agents.closeout import LINK_PLACEHOLDER, REPLY_NOTES, CloseoutDrafts, build_context, draft
from tech.closeout import WORK_TYPE, no_playbook_dod, with_link, work_minutes
from tech.core.models import Plan
from tech.core.store import RunState
from tech.execution import dod, links
from tech.playbooks import load_playbook

REQUESTER = {"name": "Taylor", "email": TAYLOR}


def context_for(rt, run_id, plan, probes=()):
    return build_context(rt.store, run_id, plan, list(probes), requester=REQUESTER,
                         playbook=load_playbook("user.onboard", DOMAIN))


# ------------------------------------------------------------------ drafting


async def test_the_context_has_what_was_done_and_no_secret_or_ticket(rt, executed):
    run_id, plan = await executed()
    context = context_for(rt, run_id, plan, [dod.Probe("user_exists", True, "ok")])
    text = json.dumps(context)

    assert context["new_user"]["userPrincipalName"] == f"priya.nair@{DOMAIN}"
    assert context["one_time_link"]["write"] == LINK_PLACEHOLDER
    assert {s["action"] for s in context["steps_done"]} >= {"user.create", "group.addMember", "tap.create"}
    assert context["definition_of_done"] == [{"probe": "user_exists", "ok": True, "detail": "ok"}]
    token = links.for_run(rt.store, run_id)[0].token
    assert TAP_VALUE not in text and token not in text and "new hire Priya" not in text


async def test_draft_returns_the_models_drafts(rt, executed):
    run_id, plan = await executed()
    model = drafts_model()
    drafts = await draft(context_for(rt, run_id, plan), model=model)

    assert isinstance(drafts, CloseoutDrafts) and LINK_PLACEHOLDER in drafts.reply_body
    assert "priya.nair@" in str(model.prompts[0])


async def test_draft_times_out():
    async def slow(messages, info) -> ModelResponse:
        await asyncio.sleep(1)
        raise AssertionError("unreachable")

    with pytest.raises(TimeoutError):
        await draft({"requester": REQUESTER}, model=FunctionModel(slow), timeout_s=0.05)


# ------------------------------------------------------------------ orchestration


def payloads(rt, run_id, type):
    return [e.payload for e in rt.store.events_of(run_id, type)]


async def test_a_clean_run_closes_out_to_done(rt, gated, tenant):
    run_id = gated()
    assert await runner.approve(rt, run_id, by=TAYLOR) == RunState.DONE

    checks = payloads(rt, run_id, "done_check")
    assert [c["probe"] for c in checks][-1] == "message_sent" and all(c["ok"] for c in checks)
    sent = {m["kind"]: m for m in payloads(rt, run_id, "message_sent")}
    assert sent["reply"]["to"] == TAYLOR and LINK_PLACEHOLDER in sent["reply"]["text"]
    assert sent["psa_note"]["text"]
    entry = payloads(rt, run_id, "time_entry")[0]
    assert entry["work_type"] == WORK_TYPE == "AI Automation" and entry["minutes"] >= 1
    everything = json.dumps([e.payload for e in rt.store.events(run_id)])
    assert TAP_VALUE not in everything and links.for_run(rt.store, run_id)[0].token not in everything


async def test_a_reply_containing_the_tap_is_blocked_and_the_run_escalates(rt, gated, tenant):
    rt.closeout_model = drafts_model(reply=f"Her pass is {TAP_VALUE}, link {{{{link}}}}")
    run_id = gated()

    assert await runner.approve(rt, run_id, by=TAYLOR) == RunState.ESCALATED
    blocked = payloads(rt, run_id, "message_blocked")
    assert blocked[0]["kind"] == "reply" and blocked[0]["reason"] == "secret" and blocked[0]["hits"]
    assert all(m["kind"] != "reply" for m in payloads(rt, run_id, "message_sent"))
    state = rt.store.last_event(run_id, "state").payload
    assert state["kind"] == "dod" and "message_sent" in state["failed_probes"]
    assert TAP_VALUE not in json.dumps([e.payload for e in rt.store.events(run_id)])


async def test_a_failed_graph_probe_holds_the_reply_but_writes_the_psa_note(rt, gated, tenant, monkeypatch):
    from tech.execution import dod as dod_module

    async def no_tap(ctx, args):
        return dod_module.Probe("tap_active", False, "no usable TAP (0 found)")

    monkeypatch.setitem(dod_module.PROBES, "tap_active", no_tap)
    run_id = gated()

    assert await runner.approve(rt, run_id, by=TAYLOR) == RunState.ESCALATED
    assert payloads(rt, run_id, "message_blocked")[0] | {"hits": []} == {
        "kind": "reply", "reason": "dod", "detail": "the definition of done isn't met", "hits": []}
    assert [m["kind"] for m in payloads(rt, run_id, "message_sent")] == ["psa_note"]
    assert rt.store.last_event(run_id, "state").payload["failed_probes"] == ["tap_active", "message_sent"]


async def test_a_model_error_escalates_without_crashing(rt, gated, tenant):
    def broken(messages, info):
        raise ModelHTTPError(status_code=529, model_name="sonnet", body="overloaded")

    rt.closeout_model = FunctionModel(broken)
    run_id = gated()

    assert await runner.approve(rt, run_id, by=TAYLOR) == RunState.ESCALATED
    assert payloads(rt, run_id, "error")[0]["where"] == "closeout"
    assert payloads(rt, run_id, "time_entry")  # still logged


@pytest.mark.parametrize("body", ["Open {{ link }} soon", "Open {{Link}} soon"])
def test_a_mangled_placeholder_is_normalised(body):
    assert with_link(body, True) == f"Open {LINK_PLACEHOLDER} soon"


def test_a_missing_placeholder_gets_a_link_line_only_when_there_is_a_link():
    assert with_link("All set.", True).endswith(f"Sign-in link (opens once): {LINK_PLACEHOLDER}")
    assert with_link("All set.", False) == "All set."


def test_work_minutes_excludes_waiting_for_an_approver_and_mixes_naive_and_aware():
    t0 = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
    states = [("RECEIVED", t0), ("AWAITING_APPROVAL", t0 + timedelta(seconds=70)),
              ("EXECUTING", (t0 + timedelta(minutes=30)).replace(tzinfo=None)),  # SQLite hands back naive
              ("CLOSING", t0 + timedelta(minutes=31))]
    assert work_minutes(states, t0 + timedelta(minutes=31, seconds=20)) == 3  # 70 s + 60 s + 20 s -> 2.5 -> 3
    assert work_minutes([("CLOSING", t0)], t0) == 1


def test_a_two_intent_run_is_checked_on_both_playbooks():
    from tech.closeout import dod_playbook
    from tech.playbooks import load_playbooks

    merged = dod_playbook(load_playbooks(["user.offboard", "user.onboard"], DOMAIN))
    names = [next(iter(item)) for item in merged.definition_of_done]
    assert merged.intent == "user.offboard" and names[0] == "account_disabled"  # the plan's own playbook first
    assert {"user_exists", "tap_active", "message_sent", "sessions_revoked"} <= set(names)
    assert names.count("needs_technician_listed") == 1  # in both playbooks, probed once
    assert dod_playbook({}) is None


# ------------------------------------------------------------------ no playbook (Build-W4 Day 18)

RESTORED = "Hi Sophie, Q3 Parking Rates is back to yesterday afternoon's version. Nothing is lost."


async def test_a_no_playbook_restore_closes_out_on_its_own_dod(rt, restore_gated):
    rt.closeout_model = model = drafts_model(reply=RESTORED, note="Restored Q3 Parking Rates.xlsx to 2.0")
    run_id, _, _ = restore_gated()

    assert await runner.approve(rt, run_id, by=TAYLOR) == RunState.DONE
    checks = payloads(rt, run_id, "done_check")
    assert [(c["probe"], c["ok"]) for c in checks] == [("version_restored", True), ("message_sent", True)]
    assert "Q3 Parking Rates.xlsx is version 2.0 again (now 4.0)" in checks[0]["detail"]
    assert checks[1]["detail"] == f"reply to {SOPHIE}"                          # no one-time link asked for
    assert {m["kind"] for m in payloads(rt, run_id, "message_sent")} == {"reply", "psa_note"}
    assert payloads(rt, run_id, "time_entry")[0]["work_type"] == WORK_TYPE
    assert "nothing is lost" in str(model.prompts[0])                             # the restore note reached Sonnet


async def test_a_restore_whose_content_doesnt_match_holds_the_reply(rt, restore_gated, tenant):
    run_id, _, item = restore_gated()
    real = tenant._drive_route

    def corrupt(method, drive_id, rest):  # the item's current bytes aren't version 2.0's
        if rest == f"/items/{item}/content":
            return httpx.Response(200, content=b"something else")
        return real(method, drive_id, rest)

    tenant._drive_route = corrupt
    assert await runner.approve(rt, run_id, by=TAYLOR) == RunState.ESCALATED
    assert rt.store.last_event(run_id, "state").payload["failed_probes"] == ["version_restored", "message_sent"]
    assert payloads(rt, run_id, "message_blocked")[0]["reason"] == "dod"
    assert [m["kind"] for m in payloads(rt, run_id, "message_sent")] == ["psa_note"]


def test_the_no_playbook_dod_is_a_probe_per_action_that_has_one_then_the_reply():
    plan = Plan.model_validate({"intent": "unknown", "summary": "x", "steps": [
        step("rename", "user.update", {"userId": MARCUS, "jobTitle": "Lead"}, "write.reversible")]})
    assert no_playbook_dod(plan).definition_of_done == [{"message_sent": {"to": "$requester"}}]
    assert no_playbook_dod(plan.model_copy(update={"steps": []})) is None


async def test_the_context_carries_the_note_for_each_action_done(rt, restore_gated):
    run_id, plan, _ = restore_gated()
    await runner.approve(rt, run_id, by=TAYLOR)
    context = build_context(rt.store, run_id, plan, [], requester=REQUESTER, playbook=None)
    assert context["notes"] == [REPLY_NOTES["file.restoreVersion"]]
    assert context_for(rt, run_id, plan.model_copy(update={"steps": []}))["notes"] == []
