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


@dataclass
class Transcript:
    """What the investigator looked at in one run: the calls, and every object ID it saw."""

    calls: list[ToolCall] = field(default_factory=list)
    observed_ids: set[str] = field(default_factory=set)

    def record(self, tool: str, args: dict[str, Any], result: Any = None, error: str | None = None) -> None:
        self.calls.append(ToolCall(tool, args, error))
        if result is not None:
            self.observed_ids |= {m.lower() for m in GUID_RE.findall(json.dumps(result, default=str))}

    def observed(self, object_id: str) -> bool:
        return object_id.lower() in self.observed_ids

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
    """A user's groups, each classified as m365 | security | mail_security | distribution_list."""
    params = {"$select": "id,displayName,groupTypes,mailEnabled,securityEnabled,mail"}
    return [
        {"id": g["id"], "displayName": g.get("displayName"), "kind": classify(g), "mail": g.get("mail")}
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
    "files.search": files_search,
    "files.versions": file_versions,
}


async def run_read(graph: GraphClient, transcript: Transcript, tool: str, **args: Any) -> Any:
    """Run a read tool and record the call and the IDs it observed. Graph errors are recorded, then re-raised."""
    try:
        result = await READS[tool](graph, **args)
    except GraphError as exc:
        transcript.record(tool, args, error=f"{exc.status} {exc.code}: {exc.message}")
        raise
    transcript.record(tool, args, result)
    return result
