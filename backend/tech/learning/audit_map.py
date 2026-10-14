"""Entra directory audit entries -> catalogue actions (Build-W4 "audit-log learning", Build-Day20 Part 0).

A technician who fixed an escalated run in the Entra admin center leaves `directoryAudits` entries. This
module decides which entries count and what each one did, in catalogue terms. The shapes are the ones the
Day 20 spike recorded:

  - "Add member to group": targetResources = [the user (type User), the group (type Group)].
  - "Update user": targetResources[0] is the user; modifiedProperties has one entry per changed field, in
    PascalCase, with JSON-array values (`JobTitle: ["Lead Lot Attendant"] -> ["Lot Supervisor"]`), plus
    bookkeeping entries ("Included Updated Properties", "TargetId.UserType").
  - display names are null; IDs and UPNs are there.

Which entries count (`relevant`): one admin-center session made 11 entries for 2 changes. Kept only if a
person did it (initiatedBy.user; an app's entries, ours included, are not a technician's fix), it isn't a
read (operationType "Read"), it falls in the window, and it touches an object the run observed. Raw entries
can carry device tokens (the MFA service's "Update user"), so they never leave this module: only mapped
steps and short descriptions of the rest do.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from tech.core.models import ObservedStep
from tech.execution.catalogue import ACTIONS

GROUP_ACTIONS = {
    "Add member to group": "group.addMember",
    "Remove member from group": "group.removeMember",
    "Add owner to group": "group.addOwner",
}
# "Update user"'s modifiedProperties -> user.update's params; a field not here makes the entry unmapped.
USER_FIELDS = {"JobTitle": "jobTitle", "Department": "department", "DisplayName": "displayName",
               "UsageLocation": "usageLocation", "AccountEnabled": "accountEnabled"}
BOOKKEEPING = {"Included Updated Properties", "TargetId.UserType"}


@dataclass
class Unmapped:
    """An entry a technician made that we can't learn from, and why. No raw values: names and IDs only."""

    activity: str
    at: str
    by: str
    reason: str
    targets: list[str]

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def when(entry: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(entry["activityDateTime"].replace("Z", "+00:00"))


def initiator(entry: dict[str, Any]) -> str | None:
    """The person who made the change (their UPN), or None if an app did."""
    user = (entry.get("initiatedBy") or {}).get("user")
    return (user.get("userPrincipalName") or user.get("id")) if user else None


def relevant(entry: dict[str, Any], *, since: datetime, until: datetime, observed: set[str]) -> bool:
    targets = {str(t.get("id") or "").lower() for t in entry.get("targetResources") or []}
    return (initiator(entry) is not None and entry.get("operationType") != "Read"
            and since <= when(entry) <= until and bool(targets & observed))


def _target(entry: dict[str, Any], kind: str) -> dict[str, Any] | None:
    return next((t for t in entry.get("targetResources") or [] if t.get("type") == kind), None)


def _new_value(prop: dict[str, Any]) -> Any:
    """modifiedProperties values are JSON: an array for user fields (`["x"]`), a quoted string elsewhere."""
    value = json.loads(prop["newValue"]) if prop.get("newValue") else None
    return value[0] if isinstance(value, list) and len(value) == 1 else value


def map_entry(entry: dict[str, Any]) -> ObservedStep | Unmapped:
    name, by = entry.get("activityDisplayName", ""), initiator(entry) or "an app"
    targets = [t.get("id") for t in entry.get("targetResources") or [] if t.get("id")]

    def unmapped(reason: str) -> Unmapped:
        return Unmapped(activity=name, at=entry["activityDateTime"], by=by, reason=reason, targets=targets)

    user = _target(entry, "User")
    if name in GROUP_ACTIONS:
        group = _target(entry, "Group")
        if not (user and group):
            return unmapped("the entry doesn't name both the user and the group")
        action, params = GROUP_ACTIONS[name], {"groupId": group["id"], "userId": user["id"]}
    elif name == "Update user" and user:
        props = [p for p in user.get("modifiedProperties") or [] if p.get("displayName") not in BOOKKEEPING]
        if unknown := [p["displayName"] for p in props if p["displayName"] not in USER_FIELDS]:
            return unmapped(f"changes {', '.join(unknown)}, which user.update can't make")
        if not props:
            return unmapped("no field this product can change was updated")
        action = "user.update"
        params = {"userId": user["id"]} | {USER_FIELDS[p["displayName"]]: _new_value(p) for p in props}
    elif name == "Disable account" and user:
        action, params = "user.update", {"userId": user["id"], "accountEnabled": False}
    else:
        return unmapped("not in the catalogue: there is no action that makes this change")

    try:
        ACTIONS[action].params.model_validate(params)
    except ValidationError as exc:
        return unmapped(f"doesn't fit {action}: {exc.errors()[0]['msg']}")
    return ObservedStep(action=action, params=params, actor=f"technician:{by}".lower(),
                        rationale=f"{name} in the Entra admin center at {entry['activityDateTime']}")
