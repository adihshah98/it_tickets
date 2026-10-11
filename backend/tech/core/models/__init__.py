"""Typed contracts between the agents and the code: Triage (Tech.MD §4.0), Plan (§5) and GateDecision (§8).

Split into submodules in Week 3 (Build-W3.md) when offboarding and MFA roughly doubled the step union:
`base` holds the shared primitives (IDs, references, the Intent/RiskClass literals), then `triage`,
`params`, `plan` and `gate`. This package re-exports the full public surface, so every
`from tech.core.models import ...` keeps working unchanged.
"""

from __future__ import annotations

from tech.core.models.base import (
    GUID,
    GUID_RE,
    REF_RE,
    Intent,
    ObjectId,
    ObjectIdOrRef,
    Params,
    RiskClass,
    TapRef,
    alias,
    parse_ref,
)
from tech.core.models.gate import GateDecision
from tech.core.models.params import (
    FollowupParams,
    GroupAddMemberParams,
    LicenseAssignParams,
    LicensePurchaseParams,
    LinkContent,
    LinkSendParams,
    TapCreateParams,
    UserCreateParams,
    UserUpdateParams,
)
from tech.core.models.plan import (
    STEP_TYPES,
    Confirmation,
    FollowupStep,
    GroupAddMemberStep,
    LicenseAssignStep,
    LicensePurchaseStep,
    LinkSendStep,
    Plan,
    Step,
    StepBase,
    TapCreateStep,
    TechnicianItem,
    UserCreateStep,
    UserUpdateStep,
)
from tech.core.models.triage import NewUser, Requester, Triage, TriageEntities

__all__ = [
    # base
    "GUID", "GUID_RE", "REF_RE", "Intent", "RiskClass", "ObjectId", "ObjectIdOrRef", "TapRef",
    "Params", "alias", "parse_ref",
    # triage
    "NewUser", "TriageEntities", "Requester", "Triage",
    # params
    "UserCreateParams", "UserUpdateParams", "LicensePurchaseParams", "LicenseAssignParams",
    "GroupAddMemberParams", "TapCreateParams", "FollowupParams", "LinkSendParams", "LinkContent",
    # plan
    "StepBase", "UserCreateStep", "UserUpdateStep", "LicensePurchaseStep", "LicenseAssignStep",
    "GroupAddMemberStep", "TapCreateStep", "FollowupStep", "LinkSendStep", "Step", "STEP_TYPES",
    "TechnicianItem", "Confirmation", "Plan",
    # gate
    "GateDecision",
]
