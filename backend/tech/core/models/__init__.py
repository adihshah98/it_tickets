"""Typed contracts between the agents and the code: Triage (Tech.MD §4.0), Plan (§5) and GateDecision (§8).

Split into submodules in Week 3 (Build-W3.md) when offboarding and MFA roughly doubled the step union:
`base` holds the shared primitives (IDs, references, the intent slug, the RiskClass literal), then `triage`,
`params`, `plan`, `gate` and (Week 4) `learning`. This package re-exports the full public surface, so every
`from tech.core.models import ...` keeps working unchanged.
"""

from __future__ import annotations

from tech.core.models.base import (
    GUID,
    GUID_RE,
    INTENT_RE,
    REF_RE,
    DriveId,
    DriveItemId,
    Intent,
    IntentSlug,
    ObjectId,
    ObjectIdOrRef,
    Params,
    RiskClass,
    TapRef,
    VersionId,
    alias,
    parse_ref,
)
from tech.core.models.gate import GateDecision
from tech.core.models.learning import DraftSource, ObservedRead, ObservedStep, SourceKind
from tech.core.models.params import (
    AuthMethodDeleteParams,
    AuthMethodType,
    FileRestoreVersionParams,
    FollowupParams,
    GroupAddMemberParams,
    GroupAddOwnerParams,
    GroupRemoveMemberParams,
    LicenseAssignParams,
    LicensePurchaseParams,
    LinkContent,
    LinkSendParams,
    MailboxAutoReplyParams,
    MailboxForwardRuleParams,
    SessionsRevokeParams,
    TapCreateParams,
    UserCreateParams,
    UserUpdateParams,
)
from tech.core.models.plan import (
    STEP_TYPES,
    AuthMethodDeleteStep,
    Confirmation,
    FileRestoreVersionStep,
    FollowupStep,
    GroupAddMemberStep,
    GroupAddOwnerStep,
    GroupRemoveMemberStep,
    LicenseAssignStep,
    LicensePurchaseStep,
    LinkSendStep,
    MailboxAutoReplyStep,
    MailboxForwardRuleStep,
    Plan,
    SessionsRevokeStep,
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
    "GUID", "GUID_RE", "INTENT_RE", "REF_RE", "Intent", "IntentSlug", "RiskClass", "ObjectId", "ObjectIdOrRef",
    "TapRef", "DriveId", "DriveItemId", "VersionId", "Params", "alias", "parse_ref",
    # triage
    "NewUser", "TriageEntities", "Requester", "Triage",
    # params
    "UserCreateParams", "UserUpdateParams", "LicensePurchaseParams", "LicenseAssignParams",
    "GroupAddMemberParams", "TapCreateParams", "FollowupParams", "LinkSendParams", "LinkContent",
    "SessionsRevokeParams", "MailboxAutoReplyParams", "MailboxForwardRuleParams",
    "GroupRemoveMemberParams", "GroupAddOwnerParams", "AuthMethodDeleteParams", "AuthMethodType",
    "FileRestoreVersionParams",
    # plan
    "StepBase", "UserCreateStep", "UserUpdateStep", "LicensePurchaseStep", "LicenseAssignStep",
    "GroupAddMemberStep", "TapCreateStep", "FollowupStep", "LinkSendStep", "Step", "STEP_TYPES",
    "TechnicianItem", "Confirmation", "Plan",
    "SessionsRevokeStep", "MailboxAutoReplyStep", "MailboxForwardRuleStep",
    "GroupRemoveMemberStep", "GroupAddOwnerStep", "AuthMethodDeleteStep", "FileRestoreVersionStep",
    # gate
    "GateDecision",
    # learning
    "DraftSource", "ObservedRead", "ObservedStep", "SourceKind",
]
