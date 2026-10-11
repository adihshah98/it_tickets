"""Shared fixtures for the linter and gate: Marcus's investigation and the onboarding plan it led to
(the plan is the one Opus proposed in the Day 3 live run)."""

import copy

import pytest

from tech.core.models import Plan
from tech.graph.reads import Transcript
from tech.playbooks import load_client_facts, load_playbooks


def drafts_model(reply: str = "Hi Taylor, Priya is set up. Sign-in link: {{link}}. Use the Outlook mobile app.",
                 note: str = "Created priya.nair; groups mirrored; license follow-up Monday.",
                 subject: str = "Priya Nair is set up"):
    """A FunctionModel standing in for Sonnet at close-out: returns these drafts, and keeps the prompts it got."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    def respond(messages, info):
        respond.prompts.append(messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            "reply_subject": subject, "reply_body": reply, "psa_note": note})])

    respond.prompts = []
    model = FunctionModel(respond)
    model.prompts = respond.prompts
    return model


DOMAIN = "NorthwindParking.onmicrosoft.com"
MARCUS = "8a590663-09f2-4ee8-ab1a-6195ca3943f9"
SPB = "cbdc14ab-d96c-4c30-b9f4-6ada7cdc1d46"
ACCOUNTING = "a55f62f7-a1fe-4314-8d61-88d40f8cfd54"
FINANCE_FILES = "48297cf8-723a-4826-a8b0-199373841df5"
RDS_USERS = "7d1e2c3b-0000-4000-8000-000000000001"
ORG_WIDE = "7d1e2c3b-0000-4000-8000-000000000002"
ANNOUNCE_DL = "7d1e2c3b-0000-4000-8000-000000000003"
IT_ADMINS = "7d1e2c3b-0000-4000-8000-000000000004"


def group(gid: str, name: str, kind: str, role_assignable: bool = False) -> dict:
    return {"id": gid, "displayName": name, "kind": kind, "mail": None, "roleAssignable": role_assignable}


def marcus_transcript() -> Transcript:
    t = Transcript()
    t.record("users.search", {"name": "Marcus"}, [{"id": MARCUS, "displayName": "Marcus Webb"}])
    t.record("users.get", {"user_id": MARCUS}, {"id": MARCUS, "displayName": "Marcus Webb", "department": "Accounting"})
    t.record("users.memberOf", {"user_id": MARCUS}, [
        group(ACCOUNTING, "Accounting", "m365"),
        group(FINANCE_FILES, "Finance Files", "security"),
        group(RDS_USERS, "RDS-Users", "security"),
        group(ORG_WIDE, "Northwind Parking", "m365"),
        group(ANNOUNCE_DL, "accounting-announce", "distribution_list"),
        group(IT_ADMINS, "IT Admins", "security", role_assignable=True),
    ])
    t.record("users.licenseDetails", {"user_id": MARCUS}, [{"skuId": SPB, "skuPartNumber": "SPB"}])
    t.record("subscribedSkus.list", {}, [{"skuId": SPB, "skuPartNumber": "SPB", "available": 0}])
    t.record("users.list", {}, [{"id": MARCUS, "userPrincipalName": f"marcus.webb@{DOMAIN}"}])
    return t


def step(sid: str, action: str, params: dict, risk: str, depends_on: tuple[str, ...] = ()) -> dict:
    return {"id": sid, "action": action, "params": params, "depends_on": list(depends_on),
            "rationale": "test", "expected": {}, "risk_class": risk}


NEW = "$ref:create_user.output.id"

ONBOARD_PLAN = {
    "intent": "user.onboard",
    "summary": "Onboard Priya Nair like Marcus Webb",
    "assumptions": [
        "peer is Marcus Webb, named in the request",
        "start_date is Monday 2026-10-12, so the license is assigned on the start date",
    ],
    "confirm_on_approval": [
        {"key": "upn_format", "question": "Use priya.nair@ (the recent-hire format)?",
         "proposed": f"priya.nair@{DOMAIN}", "evidence": "recent hires use first.last@"},
    ],
    "steps": [
        step("create_user", "user.create", {
            "displayName": "Priya Nair", "userPrincipalName": f"priya.nair@{DOMAIN}", "mailNickname": "priya.nair",
            "department": "Accounting", "usageLocation": "US"}, "write.reversible"),
        step("buy_seat", "license.purchase", {"skuId": SPB, "skuPartNumber": "SPB", "quantity": 1}, "spend"),
        step("add_accounting", "group.addMember", {"groupId": ACCOUNTING, "userId": NEW}, "write.reversible",
             ("create_user",)),
        step("add_finance_files", "group.addMember", {"groupId": FINANCE_FILES, "userId": NEW}, "write.reversible",
             ("create_user",)),
        step("assign_license_monday", "followup.schedule", {
            "kind": "license.assign", "runAt": "2026-10-12T10:00:00Z", "userId": NEW, "skuId": SPB},
             "none", ("create_user", "buy_seat")),
        step("tap", "tap.create", {"userId": NEW, "lifetimeInMinutes": 480}, "security.credential", ("create_user",)),
        step("send_link", "link.sendOneTime", {"to": f"tgarcia@{DOMAIN}", "userId": NEW, "tap": "$ref:tap.output.tap"},
             "none", ("tap",)),
    ],
}


def plan_with(*extra_steps: dict, drop: tuple[str, ...] = ()) -> Plan:
    data = copy.deepcopy(ONBOARD_PLAN)
    data["steps"] = [s for s in data["steps"] if s["id"] not in drop] + list(extra_steps)
    return Plan.model_validate(data)


@pytest.fixture
def transcript() -> Transcript:
    return marcus_transcript()


@pytest.fixture
def facts():
    return load_client_facts(DOMAIN)


@pytest.fixture
def playbooks():
    return load_playbooks(["user.onboard"], DOMAIN)


# ------------------------------------------------------------------ execution: a fake tenant behind respx

LINK_KEY = "oZ52dGBlFpEMoBqyaDWCIMI_ZhKuu6Tj2RCvSlfPIHE="  # a fixed Fernet key for tests


@pytest.fixture
def tenant():
    import respx
    from fake_tenant import FakeTenant

    t = FakeTenant(DOMAIN)
    t.add_user("marcus", "Marcus Webb", MARCUS)
    t.licenses[t.seat_holder] = {SPB}
    with respx.mock(assert_all_called=False) as router:
        router.route(host="graph.microsoft.com").mock(side_effect=t)
        yield t


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
async def graph(sleeps):
    import time

    from tech.graph.client import GraphClient

    async def tokens():
        return "tok", time.time() + 3600

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = GraphClient(tokens, sleep=fake_sleep)
    yield client
    await client.aclose()


@pytest.fixture
def store():
    from tech.core.store import Store

    return Store("sqlite://")


# ------------------------------------------------------------------ runner: a run as `tech run` leaves it

TAYLOR = f"tgarcia@{DOMAIN}"


@pytest.fixture
def rt(store, graph, facts, sleeps):
    from tech.runner import Runtime

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    return Runtime(store=store, graph=graph, facts=facts, domain=DOMAIN, link_key=LINK_KEY, sleep=fake_sleep,
                   closeout_model=drafts_model())


@pytest.fixture
def gated(rt, playbooks):
    """A run that went through triage, investigation and the real gate, as `tech run` leaves it."""
    from tech.core.models import Requester, Triage, TriageEntities
    from tech.core.store import RunState
    from tech.safety import gate

    def make(plan: Plan | None = None, *, requester: str = TAYLOR, approved: bool = True) -> int:
        plan = plan or plan_with()
        store = rt.store
        run_id = store.create_run(store.create_ticket("new hire Priya", requester).id).id
        triage = Triage(intents=["user.onboard"], entities=TriageEntities(),
                        requester=Requester(name="Taylor", email=requester))
        store.append_event(run_id, "triage", {"triage": triage.model_dump(mode="json"),
                                              "requester_approved": approved, "requester_scopes": {}})
        for state in (RunState.TRIAGED, RunState.INVESTIGATING):
            store.set_state(run_id, state)
        store.append_event(run_id, "finding", {"text": "Marcus is in Accounting", "t": 1.0})
        store.append_event(run_id, "plan", {"plan": plan.model_dump(mode="json", by_alias=True), "usage": {}})
        store.set_state(run_id, RunState.PLAN_PROPOSED)
        gate.check(store, run_id, plan, requester_approved=approved, intents=["user.onboard"], playbooks=playbooks,
                   facts=rt.facts, transcript=marcus_transcript())
        return run_id

    return make


@pytest.fixture
def executed(rt, gated, tenant):
    """A gated run whose plan has been executed against the fake tenant (no close-out): (run_id, plan)."""
    from tech.execution.executor import Execution

    async def make(plan: Plan | None = None) -> tuple[int, Plan]:
        plan = plan or plan_with()
        run_id = gated(plan)
        result = await Execution(rt.store, run_id, rt.graph, DOMAIN, link_key=LINK_KEY, sleep=rt.sleep).run(plan)
        assert result.ok, result.error
        return run_id, plan

    return make
