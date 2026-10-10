"""Typed contracts between the agents and the code: Triage (Tech.MD §4.0), Plan (§5) and GateDecision (§8).

Plan steps are a discriminated union on `action`, so every action carries its own params model and the
model sees the exact shape it must produce. Param names follow Graph (camelCase); the snake_case
spellings the model also produces (user_id, run_at...) are accepted, because a lint retry costs ~10 s.
Semantic mistakes are not tolerated: unknown fields are rejected (e.g. a passwordProfile, since the
playbooks forbid human-set passwords) and a TAP can only be passed as a reference, never as a value.

ID fields take either an object ID observed during investigation (a GUID) or a reference to an earlier
step's output, written `$ref:<step id>.output.id`.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
GUID_RE = re.compile(GUID)
REF_RE = re.compile(r"^\$ref:([A-Za-z0-9_-]+)\.output\.([A-Za-z]+)$")

ObjectId = Annotated[str, Field(pattern=f"^{GUID}$", description="An object ID you observed in a tool result")]
ObjectIdOrRef = Annotated[str, Field(
    pattern=rf"^({GUID}|\$ref:[A-Za-z0-9_-]+\.output\.id)$",
    description="An object ID you observed in a tool result, or '$ref:<step id>.output.id' for an object an "
                "earlier step creates",
)]
TapRef = Annotated[str, Field(
    pattern=r"^\$ref:[A-Za-z0-9_-]+\.output\.tap$",
    description="'$ref:<tap.create step id>.output.tap'. The TAP value itself is never in a plan",
)]

Intent = Literal["user.onboard", "user.offboard", "mfa.reset", "file.restore_version", "unknown"]
RiskClass = Literal[
    "read", "write.reversible", "access.revoke", "data.access", "security.credential",
    "spend", "destructive.soft", "none",
]


def parse_ref(value: Any) -> tuple[str, str] | None:
    """`$ref:s1.output.id` -> ("s1", "id"); anything else -> None."""
    if isinstance(value, str) and (m := REF_RE.match(value)):
        return m.group(1), m.group(2)
    return None


def alias(*names: str, **kw: Any) -> Any:
    """Accept the Graph name first, then the spellings the model also uses; serialize as the Graph name."""
    return Field(validation_alias=AliasChoices(*names), serialization_alias=names[0], **kw)


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)


# ------------------------------------------------------------------ triage (§4.0)


class NewUser(BaseModel):
    display_name: str
    department: str | None = None
    job_title: str | None = None
    start_date: date | None = Field(None, description="Resolved to a date, e.g. 'monday' -> the coming Monday")


class TriageEntities(BaseModel):
    new_user: NewUser | None = None
    peer_user_hint: str | None = Field(None, description="The coworker the requester said to copy, as written")
    subject_user_hint: str | None = Field(None, description="The leaver or locked-out user, as written")
    apps: list[str] = []


class Requester(BaseModel):
    name: str
    email: str


class Triage(BaseModel):
    intents: list[Intent]
    entities: TriageEntities
    requester: Requester
    out_of_scope_asks: list[str] = Field(default=[], description="Anything asked for beyond the ticket type, quoted")
    missing_info: list[str] = []


# ------------------------------------------------------------------ step params (onboarding actions)


class UserCreateParams(Params):
    displayName: str = alias("displayName", "display_name")
    givenName: str | None = alias("givenName", "given_name", default=None)
    surname: str | None = None
    userPrincipalName: str = alias("userPrincipalName", "user_principal_name", pattern=r"^[^@\s]+@[^@\s]+$")
    mailNickname: str = alias("mailNickname", "mail_nickname")
    department: str | None = None
    jobTitle: str | None = alias("jobTitle", "job_title", default=None)
    usageLocation: str = alias("usageLocation", "usage_location", pattern=r"^[A-Z]{2}$",
                               description="Two-letter country code, from client facts")
    accountEnabled: bool = alias("accountEnabled", "account_enabled", default=True)

    @model_validator(mode="after")
    def nickname_matches_upn(self) -> UserCreateParams:
        if self.userPrincipalName.split("@")[0].lower() != self.mailNickname.lower():
            raise ValueError("mailNickname must equal the part of userPrincipalName before the @")
        return self


class UserUpdateParams(Params):
    userId: ObjectIdOrRef = alias("userId", "user_id")
    usageLocation: str | None = alias("usageLocation", "usage_location", default=None, pattern=r"^[A-Z]{2}$")
    accountEnabled: bool | None = alias("accountEnabled", "account_enabled", default=None)
    department: str | None = None
    jobTitle: str | None = alias("jobTitle", "job_title", default=None)
    displayName: str | None = alias("displayName", "display_name", default=None)

    @model_validator(mode="after")
    def changes_something(self) -> UserUpdateParams:
        if not self.model_dump(exclude={"userId"}, exclude_none=True):
            raise ValueError("user.update must change at least one property")
        return self


class LicensePurchaseParams(Params):
    skuId: ObjectId = alias("skuId", "sku_id")
    skuPartNumber: str = alias("skuPartNumber", "sku_part_number")
    quantity: int = Field(1, ge=1, le=10)


class LicenseAssignParams(Params):
    userId: ObjectIdOrRef = alias("userId", "user_id")
    skuId: ObjectId = alias("skuId", "sku_id")
    skuPartNumber: str | None = alias("skuPartNumber", "sku_part_number", default=None)


class GroupAddMemberParams(Params):
    groupId: ObjectId = alias("groupId", "group_id")
    userId: ObjectIdOrRef = alias("userId", "user_id")
    groupName: str | None = alias("groupName", "group_name", default=None,
                                  description="Display name, for the approval card only")


class TapCreateParams(Params):
    userId: ObjectIdOrRef = alias("userId", "user_id")
    startDateTime: datetime | None = alias("startDateTime", "start_date_time", default=None)
    lifetimeInMinutes: int = alias("lifetimeInMinutes", "lifetime_in_minutes", default=60, ge=10, le=43200)
    isUsableOnce: bool = alias("isUsableOnce", "is_usable_once", default=False)


class FollowupParams(Params):
    kind: Literal["license.assign", "license.review", "mfa.verifyNewMethod"] = alias(
        "kind", "type", description="What runs later")
    runAt: datetime = alias("runAt", "run_at", "when")
    userId: ObjectIdOrRef = alias("userId", "user_id")
    skuId: ObjectId | None = alias("skuId", "sku_id", default=None)
    skuPartNumber: str | None = alias("skuPartNumber", "sku_part_number", default=None)

    @model_validator(mode="after")
    def license_needs_sku(self) -> FollowupParams:
        if self.kind == "license.assign" and self.skuId is None:
            raise ValueError("a license.assign follow-up needs skuId")
        return self


LinkContent = Literal["email_access_steps", "outlook_mobile_app_tip", "file_share_note"]


class LinkSendParams(Params):
    to: str = Field(pattern=r"^[^@\s]+@[^@\s]+$")
    userId: ObjectIdOrRef = alias("userId", "user_id", "subject_user", description="The user the credentials are for")
    tap: TapRef | None = Field(None, description="Omit to use this user's tap.create step")
    includes: list[LinkContent] = []
    note: str | None = Field(None, description="Plain text shown with the link. Never put a TAP or password here")


# ------------------------------------------------------------------ plan (§5)


class StepBase(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    depends_on: list[str] = []
    rationale: str
    expected: dict[str, Any] = Field(description="What a verify read should show afterwards")
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


Step = Annotated[
    UserCreateStep | UserUpdateStep | LicensePurchaseStep | LicenseAssignStep | GroupAddMemberStep
    | TapCreateStep | FollowupStep | LinkSendStep,
    Field(discriminator="action"),
]
STEP_TYPES: dict[str, type[StepBase]] = {
    t.model_fields["action"].annotation.__args__[0]: t  # type: ignore[union-attr]
    for t in (UserCreateStep, UserUpdateStep, LicensePurchaseStep, LicenseAssignStep, GroupAddMemberStep,
              TapCreateStep, FollowupStep, LinkSendStep)
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


# ------------------------------------------------------------------ gate (§8)


class GateDecision(BaseModel):
    step_id: str
    action: str
    decision: Literal["auto", "approve", "block"]
    rule_id: str
    reason: str
    risk_class: RiskClass | None = Field(None, description="From the catalogue, never from the plan")
