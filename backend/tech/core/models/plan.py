"""Plan (Tech.MD §5): the investigator's typed output.

Plan steps are a discriminated union on `action`, so every action carries its own params model and the
model sees the exact shape it must produce.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from tech.core.models.base import Intent, RiskClass
from tech.core.models.params import (
    AuthMethodDeleteParams,
    FollowupParams,
    GroupAddMemberParams,
    GroupAddOwnerParams,
    GroupRemoveMemberParams,
    LicenseAssignParams,
    LicensePurchaseParams,
    LinkSendParams,
    MailboxAutoReplyParams,
    MailboxForwardRuleParams,
    SessionsRevokeParams,
    TapCreateParams,
    UserCreateParams,
    UserUpdateParams,
)


class StepBase(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    depends_on: list[str] = []
    rationale: str
    expected: dict[str, Any] = Field(description=(
        "What a verify read should show afterwards, as literal values (e.g. {\"accountEnabled\": false}). "
        "Leave out what you can't know in advance, such as timestamps"))
    risk_class: RiskClass = Field(description="Your view; the gate recomputes it from the catalogue")


class UserCreateStep(StepBase):
    action: Literal["user.create"]
    params: UserCreateParams


class UserUpdateStep(StepBase):
    action: Literal["user.update"]
    params: UserUpdateParams


class LicensePurchaseStep(StepBase):
    action: Literal["license.purchase"]
    params: LicensePurchaseParams


class LicenseAssignStep(StepBase):
    action: Literal["license.assign"]
    params: LicenseAssignParams


class GroupAddMemberStep(StepBase):
    action: Literal["group.addMember"]
    params: GroupAddMemberParams


class TapCreateStep(StepBase):
    action: Literal["tap.create"]
    params: TapCreateParams


class FollowupStep(StepBase):
    action: Literal["followup.schedule"]
    params: FollowupParams


class LinkSendStep(StepBase):
    action: Literal["link.sendOneTime"]
    params: LinkSendParams


class SessionsRevokeStep(StepBase):
    action: Literal["sessions.revoke"]
    params: SessionsRevokeParams


class MailboxAutoReplyStep(StepBase):
    action: Literal["mailbox.autoReply"]
    params: MailboxAutoReplyParams


class MailboxForwardRuleStep(StepBase):
    action: Literal["mailbox.forwardRule"]
    params: MailboxForwardRuleParams


class GroupRemoveMemberStep(StepBase):
    action: Literal["group.removeMember"]
    params: GroupRemoveMemberParams


class GroupAddOwnerStep(StepBase):
    action: Literal["group.addOwner"]
    params: GroupAddOwnerParams


class AuthMethodDeleteStep(StepBase):
    action: Literal["authMethod.delete"]
    params: AuthMethodDeleteParams


Step = Annotated[
    UserCreateStep | UserUpdateStep | LicensePurchaseStep | LicenseAssignStep | GroupAddMemberStep
    | TapCreateStep | FollowupStep | LinkSendStep | SessionsRevokeStep | MailboxAutoReplyStep
    | MailboxForwardRuleStep | GroupRemoveMemberStep | GroupAddOwnerStep | AuthMethodDeleteStep,
    Field(discriminator="action"),
]
STEP_TYPES: dict[str, type[StepBase]] = {
    t.model_fields["action"].annotation.__args__[0]: t  # type: ignore[union-attr]
    for t in (UserCreateStep, UserUpdateStep, LicensePurchaseStep, LicenseAssignStep, GroupAddMemberStep,
              TapCreateStep, FollowupStep, LinkSendStep, SessionsRevokeStep, MailboxAutoReplyStep,
              MailboxForwardRuleStep, GroupRemoveMemberStep, GroupAddOwnerStep, AuthMethodDeleteStep)
}


class TechnicianItem(BaseModel):
    item: str
    reason: str


class Confirmation(BaseModel):
    key: Literal["upn_format", "license_purchase", "start_date", "other"] = Field(
        "other", description="Which plan fact the approver's answer overrides",
    )
    question: str
    proposed: str
    evidence: str


class Plan(BaseModel):
    intent: Intent
    summary: str
    assumptions: list[str] = []
    steps: list[Step] = Field(description="Empty when escalating")
    needs_technician: list[TechnicianItem] = []
    confirm_on_approval: list[Confirmation] = []
    questions_for_requester: list[str] = []
    out_of_scope_requests: list[str] = Field(
        default=[], description="Anything asked for that this job must not do, quoted")
    escalate: bool = False
    escalation_reason: str | None = None

    def step(self, step_id: str) -> StepBase:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)
