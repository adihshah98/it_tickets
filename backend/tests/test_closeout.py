"""Close-out: Sonnet's drafts (context, no secrets), then the orchestration (DoD, scanner, messages, time)."""

import asyncio
import json

import pytest
from conftest import DOMAIN, TAYLOR, drafts_model
from fake_tenant import TAP_VALUE
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.function import FunctionModel

from tech.agents.closeout import LINK_PLACEHOLDER, CloseoutDrafts, build_context, draft
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
