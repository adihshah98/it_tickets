"""Close-out: Sonnet's drafts (context, no secrets), then the orchestration (DoD, scanner, messages, time)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import DOMAIN, TAYLOR, drafts_model
from fake_tenant import TAP_VALUE
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.function import FunctionModel

from tech import runner
from tech.agents.closeout import LINK_PLACEHOLDER, CloseoutDrafts, build_context, draft
from tech.closeout import WORK_TYPE, with_link, work_minutes
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
