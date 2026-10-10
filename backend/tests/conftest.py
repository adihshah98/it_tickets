"""Shared fixtures for the linter and gate: Marcus's investigation and the onboarding plan it led to
(the plan is the one Opus proposed in the Day 3 live run)."""

import copy

import pytest

from tech.core.models import Plan
from tech.graph.reads import Transcript
from tech.playbooks import load_client_facts, load_playbooks

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
    return t


def step(sid: str, action: str, params: dict, risk: str, depends_on: tuple[str, ...] = ()) -> dict:
    return {"id": sid, "action": action, "params": params, "depends_on": list(depends_on),
            "rationale": "test", "expected": {}, "risk_class": risk}


NEW = "$ref:create_user.output.id"

ONBOARD_PLAN = {
    "intent": "user.onboard",
    "summary": "Onboard Priya Nair like Marcus Webb",
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
        step("tap", "tap.create", {"userId": NEW, "lifetimeInMinutes": 720}, "security.credential", ("create_user",)),
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
