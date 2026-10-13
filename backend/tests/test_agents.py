"""Agent wiring, offline: FunctionModel/TestModel stand in for Claude, respx for Graph."""

from datetime import date

import httpx
import pytest
import respx
from conftest import shipped_library
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import UsageLimits

from tech.agents.intake import check_requester, near_duplicate, run_intake
from tech.agents.investigator import READ_TOOLS, investigate
from tech.core.models import Triage
from tech.core.store import RunState, Store
from tech.execution.catalogue import ACTIONS
from tech.graph.client import GRAPH, GraphClient
from tech.graph.reads import READS
from tech.playbooks import load_client_facts

DOMAIN = "NorthwindParking.onmicrosoft.com"
TAYLOR = f"tgarcia@{DOMAIN}"
MARCUS = "8a590663-09f2-4ee8-ab1a-6195ca3943f9"
TICKET = "hey team, we have a new hire Priya Nair starting monday in accounting. same setup as Marcus pls"
# A throwaway terminator for loop-mechanics tests that don't build a real plan: an escalation passes the
# linter with no steps, so rules 4-7 (DoD coverage, must-investigate, questions) don't fire. Tests that
# assert a plan was *proposed* use a complete plan via `planned_terminator` instead.
PLAN = {"intent": "user.onboard", "summary": "Create Priya like Marcus", "steps": [],
        "escalate": True, "escalation_reason": "mechanics-test terminator"}


async def tokens() -> tuple[str, float]:
    return "tok", 9e12


async def no_sleep(_: float) -> None:
    pass


@pytest.fixture
def graph():
    return GraphClient(tokens, sleep=no_sleep)


@pytest.fixture
def store():
    return Store("sqlite://")


@pytest.fixture
def run_id(store):
    return store.create_run(store.create_ticket(TICKET, TAYLOR).id).id


@pytest.fixture
def facts():
    return load_client_facts(DOMAIN)


def triage(email: str = TAYLOR) -> Triage:
    return Triage.model_validate({
        "intents": ["user.onboard"],
        "entities": {"new_user": {"display_name": "Priya Nair", "department": "Accounting",
                                  "start_date": "2026-10-12"}, "peer_user_hint": "Marcus"},
        "requester": {"name": "Taylor", "email": email},
    })


def scripted(*turns):
    """A FunctionModel that plays `turns` in order. A turn is a list of (tool, args); "PLAN" means the output tool."""
    remaining = list(turns)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls = remaining.pop(0)
        output_tool = info.output_tools[0].name
        return ModelResponse(parts=[
            ToolCallPart(output_tool if name == "PLAN" else name, args) for name, args in calls
        ])

    return FunctionModel(respond)


def tool_returns(messages: list[ModelMessage]) -> dict[str, object]:
    return {p.tool_name: p.content for m in messages for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)}


async def run(store, run_id, graph, facts, model, **kw):
    return await investigate(store, run_id, TICKET, triage(), requester_approved=True, graph=graph, facts=facts,
                             domain=DOMAIN, library=shipped_library(store, facts), model=model,
                             today=date(2026, 10, 9), **kw)


def states(store, run_id) -> list[str]:
    return [e.payload["to"] for e in store.events(run_id) if e.type == "state"]


# ------------------------------------------------------------------ investigator


async def test_only_read_tools_and_record_finding_are_registered(store, run_id, graph, facts):
    seen: list[str] = []

    def respond(messages, info: AgentInfo) -> ModelResponse:
        seen.extend(t.name for t in info.function_tools)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, PLAN)])

    await run(store, run_id, graph, facts, FunctionModel(respond))
    assert sorted(seen) == sorted([f.__name__ for f in READ_TOOLS] + ["record_finding"])
    assert len(READ_TOOLS) == len(READS)
    write_names = {a.replace(".", "_").lower() for a in ACTIONS} | {"create", "add", "assign", "delete", "update"}
    assert not any(w in name.lower() for name in seen for w in write_names)


@respx.mock
async def test_tool_calls_and_findings_are_recorded_and_plan_proposed(store, run_id, graph, facts):
    respx.get(f"{GRAPH}/users").mock(return_value=httpx.Response(200, json={"value": [
        {"id": MARCUS, "displayName": "Marcus Lee", "userPrincipalName": f"marcus@{DOMAIN}"}]}))
    model = scripted(
        [("users_search", {"name": "Marcus"}), ("record_finding", {"finding": "Marcus is the peer"})],
        [("PLAN", PLAN)],
    )
    result = await run(store, run_id, graph, facts, model)

    assert result.plan.summary == PLAN["summary"]
    assert result.findings == ["Marcus is the peer"]
    events = {e.type: e.payload for e in store.events(run_id)}
    assert events["tool_call"]["tool"] == "users.search" and events["tool_call"]["ok"]
    assert events["tool_call"]["result"][0]["id"] == MARCUS
    assert events["finding"]["text"] == "Marcus is the peer"
    assert events["plan"]["plan"]["intent"] == "user.onboard"
    assert states(store, run_id) == ["RECEIVED", "INVESTIGATING", "ESCALATED"]


@respx.mock
async def test_graph_errors_go_back_to_the_model_not_up_the_stack(store, run_id, graph, facts):
    respx.get(f"{GRAPH}/users/{MARCUS}/memberOf").mock(return_value=httpx.Response(
        404, json={"error": {"code": "Request_ResourceNotFound", "message": "gone"}}))
    model = scripted([("users_member_of", {"user_id": MARCUS})], [("PLAN", PLAN)])
    result = await run(store, run_id, graph, facts, model)

    assert result.plan is not None  # the run finished without the Graph error bubbling up
    [call] = [e.payload for e in store.events(run_id) if e.type == "tool_call"]
    assert not call["ok"] and call["error"].startswith("404")


@respx.mock
async def test_a_read_with_no_response_goes_back_to_the_model(store, run_id, graph, facts):
    # Build-Day16 live run 1: a slow sign-in log raised httpx.ReadTimeout out of runner.start
    respx.get(f"{GRAPH}/auditLogs/signIns").mock(side_effect=httpx.ReadTimeout("slow"))
    model = scripted([("sign_ins", {"user_id": MARCUS})], [("PLAN", PLAN)])
    result = await run(store, run_id, graph, facts, model)

    assert result.plan is not None
    [call] = [e.payload for e in store.events(run_id) if e.type == "tool_call"]
    assert not call["ok"] and call["error"].startswith("0 ReadTimeout")


@respx.mock
async def test_read_budget_tells_the_model_to_submit(store, run_id, graph, facts):
    route = respx.get(f"{GRAPH}/subscribedSkus").mock(return_value=httpx.Response(200, json={"value": []}))
    returns = {}

    def respond(messages, info: AgentInfo) -> ModelResponse:
        returns.update(tool_returns(messages))
        if len(messages) < 4:
            return ModelResponse(parts=[ToolCallPart("subscribed_skus", {})])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, PLAN)])

    result = await run(store, run_id, graph, facts, FunctionModel(respond), max_reads=1)
    assert result.plan is not None and route.call_count == 1
    assert "read budget of 1 used up" in returns["subscribed_skus"]["error"]


async def test_refusal_escalates(store, run_id, graph, facts):
    def refuse(messages, info) -> ModelResponse:
        return ModelResponse(parts=[], finish_reason="content_filter", provider_details={"finish_reason": "refusal"})

    result = await run(store, run_id, graph, facts, FunctionModel(refuse))
    assert result.outcome == "escalated" and result.plan is None
    last = store.events(run_id)[-1].payload
    assert last["to"] == "ESCALATED" and last["kind"] == "refusal"


async def test_tool_call_limit_escalates_with_the_findings_so_far(store, run_id, graph, facts):
    def keep_finding(messages, info) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("record_finding", {"finding": f"finding {len(messages)}"})])

    result = await run(store, run_id, graph, facts, FunctionModel(keep_finding),
                       limits=UsageLimits(tool_calls_limit=2))
    assert result.outcome == "escalated" and result.findings == ["finding 1", "finding 3"]
    last = store.events(run_id)[-1].payload
    assert last["kind"] == "usage_limit" and last["findings"] == result.findings
    assert store.get_run(run_id).state == RunState.ESCALATED


async def test_timeout_escalates(store, run_id, graph, facts):
    import asyncio

    async def slow(messages, info) -> ModelResponse:
        await asyncio.sleep(1)
        return ModelResponse(parts=[TextPart("never")])

    result = await run(store, run_id, graph, facts, FunctionModel(slow), timeout_s=0.05)
    assert result.outcome == "escalated"
    assert store.events(run_id)[-1].payload["kind"] == "timeout"


async def test_model_api_error_escalates(store, run_id, graph, facts):
    def broken(messages, info) -> ModelResponse:
        raise ModelHTTPError(400, "claude-opus-5-5", {"message": "grammar too large"})

    result = await run(store, run_id, graph, facts, FunctionModel(broken))
    assert result.outcome == "escalated"
    assert store.events(run_id)[-1].payload["kind"] == "model_api"


async def test_investigator_escalation_is_escalated(store, run_id, graph, facts):
    plan = PLAN | {"escalate": True, "escalation_reason": "Looks like an account takeover"}
    result = await run(store, run_id, graph, facts, scripted([("PLAN", plan)]))
    assert result.outcome == "escalated" and result.plan is not None
    assert states(store, run_id)[-1] == "ESCALATED"
    assert [e.type for e in store.events(run_id)][-2] == "plan"


# ------------------------------------------------------------------ intake


async def test_intake_takes_the_requester_from_the_sender_not_the_body(store, run_id, facts):
    spoofed = triage(email=TAYLOR).model_dump(mode="json")
    model = TestModel(custom_output_args=spoofed)
    result, check = await run_intake(store, run_id, TICKET, f"JLee@{DOMAIN}", facts,
                                       library=shipped_library(store, facts), model=model)

    assert result.requester.email == f"jlee@{DOMAIN}".lower()
    assert not check.approved and check.scopes == {"user.onboard": False}
    event = next(e.payload for e in store.events(run_id) if e.type == "triage")
    assert event["requester_approved"] is False
    assert store.get_run(run_id).state == RunState.TRIAGED


KNOWN = ["mfa.reset", "user.offboard", "user.onboard", "file.restore_version"]


@pytest.mark.parametrize(("slug", "duplicates"), [
    ("file.restore", "file.restore_version"),
    ("file.restoreversion", "file.restore_version"),
    ("user.onboarding", "user.onboard"),
    ("user.onbaord", "user.onboard"),
    ("user.off_board", "user.offboard"),
    ("mfa.resets", "mfa.reset"),
    ("user.onboard", None),  # a known intent is reused, not a duplicate
    ("unknown", None),
    ("user.rename", None),  # same first word, a different job
    ("mfa.register", None),
    ("group.add_member", None),
])
def test_near_duplicates_of_known_intents(slug, duplicates):
    assert near_duplicate(slug, KNOWN) == duplicates


def intake_turns(*intents: list[str]) -> tuple[FunctionModel, list[str]]:
    """An intake model that answers with these intents in turn; also returns the retry prompts it saw."""
    answers, retries = list(intents), []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        retries.extend(p.content for m in messages for p in getattr(m, "parts", [])
                       if p.part_kind == "retry-prompt" and isinstance(p.content, str))
        data = triage().model_dump(mode="json") | {"intents": answers.pop(0)}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, data)])

    return FunctionModel(respond), retries


async def test_intake_is_sent_back_to_reuse_a_known_intent(store, run_id, facts):
    model, retries = intake_turns(["user.onboarding"], ["user.onboard"])
    result, _ = await run_intake(store, run_id, TICKET, TAYLOR, facts, library=shipped_library(store, facts),
                                 model=model)

    assert result.intents == ["user.onboard"]
    assert len(retries) == 1 and "'user.onboarding' is the known intent 'user.onboard'" in retries[0]


async def test_intake_keeps_a_new_intent_with_no_playbook(store, run_id, facts):
    model, retries = intake_turns(["file.restore_version"])
    result, _ = await run_intake(store, run_id, TICKET, TAYLOR, facts, library=shipped_library(store, facts),
                                 model=model)

    assert result.intents == ["file.restore_version"] and retries == []


def test_requester_check(facts):
    assert check_requester(triage(TAYLOR), facts).approved
    assert not check_requester(triage(f"kevin@{DOMAIN}"), facts).approved
    mfa = triage().model_copy(update={"intents": ["mfa.reset"]})
    assert check_requester(mfa, facts).approved  # no approver list for a user's own MFA reset


# ------------------------------------------------------------------ linter in the loop

ACCOUNTING = "a55f62f7-a1fe-4314-8d61-88d40f8cfd54"
INVENTED = "00000000-1111-2222-3333-444444444444"


# A complete onboarding plan grounded on Marcus's seeded groups, so only the grounding of `group_id`
# varies between the rejected attempt (INVENTED) and the accepted one (ACCOUNTING). Rules 4-7 pass:
# the DoD producers are present, the questions are resolved, and the dependency ordering is backward.
def onboard_plan(group_id: str) -> dict:
    return {
        "intent": "user.onboard", "summary": "Onboard Priya like Marcus",
        "assumptions": ["peer is Marcus", "start_date is Monday 2026-10-12"],
        "confirm_on_approval": [{"key": "upn_format", "question": "priya.nair@?",
                                 "proposed": f"priya.nair@{DOMAIN}", "evidence": "recent hires"}],
        "steps": [
            {"id": "create_user", "action": "user.create", "depends_on": [], "rationale": "new hire",
             "expected": {}, "risk_class": "write.reversible", "params": {
                 "displayName": "Priya Nair", "userPrincipalName": f"priya.nair@{DOMAIN}",
                 "mailNickname": "priya.nair", "usageLocation": "US"}},
            {"id": "g1", "action": "group.addMember", "depends_on": ["create_user"], "rationale": "mirror",
             "expected": {"member": True}, "risk_class": "write.reversible",
             "params": {"groupId": group_id, "userId": "$ref:create_user.output.id"}},
            {"id": "tap", "action": "tap.create", "depends_on": ["create_user"], "rationale": "sign-in",
             "expected": {}, "risk_class": "security.credential",
             "params": {"userId": "$ref:create_user.output.id", "lifetimeInMinutes": 480}},
            {"id": "send_link", "action": "link.sendOneTime", "depends_on": ["tap"], "rationale": "deliver",
             "expected": {}, "risk_class": "none", "params": {
                 "to": f"tgarcia@{DOMAIN}", "userId": "$ref:create_user.output.id",
                 "tap": "$ref:tap.output.tap"}},
            {"id": "lic", "action": "followup.schedule", "depends_on": ["create_user"], "rationale": "start date",
             "expected": {}, "risk_class": "none", "params": {
                 "kind": "license.assign", "runAt": "2026-10-12T10:00:00Z",
                 "userId": "$ref:create_user.output.id", "skuId": SPB}},
        ],
        "needs_technician": [{"item": "RDS-Users / VPN-Users", "reason": "hosted desktop and VPN are outside Graph"}],
    }


SPB = "cbdc14ab-d96c-4c30-b9f4-6ada7cdc1d46"
# The must_investigate reads for onboarding, so rule 5 (must-investigate covered) is satisfied.
FULL_READS = [("users_search", {"name": "Marcus"}), ("users_get", {"user_id": MARCUS}),
              ("users_member_of", {"user_id": MARCUS}), ("users_license_details", {"user_id": MARCUS}),
              ("subscribed_skus", {}), ("users_list", {})]


def mock_reads() -> None:
    """Mock every onboarding read route to return Marcus / the seeded SKU."""
    respx.get(f"{GRAPH}/users/{MARCUS}/memberOf").mock(return_value=httpx.Response(200, json={"value": [
        {"@odata.type": "#microsoft.graph.group", "id": ACCOUNTING, "displayName": "Accounting",
         "groupTypes": ["Unified"], "mailEnabled": True, "securityEnabled": False}]}))
    respx.get(f"{GRAPH}/users/{MARCUS}/licenseDetails").mock(return_value=httpx.Response(
        200, json={"value": [{"skuId": SPB, "skuPartNumber": "SPB"}]}))
    respx.get(f"{GRAPH}/subscribedSkus").mock(return_value=httpx.Response(
        200, json={"value": [{"skuId": SPB, "skuPartNumber": "SPB", "prepaidUnits": {"enabled": 5},
                              "consumedUnits": 5}]}))
    respx.get(f"{GRAPH}/users/{MARCUS}/manager").mock(return_value=httpx.Response(
        404, json={"error": {"code": "Request_ResourceNotFound", "message": "no manager"}}))
    respx.get(f"{GRAPH}/users/{MARCUS}").mock(return_value=httpx.Response(
        200, json={"id": MARCUS, "displayName": "Marcus Webb", "department": "Accounting"}))
    respx.get(f"{GRAPH}/users").mock(return_value=httpx.Response(
        200, json={"value": [{"id": MARCUS, "userPrincipalName": f"marcus@{DOMAIN}"}]}))


@respx.mock
async def test_a_rejected_plan_goes_back_to_the_model_with_the_reasons(store, run_id, graph, facts):
    mock_reads()
    retry_prompts: list[str] = []
    turns = [FULL_READS, [("PLAN", onboard_plan(INVENTED))], [("PLAN", onboard_plan(ACCOUNTING))]]

    def respond(messages, info: AgentInfo) -> ModelResponse:
        retry_prompts.extend(str(p.content) for p in messages[-1].parts if p.part_kind == "retry-prompt")
        output_tool = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(output_tool if n == "PLAN" else n, a) for n, a in turns.pop(0)])

    result = await run(store, run_id, graph, facts, FunctionModel(respond))
    assert result.outcome == "planned" and result.plan.step("g1").params.groupId == ACCOUNTING
    assert len(retry_prompts) == 1 and "Plan rejected" in retry_prompts[0] and INVENTED in retry_prompts[0]
    lints = [e.payload for e in store.events(run_id) if e.type == "lint_result"]
    assert [(lr["attempt"], lr["ok"]) for lr in lints] == [(1, False), (2, True)]
    assert result.transcript.observed(ACCOUNTING) and "user.onboard" in result.playbooks


@respx.mock
async def test_a_plan_still_rejected_after_the_retries_escalates(store, run_id, graph, facts):
    mock_reads()
    result = await run(store, run_id, graph, facts,
                       scripted(FULL_READS, *[[("PLAN", onboard_plan(INVENTED))]] * 3))
    assert result.outcome == "escalated"
    last = store.events(run_id)[-1].payload
    assert last["kind"] == "lint" and INVENTED in last["reason"]
    assert len([e for e in store.events(run_id) if e.type == "lint_result"]) == 3  # first try + 2 retries


# ------------------------------------------------------------------ the MFA sign-in prefetch

DENNIS = "4de26d55-a0ad-487f-837c-3d6bec07fce6"
SIGN_IN = {"createdDateTime": "2026-10-12T15:30:00Z", "appDisplayName": "Teams", "ipAddress": "98.42.17.203",
           "status": {"errorCode": 0}, "location": {"city": "Fremont", "countryOrRegion": "US"}}
MFA_STOP = {"intent": "mfa.reset", "summary": "stop", "steps": [], "escalate": True, "escalation_reason": "test"}


def mfa_triage(hint: str | None = "Dennis") -> Triage:
    return Triage.model_validate({"intents": ["mfa.reset"], "entities": {"subject_user_hint": hint},
                                  "requester": {"name": "Dennis", "email": f"dennis@{DOMAIN}"}})


async def run_mfa(store, run_id, graph, facts, model, triage_=None, **kw):
    return await investigate(store, run_id, "lost my phone, reset my MFA", triage_ or mfa_triage(),
                             requester_approved=True, graph=graph, facts=facts, domain=DOMAIN,
                             library=shipped_library(store, facts), model=model, today=date(2026, 10, 12), **kw)


@respx.mock
async def test_an_mfa_reset_reads_the_subjects_sign_ins_once_started_early(store, run_id, graph, facts):
    respx.get(f"{GRAPH}/users").mock(return_value=httpx.Response(200, json={"value": [
        {"id": DENNIS, "displayName": "Dennis Okafor"}]}))
    sign_ins = respx.get(f"{GRAPH}/auditLogs/signIns").mock(return_value=httpx.Response(200, json={"value": [SIGN_IN]}))
    before_first_turn = []

    def respond(messages, info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            before_first_turn.append(sign_ins.call_count)
            return ModelResponse(parts=[ToolCallPart("sign_ins", {"user_id": DENNIS})])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, MFA_STOP)])

    result = await run_mfa(store, run_id, graph, facts, FunctionModel(respond))

    assert before_first_turn == [1]  # under way before the model asked
    assert sign_ins.call_count == 1  # the model's call used it: one slow query, not two
    [call] = [e.payload for e in store.events(run_id) if e.type == "tool_call"]
    assert call["tool"] == "signIns.list" and call["ok"] and call["result"][0]["city"] == "Fremont"
    assert [c.tool for c in result.transcript.calls] == ["signIns.list"]  # the subject lookup isn't in it


async def test_an_unused_prefetch_leaves_no_trace(store, run_id, graph, facts):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{GRAPH}/users").mock(return_value=httpx.Response(200, json={"value": [{"id": DENNIS}]}))
        router.get(f"{GRAPH}/auditLogs/signIns").mock(return_value=httpx.Response(200, json={"value": [SIGN_IN]}))
        result = await run_mfa(store, run_id, graph, facts, scripted([("PLAN", MFA_STOP)]))

    assert result.transcript.calls == [] and not result.transcript.observed(DENNIS)
    assert not [e for e in store.events(run_id) if e.type == "tool_call"]


async def test_no_prefetch_off_an_mfa_reset_or_for_an_ambiguous_subject(store, graph, facts):
    with respx.mock(assert_all_called=False) as router:
        users = router.get(f"{GRAPH}/users").mock(return_value=httpx.Response(200, json={"value": [
            {"id": DENNIS}, {"id": MARCUS}]}))
        sign_ins = router.get(f"{GRAPH}/auditLogs/signIns").mock(return_value=httpx.Response(200, json={"value": []}))

        await run(store, store.create_run(store.create_ticket(TICKET, TAYLOR).id).id, graph, facts,
                  scripted([("PLAN", PLAN)]))  # onboarding
        assert users.call_count == 0
        await run_mfa(store, store.create_run(store.create_ticket(TICKET, TAYLOR).id).id, graph, facts,
                      scripted([("PLAN", MFA_STOP)]), mfa_triage("D"))  # two users match "D"
        assert users.call_count == 1 and sign_ins.call_count == 0


@respx.mock
async def test_the_prefetch_honours_the_demo_sign_in_mock(store, run_id, graph, facts):
    respx.get(f"{GRAPH}/users/dennis%40{DOMAIN}").mock(return_value=httpx.Response(200, json={"id": DENNIS}))
    respx.get(f"{GRAPH}/users/{DENNIS}").mock(return_value=httpx.Response(200, json={
        "id": DENNIS, "createdDateTime": "2026-10-01T16:00:00Z"}))
    audit = respx.get(f"{GRAPH}/auditLogs/signIns").mock(return_value=httpx.Response(200, json={"value": []}))
    model = scripted([("sign_ins", {"user_id": DENNIS})], [("PLAN", MFA_STOP)])

    await run_mfa(store, run_id, graph, facts, model, mfa_triage(None), clean_sign_ins_for=DENNIS)

    [call] = [e.payload for e in store.events(run_id) if e.type == "tool_call"]
    assert call["result"][0]["city"] == "Sacramento" and audit.call_count == 0


async def test_a_sign_in_read_past_its_cap_is_unknown_not_clean(store, run_id, graph, facts, monkeypatch):
    import asyncio

    from tech.agents import investigator

    monkeypatch.setattr(investigator, "READ_TIMEOUTS_S", {"signIns.list": 0.05})

    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json={"value": []})

    with respx.mock(assert_all_called=False) as router:
        router.get(f"{GRAPH}/users").mock(return_value=httpx.Response(200, json={"value": [{"id": DENNIS}]}))
        router.get(f"{GRAPH}/auditLogs/signIns").mock(side_effect=slow)
        returns = {}

        def respond(messages, info: AgentInfo) -> ModelResponse:
            returns.update(tool_returns(messages))
            if len(messages) == 1:
                return ModelResponse(parts=[ToolCallPart("sign_ins", {"user_id": DENNIS})])
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, MFA_STOP)])

        result = await run_mfa(store, run_id, graph, facts, FunctionModel(respond))

    assert result.plan is not None  # the investigation carried on and finished
    assert "not as clean" in returns["sign_ins"]["error"]
    [call] = [e.payload for e in store.events(run_id) if e.type == "tool_call"]
    assert not call["ok"] and "no answer within" in call["error"]
    assert not result.transcript.called("signIns.list")  # rule 5 doesn't count a read that never answered
