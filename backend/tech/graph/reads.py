"""Read tools for the investigator (Tech.MD §7), ported from the spike.

Each tool is an async function taking the Graph client and its arguments and returning trimmed dicts
(only what the model needs, so prompts stay small). Call them through `run_read`, which records the
call and every object ID (GUID) in the result to the run's Transcript. The linter's "grounded" rule
accepts an ID in a plan only if it is in `Transcript.observed_ids`.

Graph quirks handled here:
  - signInActivity: 204 when read on /users/{id}, 400 on /users/{upn}; only a /users?$filter=id query works.
  - Group kind comes from the groupTypes, mail and security flags (`classify`).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from tech.core.models import GUID_RE
from tech.graph.client import GraphClient, GraphError, odata_str

USER_FIELDS = "id,displayName,userPrincipalName,jobTitle,department,accountEnabled,createdDateTime,usageLocation"
SIGN_IN_ACTIVITY_FIELDS = ("lastSignInDateTime", "lastNonInteractiveSignInDateTime", "lastSuccessfulSignInDateTime")


# ------------------------------------------------------------------ transcript


@dataclass
class ToolCall:
    tool: str
    args: dict[str, Any]
    error: str | None = None


OBJECT_FIELDS = ("displayName", "kind", "roleAssignable", "userPrincipalName", "skuPartNumber")


@dataclass
class Transcript:
    """What the investigator looked at in one run: the calls, every object ID it saw, and what it
    saw about each object (name, group kind...), so the linter and gate can judge a step's target."""

    calls: list[ToolCall] = field(default_factory=list)
    observed_ids: set[str] = field(default_factory=set)
    objects: dict[str, dict[str, Any]] = field(default_factory=dict)

    def record(self, tool: str, args: dict[str, Any], result: Any = None, error: str | None = None) -> None:
        self.calls.append(ToolCall(tool, args, error))
        if result is not None:
            self.observed_ids |= {m.lower() for m in GUID_RE.findall(json.dumps(result, default=str))}
            self._collect(result)

    def _collect(self, value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                self._collect(item)
        elif isinstance(value, dict):
            object_id = value.get("id") or value.get("skuId")
            if isinstance(object_id, str) and GUID_RE.fullmatch(object_id):
                seen = {k: value[k] for k in OBJECT_FIELDS if value.get(k) is not None}
                self.objects.setdefault(object_id.lower(), {}).update(seen)
            for item in value.values():
                self._collect(item)

    def observed(self, object_id: str) -> bool:
        return object_id.lower() in self.observed_ids

    def object(self, object_id: str) -> dict[str, Any]:
        """What was seen about an object; {} if nothing (or only its ID inside some text)."""
        return self.objects.get(object_id.lower(), {})

    def called(self, tool: str) -> bool:
        return any(c.tool == tool and c.error is None for c in self.calls)


# ------------------------------------------------------------------ tools


def classify(group: dict) -> str:
    if "Unified" in (group.get("groupTypes") or []):
        return "m365"
    if group.get("securityEnabled") and not group.get("mailEnabled"):
        return "security"
    if group.get("securityEnabled") and group.get("mailEnabled"):
        return "mail_security"
    return "distribution_list"


async def users_search(graph: GraphClient, name: str) -> list[dict]:
    """Find users whose display name, surname or UPN starts with `name`. Includes disabled users."""
    n = odata_str(name)
    f = f"startswith(displayName,'{n}') or startswith(surname,'{n}') or startswith(userPrincipalName,'{n}')"
    return [u async for u in graph.list("/users", {"$filter": f, "$select": USER_FIELDS})]


async def users_list(graph: GraphClient) -> list[dict]:
    """List every user, newest first (use to infer the email/UPN format of recent hires)."""
    users = [u async for u in graph.list("/users", {"$select": USER_FIELDS, "$top": "999"})]
    return sorted(users, key=lambda u: u.get("createdDateTime") or "", reverse=True)


async def users_get(graph: GraphClient, user_id: str) -> dict:
    """Profile, manager and signInActivity (last sign-in) for one user id."""

    async def sign_in_activity() -> dict | None:
        """None when it can't be read; all-null fields when no sign-in is recorded (Graph omits the property)."""
        try:
            row = await graph.first("/users", {"$filter": f"id eq '{odata_str(user_id)}'",
                                               "$select": "id,signInActivity"})
        except GraphError:  # needs Entra ID P1 and AuditLog.Read.All
            return None
        activity = (row or {}).get("signInActivity") or {}
        return {k: activity.get(k) for k in SIGN_IN_ACTIVITY_FIELDS}

    async def manager() -> dict | None:
        try:
            m = await graph.get(f"/users/{user_id}/manager", params={"$select": "id,displayName,userPrincipalName"})
        except GraphError as exc:
            if exc.status == 404:
                return None
            raise
        return {k: m.get(k) for k in ("id", "displayName", "userPrincipalName")}

    user, activity, mgr = await asyncio.gather(
        graph.get(f"/users/{user_id}", params={"$select": USER_FIELDS}), sign_in_activity(), manager(),
    )
    return user | {"signInActivity": activity, "manager": mgr}


async def users_member_of(graph: GraphClient, user_id: str) -> list[dict]:
    """A user's groups, each classified as m365 | security | mail_security | distribution_list.
    roleAssignable: the group can hold directory roles (privileged)."""
    params = {"$select": "id,displayName,groupTypes,mailEnabled,securityEnabled,mail,isAssignableToRole"}
    return [
        {"id": g["id"], "displayName": g.get("displayName"), "kind": classify(g), "mail": g.get("mail"),
         "roleAssignable": bool(g.get("isAssignableToRole"))}
        async for g in graph.list(f"/users/{user_id}/memberOf", params)
        if g.get("@odata.type") == "#microsoft.graph.group"
    ]


async def users_owned_objects(graph: GraphClient, user_id: str) -> list[dict]:
    """Groups and apps a user owns."""
    return [{"id": o["id"], "displayName": o.get("displayName"), "type": o.get("@odata.type")}
            async for o in graph.list(f"/users/{user_id}/ownedObjects")]


async def users_license_details(graph: GraphClient, user_id: str) -> list[dict]:
    """A user's assigned licenses (skuId and skuPartNumber)."""
    return [{"skuId": d["skuId"], "skuPartNumber": d["skuPartNumber"]}
            async for d in graph.list(f"/users/{user_id}/licenseDetails")]


async def subscribed_skus(graph: GraphClient) -> list[dict]:
    """Tenant license pool: enabled, consumed and available seats per SKU."""
    return [{"skuId": s["skuId"], "skuPartNumber": s["skuPartNumber"], "enabled": s["prepaidUnits"]["enabled"],
             "consumed": s["consumedUnits"], "available": s["prepaidUnits"]["enabled"] - s["consumedUnits"]}
            async for s in graph.list("/subscribedSkus")]


async def sign_ins(graph: GraphClient, user_id: str, top: int = 15) -> list[dict]:
    """Recent interactive sign-ins for a user: time, app, result code, location, client."""
    page = await graph.get("/auditLogs/signIns", params={
        "$filter": f"userId eq '{odata_str(user_id)}'", "$top": str(max(1, min(top, 50))),
    })
    return [{"time": r["createdDateTime"], "app": r.get("appDisplayName"), "errorCode": r["status"]["errorCode"],
             "failureReason": r["status"].get("failureReason"),
             "country": (r.get("location") or {}).get("countryOrRegion"),
             "city": (r.get("location") or {}).get("city"), "client": r.get("clientAppUsed"), "ip": r.get("ipAddress")}
            for r in page.get("value", [])]


async def auth_methods(graph: GraphClient, user_id: str) -> list[dict]:
    """A user's registered authentication methods (type, device name). Never contains secrets."""
    return [{"id": m["id"], "type": m["@odata.type"].split(".")[-1], "displayName": m.get("displayName")}
            async for m in graph.list(f"/users/{user_id}/authentication/methods")]


async def risky_user(graph: GraphClient, user_id: str) -> dict:
    """Identity Protection's risk verdict on a user (best-effort: with Entra ID P1 the level and detail
    read 'hidden', so reason over signIns.list too). risky=None means it couldn't be checked, not 'clean'."""
    try:
        r = await graph.get(f"/identityProtection/riskyUsers/{user_id}")
    except GraphError as exc:
        if exc.status == 404:  # Identity Protection has never flagged this user
            return {"risky": False}
        if exc.status == 403:  # no permission or licence: unknown, which must not read as clean
            return {"risky": None, "detail": f"risk data not available ({exc.code})"}
        raise
    return {"risky": r.get("riskState") not in (None, "none", "dismissed", "remediated"),
            "riskLevel": r.get("riskLevel"), "riskState": r.get("riskState"), "riskDetail": r.get("riskDetail"),
            "riskLastUpdatedDateTime": r.get("riskLastUpdatedDateTime")}


async def mailbox_settings(graph: GraphClient, user_id: str) -> dict:
    """A user's mailbox settings: whether an auto-reply is on, and its messages (for offboarding)."""
    try:
        settings = await graph.get(f"/users/{user_id}/mailboxSettings")
    except GraphError as exc:
        if exc.status == 404:  # no Exchange mailbox (e.g. an unlicensed or P1-only user)
            return {"mailbox": False}
        raise
    auto = settings.get("automaticRepliesSetting") or {}
    return {"mailbox": True, "autoReplyStatus": auto.get("status"),
            "externalReplyMessage": auto.get("externalReplyMessage")}


async def message_rules(graph: GraphClient, user_id: str) -> list[dict]:
    """A user's inbox rules, with their forwarding targets (an existing external forward is a red flag)."""
    rules = []
    try:
        async for rule in graph.list(f"/users/{user_id}/mailFolders/inbox/messageRules"):
            actions = rule.get("actions") or {}
            forwards = [t.get("emailAddress", {}).get("address")
                        for t in (actions.get("forwardTo") or []) + (actions.get("redirectTo") or [])]
            rules.append({"id": rule["id"], "displayName": rule.get("displayName"),
                          "enabled": rule.get("isEnabled"), "forwardsTo": [f for f in forwards if f]})
    except GraphError as exc:
        if exc.status == 404:
            return []
        raise
    return rules


async def files_search(graph: GraphClient, query: str) -> list[dict]:
    """Search the document libraries of every Microsoft 365 group (team sites) for files."""
    groups = [g async for g in graph.list("/groups", {"$filter": "groupTypes/any(c:c eq 'Unified')",
                                                      "$select": "id,displayName"})]

    async def search(group: dict) -> list[dict]:
        try:
            drive = await graph.get(f"/groups/{group['id']}/drive", params={"$select": "id"})
            page = await graph.get(f"/drives/{drive['id']}/root/search(q='{odata_str(query)}')")
        except GraphError:  # a group whose site was never provisioned
            return []
        return [{"site": group["displayName"], "drive_id": drive["id"], "item_id": it["id"], "name": it["name"],
                 "lastModified": it["lastModifiedDateTime"]} for it in page.get("value", [])]

    return [hit for hits in await asyncio.gather(*map(search, groups)) for hit in hits]


async def file_versions(graph: GraphClient, drive_id: str, item_id: str) -> list[dict]:
    """Version history of a file: version id, time, size, who saved it (newest first)."""

    def who(v: dict) -> str | None:
        by = v.get("lastModifiedBy") or {}
        return (by.get("user") or by.get("application") or {}).get("displayName")

    return [{"id": v["id"], "time": v["lastModifiedDateTime"], "size": v["size"], "by": who(v)}
            async for v in graph.list(f"/drives/{drive_id}/items/{item_id}/versions")]


# ------------------------------------------------------------------ registry

ReadFn = Callable[..., Awaitable[Any]]

READS: dict[str, ReadFn] = {
    "users.search": users_search,
    "users.list": users_list,
    "users.get": users_get,
    "users.memberOf": users_member_of,
    "users.ownedObjects": users_owned_objects,
    "users.licenseDetails": users_license_details,
    "subscribedSkus.list": subscribed_skus,
    "signIns.list": sign_ins,
    "authMethods.list": auth_methods,
    "riskyUsers.get": risky_user,
    "mailboxSettings.get": mailbox_settings,
    "messageRules.list": message_rules,
    "files.search": files_search,
    "files.versions": file_versions,
}


# ------------------------------------------------------------------ the MFA demo's clean sign-in history
#
# On the demo tenant Dennis's account and Authenticator are days old and his sign-ins come from wherever the
# presenter's phone is, so the investigator rightly reads them as a takeover (spike/results.md, finding 1).
# For the happy path only, MFA_DEMO_CLEAN_USER names one user whose signIns.list is replaced by an
# ordinary history: one city, one IP, daily sign-ins since the account was created, and today's MFA
# failures from the same place (he lost his phone). Everything else, his auth methods included, stays real,
# and every other user (the suspicious and service-account cases) reads real data.

DEMO_CITY = {"city": "Sacramento", "country": "US", "ip": "98.42.17.203"}
DEMO_APPS = ("Microsoft Teams", "Office 365 Exchange Online", "Microsoft Office")


def _sign_in(when: datetime, app: str, error: int = 0, reason: str | None = None) -> dict:
    return {"time": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "app": app, "errorCode": error, "failureReason": reason,
            "country": DEMO_CITY["country"], "city": DEMO_CITY["city"], "client": "Mobile Apps and Desktop clients",
            "ip": DEMO_CITY["ip"]}


async def demo_sign_ins(graph: GraphClient, user_id: str, top: int = 15, now: datetime | None = None) -> list[dict]:
    """A clean 14-day history, never starting before the account existed (that would contradict users.get)."""
    now = now or datetime.now(UTC)
    user = await graph.get(f"/users/{user_id}", params={"$select": "id,createdDateTime"})
    created = datetime.fromisoformat(user["createdDateTime"]) if user.get("createdDateTime") else now - timedelta(30)
    lost = "Authentication failed during strong authentication request."
    rows = [_sign_in(now - timedelta(minutes=95), "Microsoft Teams", 500121, lost),
            _sign_in(now - timedelta(minutes=110), "Office 365 Exchange Online", 500121, lost)]
    for day in range(1, 14):
        when = (now - timedelta(days=day)).replace(hour=15, minute=30, second=0, microsecond=0)  # 8:30 Pacific
        if when > created:
            rows.append(_sign_in(when, DEMO_APPS[day % len(DEMO_APPS)]))
    return rows[:max(1, min(top, 50))]


async def fetch(graph: GraphClient, tool: str, *, clean_sign_ins_for: str | None = None, **args: Any) -> Any:
    """Run a read tool without recording it. `clean_sign_ins_for` (the MFA demo setting) swaps in
    demo_sign_ins for that one user's signIns.list."""
    demo = (tool == "signIns.list" and clean_sign_ins_for is not None
            and str(args.get("user_id", "")).lower() == clean_sign_ins_for.lower())
    return await (demo_sign_ins(graph, **args) if demo else READS[tool](graph, **args))


async def run_read(graph: GraphClient, transcript: Transcript, tool: str, *, clean_sign_ins_for: str | None = None,
                   prefetched: Awaitable[Any] | None = None, **args: Any) -> Any:
    """Run a read tool and record the call and the IDs it observed. Graph errors are recorded, then re-raised.
    `prefetched` is this same read, already started (the investigator's sign-in prefetch): it is awaited and
    recorded exactly as if it had been run now."""
    try:
        result = await (prefetched if prefetched is not None
                        else fetch(graph, tool, clean_sign_ins_for=clean_sign_ins_for, **args))
    except GraphError as exc:
        transcript.record(tool, args, error=f"{exc.status} {exc.code}: {exc.message}")
        raise
    transcript.record(tool, args, result)
    return result
