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
        self.licenses: dict[str, set[str]] = {}
        self.taps: dict[str, list[dict]] = {}
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
        return error(404, f"no route {method} {path}", "NotFound")
