"""Write-action catalogue (Tech.MD §7): what each action may take, its risk class, how it's verified and undone.

The gate takes risk classes from here, never from the plan (the plan's risk_class is only compared, for
the audit log). Each action's `handler` does the work (graph/writes.py, mocks.py, links.py, scheduler.py);
`verify` and `compensate` describe it for people, and `compensate` is what an escalation offers to roll back.
Only the onboarding actions are here; offboarding and MFA actions arrive in Week 3.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from tech.core.models import (
    FollowupParams,
    GroupAddMemberParams,
    LicenseAssignParams,
    LicensePurchaseParams,
    LinkSendParams,
    Params,
    RiskClass,
    TapCreateParams,
    UserCreateParams,
    UserUpdateParams,
)
from tech.execution.links import LinkSend
from tech.execution.mocks import LicensePurchase
from tech.execution.scheduler import FollowupSchedule
from tech.graph.writes import HANDLERS, Handler


@dataclass(frozen=True)
class ActionSpec:
    name: str
    params: type[Params]
    risk_class: RiskClass
    implementation: str  # Graph call, or "mock" / "service" for our own
    verify: str
    compensate: str | None
    # For actions whose class depends on the params (blocking sign-in revokes access; renaming doesn't).
    risk_for: Callable[[Params], RiskClass] | None = field(default=None, compare=False)
    handler: Handler | None = field(default=None, compare=False)

    def risk(self, params: Params) -> RiskClass:
        return self.risk_for(params) if self.risk_for else self.risk_class


def _user_update_risk(params: Params) -> RiskClass:
    assert isinstance(params, UserUpdateParams)
    return "access.revoke" if params.accountEnabled is False else "write.reversible"


# What each follow-up kind does when it runs: a catalogue action, or None for a reminder that changes nothing.
FOLLOWUP_ACTIONS: dict[str, str | None] = {
    "license.assign": "license.assign",
    "license.review": None,         # a technician reviews the leaver's license after 30 days
    "mfa.verifyNewMethod": None,    # a technician checks the user registered a new method
}


def _followup_risk(params: Params) -> RiskClass:
    """A follow-up is as risky as what it will do later, since nothing gates it again when it runs."""
    assert isinstance(params, FollowupParams)
    action = FOLLOWUP_ACTIONS[params.kind]
    return ACTIONS[action].risk_class if action else "none"


ACTIONS: dict[str, ActionSpec] = {spec.name: spec for spec in [
    ActionSpec(
        "user.create", UserCreateParams, "write.reversible",
        "POST /users", "GET /users/{id}: exists, usageLocation and the created properties match",
        "DELETE /users/{id} (soft delete, restorable for 30 days)", handler=HANDLERS["user.create"],
    ),
    ActionSpec(
        "user.update", UserUpdateParams, "write.reversible",
        "PATCH /users/{userId}", "GET /users/{userId}: changed properties match",
        "PATCH /users/{userId} back to the snapshot", risk_for=_user_update_risk,
        handler=HANDLERS["user.update"],
    ),
    ActionSpec(
        "license.purchase", LicensePurchaseParams, "spend",
        "mock (Pax8): frees the seat held by seat-holder", "GET /subscribedSkus: available seats for skuId >= 1",
        "mock: return the seat to seat-holder", handler=LicensePurchase(),
    ),
    ActionSpec(
        "license.assign", LicenseAssignParams, "write.reversible",
        "POST /users/{userId}/assignLicense", "GET /users/{userId}/licenseDetails: has skuId",
        "POST /users/{userId}/assignLicense with removeLicenses=[skuId]", handler=HANDLERS["license.assign"],
    ),
    ActionSpec(
        "group.addMember", GroupAddMemberParams, "write.reversible",
        "POST /groups/{groupId}/members/$ref", "GET /users/{userId}/memberOf: contains groupId",
        "DELETE /groups/{groupId}/members/{userId}/$ref", handler=HANDLERS["group.addMember"],
    ),
    ActionSpec(
        "tap.create", TapCreateParams, "security.credential",
        "POST /users/{userId}/authentication/temporaryAccessPassMethods",
        "GET /users/{userId}/authentication/methods: a temporaryAccessPass method exists",
        "DELETE /users/{userId}/authentication/temporaryAccessPassMethods/{id}", handler=HANDLERS["tap.create"],
    ),
    ActionSpec(
        "link.sendOneTime", LinkSendParams, "none",
        "service: one-time link (TAP encrypted, view once, expires)", "link record exists and is unviewed",
        "expire the link", handler=LinkSend(),
    ),
    ActionSpec(
        "followup.schedule", FollowupParams, "none",
        "service: scheduler", "followup record exists with runAt",
        "cancel the follow-up", risk_for=_followup_risk, handler=FollowupSchedule(),
    ),
]}
