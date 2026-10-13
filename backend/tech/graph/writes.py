"""Write handlers (Tech.MD §4.6, §7): what the executor calls for each Graph action. Never offered to a model.

Each handler takes the step's resolved params (Graph names, as the plan dumps them) and has four parts:

  existing  the target state is already true: return the step's output and skip the act (idempotency)
  snapshot  the pre-state, for the audit log and rollback
  act       the Graph call; returns the step's output (later steps reference `output.id`)
  verify    a read-back: (ok, observed facts). Called again on a lag, so it must be a pure read.

Outputs and observed facts go into events, so they never hold a secret. A TAP goes into the run's
`Secrets` (memory only), and a new user's random password is sent once and kept nowhere.

`Deviation` means the tenant contradicts the plan (the user already exists, a TAP is already there).
It is not retried; in Week 1 it escalates the run, from Week 3 it triggers a replan.
"""

from __future__ import annotations

import json
import secrets as random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from tech.graph.client import GRAPH, GraphClient, GraphError
from tech.safety import scanner

if TYPE_CHECKING:
    from tech.core.store import Store

SEAT_DELAYS = (15.0, 15.0, 15.0, 15.0)  # a seat freed by the mock purchase can take ~1 min to be assignable


class Deviation(RuntimeError):
    """The tenant's state contradicts the plan. Not retryable."""


class SecretLeak(RuntimeError):
    """A secret was about to be written to an event."""


class Secrets:
    """Secrets one execution holds in memory only: TAP values by step, plus anything else to keep out of events."""

    def __init__(self) -> None:
        self._taps: dict[str, dict[str, Any]] = {}
        self._watched: set[str] = set()

    def put_tap(self, step_id: str, *, user_id: str, value: str, expires_at: datetime) -> None:
        self._taps[step_id] = {"value": value, "userId": user_id.lower(), "expiresAt": expires_at}
        self.watch(value)

    def tap(self, step_id: str) -> dict[str, Any] | None:
        return self._taps.get(step_id)

    def tap_for_user(self, user_id: str) -> dict[str, Any] | None:
        return next((t for t in self._taps.values() if t["userId"] == user_id.lower()), None)

    def watch(self, value: str) -> None:
        self._watched.add(value)

    def known(self) -> set[str]:
        """Every secret this execution holds, for the close-out scanner."""
        return set(self._watched)

    def guard(self, payload: Any) -> None:
        """Raise before a payload holding a secret reaches the event log. Known values only: event payloads
        (tool results, outputs) are full of random-looking IDs that the scanner's patterns would flag."""
        if scanner.scan(json.dumps(payload, default=str), known=self._watched, patterns=False):
            raise SecretLeak("an event payload contained a secret; it was not written")


@dataclass
class StepContext:
    graph: GraphClient
    store: Store
    run_id: int
    step_id: str
    domain: str
    secrets: Secrets
    link_key: str | None = None
    resumed: bool = False  # the step was started in an earlier process and never finished
    sleep: Callable[[float], Awaitable[None]] = field(default=None)  # type: ignore[assignment]


def same(a: Any, b: Any) -> bool:
    """Loose equality for verify: case-insensitive for scalars (Graph echoes case-preserved UPNs, the model
    writes 'true' or True). A scalar matches a list that holds it (the plan names one forward target, the
    read returns all of them); two lists match as loose sets."""
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(any(same(x, y) for y in b) for x in a)
    if isinstance(b, list) and not isinstance(a, dict):
        return any(same(a, y) for y in b)
    if isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
        return a == b
    return str(a).strip().casefold() == str(b).strip().casefold()


class Handler:
    async def existing(self, ctx: StepContext, p: dict[str, Any]) -> dict[str, Any] | None:
        return None

    async def snapshot(self, ctx: StepContext, p: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def act(self, ctx: StepContext, p: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def verify(self, ctx: StepContext, p: dict[str, Any], output: dict[str, Any]) -> tuple[bool, dict]:
        raise NotImplementedError


# ------------------------------------------------------------------ shared reads


async def get_or_none(graph: GraphClient, path: str, **kw: Any) -> dict | None:
    try:
        return await graph.get(path, **kw)
    except GraphError as exc:
        if exc.status == 404:
            return None
        raise


async def license_skus(graph: GraphClient, user_id: str) -> set[str]:
    return {d["skuId"].lower() async for d in graph.list(f"/users/{user_id}/licenseDetails", retry_replication=True)}


async def member_group_ids(graph: GraphClient, user_id: str) -> set[str]:
    return {g["id"].lower() async for g in graph.list(f"/users/{user_id}/memberOf", {"$select": "id"},
                                                       retry_replication=True)}


async def assign_license(ctx: StepContext, user_id: str, *, add: list[str] = (), remove: list[str] = ()) -> None:
    """assignLicense, waiting out a seat that was only just freed ("does not have any available licenses")."""
    body = {"addLicenses": [{"skuId": s, "disabledPlans": []} for s in add], "removeLicenses": list(remove)}
    for delay in (*SEAT_DELAYS, None):
        try:
            await ctx.graph.post(f"/users/{user_id}/assignLicense", body, retry_replication=True)
            return
        except GraphError as exc:
            if delay is None or "available licenses" not in exc.message.lower():
                raise
            await ctx.sleep(delay)


# ------------------------------------------------------------------ users

USER_CHECKED = ("userPrincipalName", "displayName", "usageLocation", "department", "jobTitle", "accountEnabled")


class UserCreate(Handler):
    async def existing(self, ctx, p):
        upn = p["userPrincipalName"]
        user = await get_or_none(ctx.graph, f"/users/{quote(upn)}", params={"$select": "id,displayName"})
        if user is None:
            return None
        if ctx.resumed:  # created by this step in a run that stopped before verifying
            return {"id": user["id"], "userPrincipalName": upn}
        raise Deviation(f"{upn} already exists ({user.get('displayName')}, {user['id']}) and this run didn't "
                        "create it: a rehire, a duplicate, or a demo tenant that wasn't reset")

    async def snapshot(self, ctx, p):
        return {"exists": False}

    async def act(self, ctx, p):
        password = random.token_urlsafe(24) + "!a1"  # meets complexity; the user signs in with the TAP
        ctx.secrets.watch(password)
        body = {k: v for k, v in p.items() if v is not None} | {
            "passwordProfile": {"password": password, "forceChangePasswordNextSignIn": True},
        }
        user = await ctx.graph.post("/users", body)
        return {"id": user["id"], "userPrincipalName": user["userPrincipalName"]}

    async def verify(self, ctx, p, output):
        user = await get_or_none(ctx.graph, f"/users/{output['id']}",
                                  params={"$select": "id," + ",".join(USER_CHECKED)}, retry_replication=True)
        if user is None:
            return False, {"exists": False}
        observed = {"exists": True} | {k: user.get(k) for k in USER_CHECKED}
        return all(same(p[k], observed[k]) for k in USER_CHECKED if p.get(k) is not None), observed


class UserUpdate(Handler):
    @staticmethod
    def _changes(p):
        return {k: v for k, v in p.items() if k != "userId" and v is not None}

    async def _read(self, ctx, p):
        fields = ",".join(self._changes(p))
        return await ctx.graph.get(f"/users/{p['userId']}", params={"$select": f"id,{fields}"},
                                   retry_replication=True)

    async def existing(self, ctx, p):
        user = await self._read(ctx, p)
        if all(same(v, user.get(k)) for k, v in self._changes(p).items()):
            return {"id": p["userId"]}
        return None

    async def snapshot(self, ctx, p):
        user = await self._read(ctx, p)
        return {k: user.get(k) for k in self._changes(p)}

    async def act(self, ctx, p):
        await ctx.graph.patch(f"/users/{p['userId']}", self._changes(p), retry_replication=True)
        return {"id": p["userId"]}

    async def verify(self, ctx, p, output):
        user = await self._read(ctx, p)
        observed = {k: user.get(k) for k in self._changes(p)}
        return all(same(v, observed[k]) for k, v in self._changes(p).items()), observed


# ------------------------------------------------------------------ licenses and groups


class LicenseAssign(Handler):
    async def existing(self, ctx, p):
        if p["skuId"].lower() in await license_skus(ctx.graph, p["userId"]):
            return {"id": p["userId"], "skuId": p["skuId"]}
        return None

    async def snapshot(self, ctx, p):
        return {"skus": sorted(await license_skus(ctx.graph, p["userId"]))}

    async def act(self, ctx, p):
        await assign_license(ctx, p["userId"], add=[p["skuId"]])
        return {"id": p["userId"], "skuId": p["skuId"]}

    async def verify(self, ctx, p, output):
        has = p["skuId"].lower() in await license_skus(ctx.graph, p["userId"])
        return has, {"hasSku": has, "skuId": p["skuId"]}


class GroupAddMember(Handler):
    async def existing(self, ctx, p):
        if p["groupId"].lower() in await member_group_ids(ctx.graph, p["userId"]):
            return {"groupId": p["groupId"], "userId": p["userId"]}
        return None

    async def snapshot(self, ctx, p):
        return {"isMember": False}

    async def act(self, ctx, p):
        body = {"@odata.id": f"{GRAPH}/directoryObjects/{p['userId']}"}
        try:  # a new user can 404 here for up to a minute (playbook gotcha); the client retries that
            await ctx.graph.post(f"/groups/{p['groupId']}/members/$ref", body, retry_replication=True)
        except GraphError as exc:
            if not (exc.status == 400 and "already exist" in exc.message.lower()):
                raise
        return {"groupId": p["groupId"], "userId": p["userId"]}

    async def verify(self, ctx, p, output):
        member = p["groupId"].lower() in await member_group_ids(ctx.graph, p["userId"])
        return member, {"isMember": member, "groupId": p["groupId"]}


# ------------------------------------------------------------------ credentials


class TapCreate(Handler):
    def _path(self, p):
        return f"/users/{p['userId']}/authentication/temporaryAccessPassMethods"

    async def existing(self, ctx, p):
        taps = [t async for t in ctx.graph.list(self._path(p), retry_replication=True)]
        if taps:  # a TAP's value can't be read back, so an existing one can't be delivered
            when = "this run created it in an earlier process" if ctx.resumed else "this run didn't create it"
            raise Deviation(f"the user already has a Temporary Access Pass ({when}); its value can't be read "
                            "back. Delete it and create a new one to send a link")
        return None

    async def snapshot(self, ctx, p):
        return {"taps": 0}

    async def act(self, ctx, p):
        body = {"lifetimeInMinutes": p["lifetimeInMinutes"], "isUsableOnce": p["isUsableOnce"]}
        if p.get("startDateTime"):
            body["startDateTime"] = p["startDateTime"]
        tap = await ctx.graph.post(self._path(p), body, retry_replication=True)
        start = datetime.fromisoformat(tap.get("startDateTime") or datetime.now(UTC).isoformat())
        lifetime = tap.get("lifetimeInMinutes", p["lifetimeInMinutes"])
        expires = start + timedelta(minutes=lifetime)
        ctx.secrets.put_tap(ctx.step_id, user_id=p["userId"], value=tap["temporaryAccessPass"], expires_at=expires)
        return {"id": tap["id"], "userId": p["userId"], "startDateTime": start.isoformat(),
                "lifetimeInMinutes": lifetime, "isUsableOnce": tap.get("isUsableOnce", p["isUsableOnce"]),
                "expiresAt": expires.isoformat()}

    async def verify(self, ctx, p, output):
        taps = {t["id"]: t async for t in ctx.graph.list(self._path(p), retry_replication=True)}
        tap = taps.get(output["id"])
        if tap is None:
            return False, {"exists": False}
        # Not isUsable: a TAP that starts on the start date reads back unusable ("NotYetValid") until then,
        # which is correct, so it must not be compared with a plan's expected {"isUsable": true}.
        return True, {"exists": True, "usability": tap.get("methodUsabilityReason")}


AUTH_METHOD_SEGMENTS = {  # AuthMethodDeleteParams.methodType -> the Graph collection the DELETE goes to
    "authenticator": "microsoftAuthenticatorMethods",
    "phone": "phoneMethods",
    "fido2": "fido2Methods",
    "softwareOath": "softwareOathMethods",
    "windowsHelloForBusiness": "windowsHelloForBusinessMethods",
}


async def auth_method_ids(graph: GraphClient, user_id: str) -> set[str]:
    """Every registered method's id. The aggregated /methods list carries each method's id whatever its type."""
    return {m["id"].lower() async for m in graph.list(f"/users/{user_id}/authentication/methods",
                                                       retry_replication=True)}


class AuthMethodDelete(Handler):
    """DELETE one registered MFA method. Not restorable by us: the user registers a new one."""

    def _path(self, p):
        return f"/users/{p['userId']}/authentication/{AUTH_METHOD_SEGMENTS[p['methodType']]}/{p['methodId']}"

    async def existing(self, ctx, p):
        if p["methodId"].lower() not in await auth_method_ids(ctx.graph, p["userId"]):
            return {"userId": p["userId"], "methodId": p["methodId"]}  # already gone
        return None

    async def snapshot(self, ctx, p):
        return {"methodPresent": True, "methodType": p["methodType"]}

    async def act(self, ctx, p):
        try:
            await ctx.graph.delete(self._path(p), retry_replication=True)
        except GraphError as exc:
            if not (exc.status == 404 or "does not exist" in exc.message.lower()):
                raise
        return {"userId": p["userId"], "methodId": p["methodId"]}

    async def verify(self, ctx, p, output):
        gone = output["methodId"].lower() not in await auth_method_ids(ctx.graph, p["userId"])
        return gone, {"methodPresent": not gone, "methodId": output["methodId"]}


# ------------------------------------------------------------------ offboarding (Tech.MD §6.2, §7)


async def group_owner_ids(graph: GraphClient, group_id: str) -> set[str]:
    return {o["id"].lower() async for o in graph.list(f"/groups/{group_id}/owners", {"$select": "id"},
                                                       retry_replication=True)}


class SessionsRevoke(Handler):
    """revokeSignInSessions invalidates refresh tokens; verify that signInSessionsValidFromDateTime moved up."""

    async def snapshot(self, ctx, p):
        user = await ctx.graph.get(f"/users/{p['userId']}", params={"$select": "id,signInSessionsValidFromDateTime"})
        return {"signInSessionsValidFromDateTime": user.get("signInSessionsValidFromDateTime")}

    async def act(self, ctx, p):
        before = (await self.snapshot(ctx, p))["signInSessionsValidFromDateTime"]
        await ctx.graph.post(f"/users/{p['userId']}/revokeSignInSessions", {}, retry_replication=True)
        return {"id": p["userId"], "revokedAfter": before}

    async def verify(self, ctx, p, output):
        user = await ctx.graph.get(f"/users/{p['userId']}", params={"$select": "id,signInSessionsValidFromDateTime"},
                                   retry_replication=True)
        now = user.get("signInSessionsValidFromDateTime")
        before = output.get("revokedAfter")
        moved = now is not None and (before is None or now > before)
        return moved, {"signInSessionsValidFromDateTime": now}


class MailboxAutoReply(Handler):
    """PATCH mailboxSettings.automaticRepliesSetting. Reversible: snapshot restores the prior setting."""

    async def _settings(self, ctx, p):
        return await ctx.graph.get(f"/users/{p['userId']}/mailboxSettings", retry_replication=True)

    def _desired(self, p):
        reply = {
            "status": "scheduled" if p.get("scheduledEndDateTime") else "alwaysEnabled",
            "externalReplyMessage": p["message"],
            "internalReplyMessage": p.get("internalMessage") or p["message"],
            "externalAudience": "all",
        }
        if p.get("scheduledEndDateTime"):
            reply["scheduledEndDateTime"] = {"dateTime": p["scheduledEndDateTime"], "timeZone": "UTC"}
        return reply

    async def existing(self, ctx, p):
        auto = (await self._settings(ctx, p)).get("automaticRepliesSetting") or {}
        if auto.get("status") in {"scheduled", "alwaysEnabled"} and \
                same(auto.get("externalReplyMessage"), p["message"]):
            return {"id": p["userId"]}
        return None

    async def snapshot(self, ctx, p):
        return {"automaticRepliesSetting": (await self._settings(ctx, p)).get("automaticRepliesSetting")}

    async def act(self, ctx, p):
        await ctx.graph.patch(f"/users/{p['userId']}/mailboxSettings",
                              {"automaticRepliesSetting": self._desired(p)}, retry_replication=True)
        return {"id": p["userId"]}

    async def verify(self, ctx, p, output):
        auto = (await self._settings(ctx, p)).get("automaticRepliesSetting") or {}
        on = auto.get("status") in {"scheduled", "alwaysEnabled"}
        return on and same(auto.get("externalReplyMessage"), p["message"]), {"status": auto.get("status")}


class MailboxForwardRule(Handler):
    """POST an inbox rule that forwards to an internal recipient. External forwarding is blocked in code."""

    def _path(self, p):
        return f"/users/{p['userId']}/mailFolders/inbox/messageRules"

    async def existing(self, ctx, p):
        async for rule in ctx.graph.list(self._path(p), retry_replication=True):
            if same(rule.get("displayName"), p["ruleName"]):
                return {"id": rule["id"], "userId": p["userId"]}
        return None

    async def snapshot(self, ctx, p):
        return {"ruleName": p["ruleName"]}

    async def act(self, ctx, p):
        domain = ctx.domain.lower()
        if p["forwardTo"].split("@")[-1].lower() != domain:
            raise Deviation(f"forwardTo {p['forwardTo']} is outside {domain}; external forwarding is forbidden")
        body = {"displayName": p["ruleName"], "sequence": 1, "isEnabled": True,
                "actions": {"forwardTo": [{"emailAddress": {"address": p["forwardTo"]}}], "stopProcessingRules": False}}
        rule = await ctx.graph.post(self._path(p), body, retry_replication=True)
        return {"id": rule["id"], "userId": p["userId"]}

    async def verify(self, ctx, p, output):
        async for rule in ctx.graph.list(self._path(p), retry_replication=True):
            if rule["id"] == output["id"]:
                to = [a["emailAddress"]["address"] async for a in _forward_targets(rule)]
                return bool(to), {"forwardTo": to}
        return False, {"exists": False}


async def _forward_targets(rule):
    for target in (rule.get("actions") or {}).get("forwardTo") or []:
        yield target


class GroupRemoveMember(Handler):
    async def existing(self, ctx, p):
        if p["groupId"].lower() not in await member_group_ids(ctx.graph, p["userId"]):
            return {"groupId": p["groupId"], "userId": p["userId"]}  # already not a member
        return None

    async def snapshot(self, ctx, p):
        return {"isMember": True}

    async def act(self, ctx, p):
        try:
            await ctx.graph.delete(f"/groups/{p['groupId']}/members/{p['userId']}/$ref", retry_replication=True)
        except GraphError as exc:
            if not (exc.status == 404 or "does not exist" in exc.message.lower()):
                raise
        return {"groupId": p["groupId"], "userId": p["userId"]}

    async def verify(self, ctx, p, output):
        gone = p["groupId"].lower() not in await member_group_ids(ctx.graph, p["userId"])
        return gone, {"isMember": not gone, "groupId": p["groupId"]}


class GroupAddOwner(Handler):
    async def existing(self, ctx, p):
        if p["groupId"].lower() and p["userId"].lower() in await group_owner_ids(ctx.graph, p["groupId"]):
            return {"groupId": p["groupId"], "userId": p["userId"]}
        return None

    async def snapshot(self, ctx, p):
        return {"isOwner": False}

    async def act(self, ctx, p):
        body = {"@odata.id": f"{GRAPH}/users/{p['userId']}"}
        try:
            await ctx.graph.post(f"/groups/{p['groupId']}/owners/$ref", body, retry_replication=True)
        except GraphError as exc:
            if not (exc.status == 400 and "already exist" in exc.message.lower()):
                raise
        return {"groupId": p["groupId"], "userId": p["userId"]}

    async def verify(self, ctx, p, output):
        owner = p["userId"].lower() in await group_owner_ids(ctx.graph, p["groupId"])
        return owner, {"isOwner": owner, "groupId": p["groupId"]}


# ------------------------------------------------------------------ files (Build-W4 Day 18)


async def file_versions(graph: GraphClient, drive_id: str, item_id: str) -> list[dict]:
    """A file's versions as Graph lists them, newest (the current one) first."""
    return [v async for v in graph.list(f"/drives/{drive_id}/items/{item_id}/versions", retry_replication=True)]


def find_version(versions: list[dict], version_id: str) -> dict:
    if (found := next((v for v in versions if v["id"] == version_id), None)) is None:
        raise Deviation(f"version {version_id} is not in the file's history "
                        f"({', '.join(v['id'] for v in versions)})")
    return found


class FileRestoreVersion(Handler):
    """restoreVersion copies the chosen version in as a new current version; nothing is lost, the overwritten
    one stays in the history. On SharePoint the copy is byte-identical and listed within ~2 s (Build-Day18
    spike), so verify looks for a new version with the chosen one's size. It can't compare modified times:
    the new version is stamped with the time of the restore."""

    async def existing(self, ctx, p):
        if not ctx.resumed:  # a second restore is harmless (one more version), so only a resumed step skips
            return None
        started = next(e.payload for e in ctx.store.events_of(ctx.run_id, "step_started")
                       if e.payload["step_id"] == ctx.step_id)
        output = {"itemId": p["itemId"], "versionId": p["versionId"],
                  "previousVersionId": started["snapshot"]["currentVersionId"],
                  "versionsBefore": started["snapshot"]["versionIds"]}
        ok, _ = await self.verify(ctx, p, output)
        return output if ok else None

    async def snapshot(self, ctx, p):
        versions = await file_versions(ctx.graph, p["driveId"], p["itemId"])
        find_version(versions, p["versionId"])
        if versions[0]["id"] == p["versionId"]:
            raise Deviation(f"version {p['versionId']} is already the current version; there is nothing to restore")
        return {"currentVersionId": versions[0]["id"], "versionIds": [v["id"] for v in versions]}

    async def act(self, ctx, p):
        before = await file_versions(ctx.graph, p["driveId"], p["itemId"])
        await ctx.graph.post(f"/drives/{p['driveId']}/items/{p['itemId']}/versions/{p['versionId']}/restoreVersion",
                             retry_replication=True)
        return {"itemId": p["itemId"], "versionId": p["versionId"], "previousVersionId": before[0]["id"],
                "versionsBefore": [v["id"] for v in before]}

    async def verify(self, ctx, p, output):
        versions = await file_versions(ctx.graph, p["driveId"], p["itemId"])
        chosen, current = find_version(versions, p["versionId"]), versions[0]
        new = current["id"] not in output["versionsBefore"]
        matches = current.get("size") == chosen.get("size")
        return new and matches, {"currentMatches": p["versionId"] if matches else None,
                                 "currentVersionId": current["id"], "size": current.get("size")}


HANDLERS: dict[str, Handler] = {
    "user.create": UserCreate(),
    "user.update": UserUpdate(),
    "license.assign": LicenseAssign(),
    "group.addMember": GroupAddMember(),
    "tap.create": TapCreate(),
    "sessions.revoke": SessionsRevoke(),
    "mailbox.autoReply": MailboxAutoReply(),
    "mailbox.forwardRule": MailboxForwardRule(),
    "group.removeMember": GroupRemoveMember(),
    "group.addOwner": GroupAddOwner(),
    "authMethod.delete": AuthMethodDelete(),
    "file.restoreVersion": FileRestoreVersion(),
}
