"""Per-action params models (Tech.MD §5, §7).

Param names follow Graph (camelCase); the snake_case spellings the model also produces (user_id,
run_at...) are accepted, because a lint retry costs ~10 s. Semantic mistakes are not tolerated:
unknown fields are rejected (e.g. a passwordProfile, since the playbooks forbid human-set passwords)
and a TAP can only be passed as a reference, never as a value.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from tech.core.models.base import ObjectId, ObjectIdOrRef, Params, TapRef, alias


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
    lifetimeInMinutes: int = alias("lifetimeInMinutes", "lifetime_in_minutes", default=480, ge=60, le=480,
                                   description="The tenant's TAP policy allows 60-480 minutes")
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
