"""A stateful stand-in for the Graph endpoints the executor writes to, mounted with respx.

    tenant = FakeTenant()
    with respx.mock: respx.route(host="graph.microsoft.com").mock(side_effect=tenant)

Holds users, group members, licenses and TAPs, and can fail the next N calls on a path prefix
(`tenant.fail("POST", "/groups/", 404, 404)`) to exercise replication lag and hard errors.

It also answers the investigator's reads (users.search/list/get, memberOf with group kinds, ownedObjects,
the license pool), so the eval harness can run a whole ticket against it. Groups and SKUs registered with
`add_group`/`add_sku` carry the fields those reads return; unregistered ones still work as bare ids.

Team-site files (`add_drive`/`add_file`) have a version history, newest first like Graph's, with
SharePoint-shaped IDs (`b!…` drives, `01…` items, `2.0` versions). A restore appends a copy of the chosen
version as the new current one, as SharePoint does. `search_lag` makes the next N searches find nothing.
"""

from __future__ import annotations

import json
import re
import secrets
import uuid
from datetime import UTC, datetime
from urllib.parse import unquote_plus

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
        self.sign_ins: list[dict] = []              # sign-in rows; filtered by userId when a row has one
        self.groups: dict[str, dict] = {}           # groupId (lower) -> group object, as Graph returns it
        self.skus: dict[str, dict] = {}             # skuId (lower) -> {skuPartNumber, enabled}
        self.managers: dict[str, str] = {}          # uid -> manager uid
        self.calls: list[tuple[str, str, dict | None]] = []
        self._failures: list[list] = []
        self.stale_member_reads = 0  # memberOf reads that don't show new memberships yet (directory lag)
        self.stale_method_reads = 0  # auth-method reads that still show deleted methods (seen live, Day 16)
        self.deleted_methods: dict[str, list[dict]] = {}
        self.drives: dict[str, str] = {}            # groupId (lower) -> driveId, for groups with a team site
        self.files: dict[str, dict] = {}            # itemId -> {id, name, drive, versions (newest first)}
        self.contents: dict[tuple[str, str], bytes] = {}  # (itemId, versionId) -> that version's bytes
        self.search_lag = 0  # searches that find nothing yet (the index trails a fresh upload)
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

    def add_group(self, gid: str, name: str, kind: str = "security", role_assignable: bool = False) -> str:
        """Register a group. `kind` is how reads.classify will see it: m365 | security | mail_security |
        distribution_list."""
        flags = {"m365": (["Unified"], True, False), "security": ([], False, True),
                 "mail_security": ([], True, True), "distribution_list": ([], True, False)}[kind]
        self.groups[gid.lower()] = {"@odata.type": "#microsoft.graph.group", "id": gid, "displayName": name,
                                    "groupTypes": flags[0], "mailEnabled": flags[1], "securityEnabled": flags[2],
                                    "mail": None, "isAssignableToRole": role_assignable}
        return gid

    def add_sku(self, sku_id: str, part_number: str, enabled: int) -> str:
        self.skus[sku_id.lower()] = {"skuId": sku_id, "skuPartNumber": part_number, "enabled": enabled}
        return sku_id

    def add_drive(self, group_id: str, drive_id: str | None = None) -> str:
        """Give a group a team site with a document library."""
        drive_id = drive_id or "b!" + secrets.token_urlsafe(48)
        self.drives[group_id.lower()] = drive_id
        return drive_id

    def add_file(self, drive_id: str, name: str, versions: list[tuple[str, int]], item_id: str | None = None,
                 ) -> tuple[str, list[str]]:
        """Upload a file with a version per (time, size), oldest first. Returns its id and version ids, oldest
        first ("1.0", "2.0"...)."""
        item_id = item_id or "01" + secrets.token_hex(16).upper()
        rows = [{"id": f"{n}.0", "lastModifiedDateTime": when, "size": size,
                 "lastModifiedBy": {"user": {"displayName": "SharePoint App"}}}
                for n, (when, size) in enumerate(versions, 1)]
        self.files[item_id] = {"id": item_id, "name": name, "drive": drive_id, "versions": rows[::-1]}
        for r in rows:  # distinct bytes per version, `size` long
            self.contents[(item_id, r["id"])] = (f"{name} {r['id']} ".encode() * r["size"])[:r["size"]]
        return item_id, [r["id"] for r in rows]

    def _item(self, item: dict) -> dict:
        current = item["versions"][0]
        return {"id": item["id"], "name": item["name"], "size": current["size"], "file": {},
                "lastModifiedDateTime": current["lastModifiedDateTime"]}

    def _drive_route(self, method: str, drive_id: str, rest: str) -> httpx.Response:
        items = [f for f in self.files.values() if f["drive"] == drive_id]
        if m := re.fullmatch(r"/root/search\(q='(.*)'\)", rest):
            if self.search_lag:
                self.search_lag -= 1
                return httpx.Response(200, json={"value": []})
            words = m.group(1).replace("''", "'").casefold().split()
            return httpx.Response(200, json={"value": [self._item(f) for f in items
                                                       if all(w in f["name"].casefold() for w in words)]})
        if rest == "/root/children":
            return httpx.Response(200, json={"value": [self._item(f) for f in items]})
        if m := re.fullmatch(r"/items/([^/]+)(?:/versions/([^/]+))?/content", rest):
            if (item := self.files.get(m.group(1))) is None:
                return error(404, "The resource could not be found.", "itemNotFound")
            version = m.group(2) or item["versions"][0]["id"]
            if m.group(2) == item["versions"][0]["id"]:  # Graph won't serve the current version this way
                return error(400, "The request is malformed or incorrect.", "invalidRequest")
            if (data := self.contents.get((item["id"], version))) is None:
                return error(404, "The resource could not be found.", "itemNotFound")
            return httpx.Response(200, content=data, headers={"Content-Type": "application/octet-stream"})
        m = re.fullmatch(r"/items/([^/]+)/versions(?:/([^/]+)/restoreVersion)?", rest)
        if not m or (item := self.files.get(m.group(1))) is None or item["drive"] != drive_id:
            return error(404, "The resource could not be found.", "itemNotFound")
        if method == "GET" and not m.group(2):
            return httpx.Response(200, json={"value": item["versions"]})
        if method == "POST" and m.group(2):
            chosen = next((v for v in item["versions"] if v["id"] == m.group(2)), None)
            if chosen is None:
                return error(404, "The resource could not be found.", "itemNotFound")
            major = max(int(v["id"].split(".")[0]) for v in item["versions"]) + 1
            self.contents[(item["id"], f"{major}.0")] = self.contents[(item["id"], chosen["id"])]
            item["versions"].insert(0, chosen | {"id": f"{major}.0", "lastModifiedDateTime": datetime.now(UTC)
                                                 .strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                 "lastModifiedBy": {"application": {"displayName": "Microsoft Graph"}}})
            return httpx.Response(204)
        return error(405, f"no route {method} {rest}", "MethodNotAllowed")

    def _group(self, gid: str) -> dict:
        return self.groups.get(gid.lower(), {"@odata.type": "#microsoft.graph.group", "id": gid})

    def _part_number(self, sku_id: str) -> str:
        return self.skus.get(sku_id.lower(), {}).get("skuPartNumber", "SPB")

    def _list_users(self, query: str) -> list[dict]:
        """GET /users: `id eq '…'` (the signInActivity read), startswith(...) (users.search), or every user."""
        if m := re.search(r"id eq '([^']+)'", query):
            user = self.users.get(m.group(1))
            return [user | {"signInActivity": user.get("signInActivity")}] if user else []
        if m := re.search(r"startswith\(displayName,'([^']*)'\)", query):
            prefix = m.group(1).lower()
            return [u for u in self.users.values()
                    if any(str(u.get(k) or "").lower().startswith(prefix)
                           for k in ("displayName", "surname", "userPrincipalName"))]
        return list(self.users.values())

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
        return self._route(request.method, path, body, unquote_plus(request.url.query.decode()))

    def _route(self, method: str, path: str, body: dict | None, query: str = "") -> httpx.Response:
        if method == "GET" and path == "/users":
            return httpx.Response(200, json={"value": self._list_users(query)})
        if method == "POST" and path == "/users":
            if self.user_by_upn(body["userPrincipalName"]):
                return error(400, "Another object with the same value for property userPrincipalName already exists.")
            uid = str(uuid.uuid4())
            self.users[uid] = {k: v for k, v in body.items() if k != "passwordProfile"} | {"id": uid}
            return httpx.Response(201, json=self.users[uid])
        if method == "GET" and path == "/subscribedSkus":
            consumed = [s.lower() for skus in self.licenses.values() for s in skus]
            return httpx.Response(200, json={"value": [
                {"skuId": s["skuId"], "skuPartNumber": s["skuPartNumber"], "prepaidUnits": {"enabled": s["enabled"]},
                 "consumedUnits": consumed.count(key)} for key, s in self.skus.items()]})
        if method == "GET" and path == "/auditLogs/signIns":
            m = re.search(r"userId eq '([^']+)'", query)
            rows = [r for r in self.sign_ins if not m or r.get("userId", m.group(1)).lower() == m.group(1).lower()]
            return httpx.Response(200, json={"value": rows})
        if method == "GET" and path == "/groups" and "Unified" in query:
            return httpx.Response(200, json={"value": [g for g in self.groups.values()
                                                       if "Unified" in g["groupTypes"]]})
        if m := re.fullmatch(r"/groups/([^/]+)/drive", path):
            if (drive := self.drives.get(m.group(1).lower())) is None:
                return error(404, "Resource not found for the segment 'drive'.", "itemNotFound")
            return httpx.Response(200, json={"id": drive})
        if m := re.fullmatch(r"/drives/([^/]+)(/.*)", path):
            return self._drive_route(method, m.group(1), m.group(2))
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
                return httpx.Response(200, json={"value": [{"skuId": s, "skuPartNumber": self._part_number(s)}
                                                           for s in sorted(self.licenses.get(uid, set()))]})
            case "GET", "/manager":
                if (manager := self.users.get(self.managers.get(uid, ""))) is None:
                    return error(404, "Resource 'manager' does not exist", "Request_ResourceNotFound")
                return httpx.Response(200, json=manager)
            case "GET", "/ownedObjects":
                return httpx.Response(200, json={"value": [self._group(g) for g, us in self.owners.items()
                                                           if uid in us]})
            case "POST", "/assignLicense":
                skus = self.licenses.setdefault(uid, set())
                skus |= {a["skuId"] for a in body["addLicenses"]}
                skus -= set(body["removeLicenses"])
                return httpx.Response(200, json=user)
            case "GET", "/memberOf" if self.stale_member_reads:
                self.stale_member_reads -= 1
                return httpx.Response(200, json={"value": []})
            case "GET", "/memberOf":
                return httpx.Response(200, json={"value": [self._group(g) for g, us in self.members.items()
                                                           if uid in us]})
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
                registered = self.auth_methods.get(uid, [])
                if self.stale_method_reads:
                    self.stale_method_reads -= 1
                    registered = registered + self.deleted_methods.get(uid, [])
                methods = [{k: v for k, v in am.items() if k != "segment"} for am in registered]
                taps = [{"id": t["id"], "@odata.type": "#microsoft.graph.temporaryAccessPassAuthenticationMethod"}
                        for t in self.taps.get(uid, [])]
                return httpx.Response(200, json={"value": methods + taps})
            case "DELETE", _ if (d := re.fullmatch(r"/authentication/([A-Za-z0-9]+Methods)/([^/]+)", rest)):
                registered = self.auth_methods.get(uid, [])
                match = next((am for am in registered if am["id"] == d.group(2)), None)
                if match is None or match["segment"] != d.group(1):  # Graph 404s a method under the wrong type
                    return error(404, "authentication method not found", "itemNotFound")
                registered.remove(match)
                self.deleted_methods.setdefault(uid, []).append(match)
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
