"""A stateful stand-in for the Graph endpoints the executor writes to, mounted with respx.

    tenant = FakeTenant()
    with respx.mock: respx.route(host="graph.microsoft.com").mock(side_effect=tenant)

Holds users, group members, licenses and TAPs, and can fail the next N calls on a path prefix
(`tenant.fail("POST", "/groups/", 404, 404)`) to exercise replication lag and hard errors.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime

import httpx

TAP_VALUE = "Tap!Secret#9z"  # what Graph returns once in temporaryAccessPass


def error(status: int, message: str = "boom", code: str = "Request_BadRequest") -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message}})


class FakeTenant:
    def __init__(self, domain: str = "NorthwindParking.onmicrosoft.com"):
        self.domain = domain
        self.users: dict[str, dict] = {}
        self.members: dict[str, set[str]] = {}
        self.owners: dict[str, set[str]] = {}       # groupId (lower) -> owner user ids
        self.licenses: dict[str, set[str]] = {}
        self.taps: dict[str, list[dict]] = {}
        self.mailbox: dict[str, dict] = {}          # uid -> mailboxSettings
        self.rules: dict[str, list[dict]] = {}      # uid -> inbox messageRules
        self.auth_methods: dict[str, list[dict]] = {}  # uid -> registered methods (besides TAPs), as Graph lists them
        self.risky: dict[str, dict] = {}            # uid -> riskyUser; absent = never flagged (404)
        self.sign_ins: list[dict] = []              # the audit log's sign-in rows (the $filter isn't applied)
        self.calls: list[tuple[str, str, dict | None]] = []
        self._failures: list[list] = []
        self.stale_member_reads = 0  # memberOf reads that don't show new memberships yet (directory lag)
        self.seat_holder = self.add_user("seat-holder", "Seat Holder")

    # ------------------------------------------------------------ setup helpers

    def add_user(self, alias: str, name: str, uid: str | None = None) -> str:
        uid = uid or str(uuid.uuid4())
        self.users[uid] = {"id": uid, "displayName": name, "userPrincipalName": f"{alias}@{self.domain}",
                           "accountEnabled": True, "usageLocation": "US"}
        return uid

    def add_auth_method(self, uid: str, segment: str = "microsoftAuthenticatorMethods", name: str = "iPhone",
                        mid: str | None = None) -> str:
        """Register a method. `segment` is its Graph collection; the @odata.type is derived the way Graph names it
        (microsoftAuthenticatorMethods -> #microsoft.graph.microsoftAuthenticatorAuthenticationMethod)."""
        mid = mid or str(uuid.uuid4())
        kind = segment.removesuffix("Methods") + "AuthenticationMethod"
        self.auth_methods.setdefault(uid, []).append(
            {"id": mid, "@odata.type": f"#microsoft.graph.{kind}", "displayName": name, "segment": segment})
        return mid

    def fail(self, method: str, prefix: str, *statuses: int, message: str = "boom") -> None:
        self._failures.append([method, prefix, list(statuses), message])

    def user_by_upn(self, upn: str) -> dict | None:
        return next((u for u in self.users.values() if u["userPrincipalName"].lower() == upn.lower()), None)

    def _user(self, key: str) -> dict | None:
        return self.users.get(key) or self.user_by_upn(key)

    # ------------------------------------------------------------ routing

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1.0")
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        for failure in self._failures:
            method, prefix, statuses, message = failure
            if statuses and request.method == method and path.startswith(prefix):
                return error(statuses.pop(0), message)
        return self._route(request.method, path, body)

    def _route(self, method: str, path: str, body: dict | None) -> httpx.Response:
        if method == "POST" and path == "/users":
            if self.user_by_upn(body["userPrincipalName"]):
                return error(400, "Another object with the same value for property userPrincipalName already exists.")
            uid = str(uuid.uuid4())
            self.users[uid] = {k: v for k, v in body.items() if k != "passwordProfile"} | {"id": uid}
            return httpx.Response(201, json=self.users[uid])
        if method == "GET" and path == "/subscribedSkus":
            return httpx.Response(200, json={"value": []})
        if method == "GET" and path == "/auditLogs/signIns":
            return httpx.Response(200, json={"value": self.sign_ins})
        if m := re.fullmatch(r"/identityProtection/riskyUsers/([^/]+)", path):
            if (risky := self.risky.get(m.group(1).lower())) is None:
                return error(404, "riskyUser not found", "ResourceNotFound")
            return httpx.Response(200, json=risky)
        if m := re.fullmatch(r"/groups/([^/]+)/members/\$ref", path):
            uid = body["@odata.id"].rsplit("/", 1)[1]
            if uid not in self.users:
                return error(404, f"Resource '{uid}' does not exist", "Request_ResourceNotFound")
            group = self.members.setdefault(m.group(1).lower(), set())
            if uid in group:
                return error(400, "One or more added object references already exist for the following modified "
                                  "properties: 'members'.")
            group.add(uid)
            return httpx.Response(204)
        if m := re.fullmatch(r"/groups/([^/]+)/members/([^/]+)/\$ref", path):  # DELETE a member
            group = self.members.setdefault(m.group(1).lower(), set())
            if m.group(2) not in group:
                return error(404, "does not exist or one of its queried reference-property objects are not present",
                             "Request_ResourceNotFound")
            group.discard(m.group(2))
            return httpx.Response(204)
        if m := re.fullmatch(r"/groups/([^/]+)/owners(/\$ref)?", path):
            gid = m.group(1).lower()
            if method == "GET":
                ids = self.owners.get(gid, set())
                return httpx.Response(200, json={"value": [{"id": u} for u in ids]})
            uid = body["@odata.id"].rsplit("/", 1)[1]
            owners = self.owners.setdefault(gid, set())
            if uid in owners:
                return error(400, "One or more added object references already exist for the following modified "
                                  "properties: 'owners'.")
            owners.add(uid)
            return httpx.Response(204)
        m = re.fullmatch(r"/users/([^/]+)(/.*)?", path)
        if not m:
            return error(404, f"no route {method} {path}", "NotFound")
        user = self._user(m.group(1))
        if user is None:
            return error(404, f"Resource '{m.group(1)}' does not exist", "Request_ResourceNotFound")
        uid, rest = user["id"], m.group(2) or ""
        match method, rest:
            case "GET", "":
                return httpx.Response(200, json=user)
            case "PATCH", "":
                user.update(body)
                return httpx.Response(204)
            case "GET", "/licenseDetails":
                return httpx.Response(200, json={"value": [{"skuId": s, "skuPartNumber": "SPB"}
                                                           for s in sorted(self.licenses.get(uid, set()))]})
            case "POST", "/assignLicense":
                skus = self.licenses.setdefault(uid, set())
                skus |= {a["skuId"] for a in body["addLicenses"]}
                skus -= set(body["removeLicenses"])
                return httpx.Response(200, json=user)
            case "GET", "/memberOf" if self.stale_member_reads:
                self.stale_member_reads -= 1
                return httpx.Response(200, json={"value": []})
            case "GET", "/memberOf":
                return httpx.Response(200, json={"value": [{"id": g} for g, us in self.members.items() if uid in us]})
            case "POST", "/authentication/temporaryAccessPassMethods":
                tap = {"id": str(uuid.uuid4()), "startDateTime": body.get("startDateTime")
                       or datetime.now(UTC).isoformat(), "lifetimeInMinutes": body["lifetimeInMinutes"],
                       "isUsableOnce": body["isUsableOnce"], "isUsable": True,
                       "methodUsabilityReason": "EnabledByPolicy"}
                self.taps.setdefault(uid, []).append(tap)
                return httpx.Response(201, json=tap | {"temporaryAccessPass": TAP_VALUE})
            case "GET", "/authentication/temporaryAccessPassMethods":
                return httpx.Response(200, json={"value": self.taps.get(uid, [])})
            case "GET", "/authentication/methods":  # every method, TAPs included, like Graph's aggregate list
                methods = [{k: v for k, v in am.items() if k != "segment"} for am in self.auth_methods.get(uid, [])]
                taps = [{"id": t["id"], "@odata.type": "#microsoft.graph.temporaryAccessPassAuthenticationMethod"}
                        for t in self.taps.get(uid, [])]
                return httpx.Response(200, json={"value": methods + taps})
            case "DELETE", _ if (d := re.fullmatch(r"/authentication/([A-Za-z0-9]+Methods)/([^/]+)", rest)):
                registered = self.auth_methods.get(uid, [])
                match = next((am for am in registered if am["id"] == d.group(2)), None)
                if match is None or match["segment"] != d.group(1):  # Graph 404s a method under the wrong type
                    return error(404, "authentication method not found", "itemNotFound")
                registered.remove(match)
                return httpx.Response(204)
            case "POST", "/revokeSignInSessions":
                user["signInSessionsValidFromDateTime"] = datetime.now(UTC).isoformat()
                return httpx.Response(200, json={"value": True})
            case "GET", "/mailboxSettings":
                return httpx.Response(200, json=self.mailbox.get(uid, {}))
            case "PATCH", "/mailboxSettings":
                self.mailbox.setdefault(uid, {}).update(body)
                return httpx.Response(200, json=self.mailbox[uid])
            case "GET", "/mailFolders/inbox/messageRules":
                return httpx.Response(200, json={"value": self.rules.get(uid, [])})
            case "POST", "/mailFolders/inbox/messageRules":
                rule = {"id": str(uuid.uuid4()), **body}
                self.rules.setdefault(uid, []).append(rule)
                return httpx.Response(201, json=rule)
        return error(404, f"no route {method} {path}", "NotFound")
