# /// script
# requires-python = ">=3.11"
# dependencies = ["azure-identity>=1.17", "httpx>=0.27", "openpyxl>=3.1"]
# ///
"""Step 5: seed the "Northwind Parking" demo org (Tech.MD §10).

    uv run tenant/seed.py

Runs app-only with the credentials setup_app.py wrote. Idempotent: existing
users and groups are updated in place, missing members and licenses are added,
and files are only uploaded when they don't exist yet.

New user passwords go to tenant/.secrets/seed-passwords.json (never printed).
"""

from __future__ import annotations

import io
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import quote

from openpyxl import Workbook

from graph import GRAPH, SECRETS_DIR, Graph, GraphError, app_graph, q

PASSWORDS_PATH = SECRETS_DIR / "seed-passwords.json"


@dataclass
class Person:
    alias: str  # mailNickname; the part before @
    given: str
    surname: str
    title: str
    dept: str
    license: str | None = None  # "primary" (Marcus's license) or "p1" (Entra ID P1), else unlicensed
    manager: str | None = None


# Mixed email formats on purpose: legacy first@ and flast@, recent hires first.last@.
# Priya Nair must NOT exist; the onboarding scenario creates her.
PEOPLE = [
    Person("tgarcia", "Taylor", "Garcia", "Office Manager", "Administration", "primary"),  # approved requester
    Person("marcus", "Marcus", "Webb", "Senior Accountant", "Accounting", "primary", "tgarcia"),
    Person("kevin", "Kevin", "Brooks", "Operations Supervisor", "Operations", "primary", "tgarcia"),
    Person("dennis", "Dennis", "Okafor", "Facilities Coordinator", "Facilities", "p1", "tgarcia"),
    Person("jlee", "Jordan", "Lee", "Lead Lot Attendant", "Operations", None, "kevin"),  # NOT an approved requester
    Person("sbaker", "Sophie", "Baker", "Billing Specialist", "Accounting", None, "tgarcia"),  # file-restore requester
    Person("rpatel", "Raj", "Patel", "Parking Enforcement Officer", "Operations", None, "kevin"),
    Person("mchen", "Mei", "Chen", "Payroll Clerk", "Accounting", None, "marcus"),
    Person("alex.rivera", "Alex", "Rivera", "Customer Service Representative", "Customer Service", None, "tgarcia"),
    Person("maria.santos", "Maria", "Santos", "Accounts Payable Clerk", "Accounting", None, "marcus"),
    Person("emma.clarke", "Emma", "Clarke", "Marketing Coordinator", "Marketing", None, "tgarcia"),
    Person("omar.haddad", "Omar", "Haddad", "Lot Attendant", "Operations", None, "kevin"),
    Person("grace.kim", "Grace", "Kim", "Revenue Analyst", "Accounting", None, "marcus"),  # second file-restore requester
    Person("luis.ortega", "Luis", "Ortega", "Maintenance Technician", "Facilities", None, "dennis"),
]
# Service account: an app signs in with it, no manager, no MFA. Listed in known_service_accounts.
SERVICE_ACCOUNT = Person("reports", "Reports", "Service", "Service account (reporting app)", "IT")


@dataclass
class Group:
    name: str
    alias: str
    unified: bool  # True: Microsoft 365 group (gets a SharePoint site); False: security group
    owners: list[str]
    members: list[str]


GROUPS = [
    # Marcus: 4 security/M365 groups, incl. RDS-Users and VPN-Users (non-Graph access in client facts)
    Group("Accounting", "accounting-team", True, ["tgarcia"], ["marcus", "sbaker", "mchen", "maria.santos", "grace.kim"]),
    Group("Finance Files", "finance-files", False, ["tgarcia"], ["marcus", "mchen", "maria.santos"]),
    Group("RDS-Users", "rds-users", False, ["tgarcia"], ["marcus", "kevin", "maria.santos"]),
    Group("VPN-Users", "vpn-users", False, ["tgarcia"], ["marcus", "kevin", "tgarcia"]),
    # Kevin owns two groups, so offboarding has to hand ownership over
    Group("Field Operations", "field-operations", True, ["kevin"], ["kevin", "jlee", "rpatel", "omar.haddad"]),
    Group("Parking Supervisors", "parking-supervisors", False, ["kevin"], ["kevin", "jlee"]),
    # Team site for the file-restore scenario
    Group("Northwind Operations", "northwind-operations", True, ["tgarcia"], ["tgarcia", "kevin", "sbaker", "grace.kim"]),
]
TEAM_SITE_GROUP = "northwind-operations"

# Graph can't create distribution lists; this one is made by hand in the Exchange admin center.
DL_ALIAS = "accounting-announce"

SKU_PRIMARY = ["O365_BUSINESS_ESSENTIALS", "SPB"]  # Business Basic after the trial, Business Premium during it
SKU_P1 = ["AAD_PREMIUM"]


def new_password() -> str:
    return f"Nw-{secrets.token_urlsafe(12)}9!"


def upn(alias: str, domain: str) -> str:
    return f"{alias}@{domain}"


# ---------------------------------------------------------------- users


def ensure_user(g: Graph, p: Person, domain: str, passwords: dict[str, str], enabled: bool = True) -> dict:
    profile = {
        "displayName": f"{p.given} {p.surname}",
        "givenName": p.given,
        "surname": p.surname,
        "jobTitle": p.title,
        "department": p.dept,
        "companyName": "Northwind Parking",
        "officeLocation": "Main Office",
        "usageLocation": "US",
        "accountEnabled": enabled,
    }
    try:
        user = g.get(f"/users/{quote(upn(p.alias, domain))}", params={"$select": "id,userPrincipalName"})
        g.patch(f"/users/{user['id']}", profile)
        return user
    except GraphError as exc:
        if exc.status != 404:
            raise
    password = new_password()
    user = g.post("/users", profile | {
        "userPrincipalName": upn(p.alias, domain),
        "mailNickname": p.alias,
        "passwordProfile": {"password": password, "forceChangePasswordNextSignIn": False},
    })
    passwords[upn(p.alias, domain)] = password
    print(f"  + {user['userPrincipalName']}")
    return user


def set_manager(g: Graph, user_id: str, manager_id: str) -> None:
    g.put(f"/users/{user_id}/manager/$ref", {"@odata.id": f"{GRAPH}/users/{manager_id}"}, retry_404=5)


# ---------------------------------------------------------------- groups


def ensure_group(g: Graph, grp: Group, ids: dict[str, str]) -> dict:
    existing = g.first("/groups", {"$filter": f"mailNickname eq '{q(grp.alias)}'"})
    if existing:
        group = existing
    else:
        body = {
            "displayName": grp.name,
            "mailNickname": grp.alias,
            "owners@odata.bind": [f"{GRAPH}/users/{ids[o]}" for o in grp.owners],
            "members@odata.bind": [f"{GRAPH}/users/{ids[m]}" for m in grp.members],
        }
        if grp.unified:
            body |= {"groupTypes": ["Unified"], "mailEnabled": True, "securityEnabled": False, "visibility": "Private"}
        else:
            body |= {"groupTypes": [], "mailEnabled": False, "securityEnabled": True}
        for attempt in range(5):  # just-created users may not have replicated yet
            try:
                group = g.post("/groups", body)
                break
            except GraphError as exc:
                if attempt == 4 or "exist" not in exc.message.lower():
                    raise
                time.sleep(2 ** (attempt + 1))
        print(f"  + group {grp.name}")

    for rel, aliases in (("owners", grp.owners), ("members", grp.members)):
        current = {o["id"] for o in g.list(f"/groups/{group['id']}/{rel}", {"$select": "id"}, retry_404=5)}
        for alias in aliases:
            if ids[alias] not in current:
                g.post(f"/groups/{group['id']}/{rel}/$ref", {"@odata.id": f"{GRAPH}/directoryObjects/{ids[alias]}"}, retry_404=5)
    return group


# ---------------------------------------------------------------- licenses


def skus(g: Graph) -> dict[str, dict]:
    return {s["skuPartNumber"]: s for s in g.list("/subscribedSkus")}


def pick_sku(available: dict[str, dict], prefs: list[str]) -> dict | None:
    return next((available[p] for p in prefs if p in available), None)


def free_seats(sku: dict) -> int:
    return sku["prepaidUnits"]["enabled"] - sku["consumedUnits"]


def ensure_license(g: Graph, user_id: str, sku_id: str) -> bool:
    user = g.get(f"/users/{user_id}", params={"$select": "assignedLicenses"})
    if any(lic["skuId"] == sku_id for lic in user["assignedLicenses"]):
        return False
    g.post(f"/users/{user_id}/assignLicense", {"addLicenses": [{"skuId": sku_id, "disabledPlans": []}], "removeLicenses": []}, retry_404=5)
    return True


# ---------------------------------------------------------------- files


def workbook(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


RATES_HEADER = ["Lot", "Hourly", "Daily max", "Monthly permit", "Effective"]
FILES = {
    "Q3 Parking Rates.xlsx": [
        workbook([RATES_HEADER, ["Lot A (Main St)", 3.00, 18.00, 140, "2026-07-01"], ["Lot B (Harbor)", 2.50, 15.00, 120, "2026-07-01"], ["Garage C", 4.00, 24.00, 185, "2026-07-01"]]),
        workbook([RATES_HEADER, ["Lot A (Main St)", 3.25, 19.00, 145, "2026-07-01"], ["Lot B (Harbor)", 2.50, 15.00, 120, "2026-07-01"], ["Garage C", 4.25, 25.00, 190, "2026-07-01"], ["Lot D (Stadium)", 5.00, 30.00, 210, "2026-08-15"]]),
        # The overwrite: someone pasted a half-finished draft over the real sheet
        workbook([["Lot", "Hourly", "Notes"], ["Lot A (Main St)", None, "TBD"], ["Garage C", None, "ask Kevin"]]),
    ],
    "Lot Permits Tracker.xlsx": [
        workbook([["Permit #", "Holder", "Lot", "Expires"], [1001, "Harbor Dental", "Lot B (Harbor)", "2026-12-31"], [1002, "Main St Bakery", "Lot A (Main St)", "2026-11-30"]]),
        workbook([["Permit #", "Holder", "Lot", "Expires"], [1001, "Harbor Dental", "Lot B (Harbor)", "2026-12-31"], [1002, "Main St Bakery", "Lot A (Main St)", "2026-11-30"], [1003, "City Library", "Garage C", "2027-01-31"]]),
        workbook([["Permit #", "Holder"], [1003, "City Library"]]),
    ],
}


def group_drive(g: Graph, group_id: str) -> dict:
    """A new Microsoft 365 group's SharePoint site takes a few minutes to provision."""
    deadline = time.time() + 600
    while True:
        try:
            return g.get(f"/groups/{group_id}/drive")
        except GraphError as exc:
            if time.time() > deadline or exc.status not in (400, 403, 404):
                raise
            print("  waiting for the team site to provision...")
            time.sleep(20)


def ensure_versioned_file(g: Graph, drive_id: str, name: str, versions: list[bytes]) -> None:
    path = f"/drives/{drive_id}/root:/{quote(name)}"
    try:
        g.get(path)
        print(f"  = {name} already exists (reset re-seeds it)")
        return
    except GraphError as exc:
        if exc.status != 404:
            raise
    for data in versions:  # each upload of the same path creates a new version
        g.request("PUT", f"{path}:/content", content=data, headers={"Content-Type": "application/octet-stream"})
        time.sleep(2)
    print(f"  + {name} with {len(versions)} versions")


# ---------------------------------------------------------------- main


def run(g: Graph, env: dict[str, str], *, files: bool = True, warn_priya: bool = True) -> tuple[dict[str, str], dict[str, str]]:
    """Seed (or re-seed) everything. Returns user ids and group ids by alias."""
    domain = env["M365_DOMAIN"]
    SECRETS_DIR.mkdir(mode=0o700, exist_ok=True)
    passwords: dict[str, str] = json.loads(PASSWORDS_PATH.read_text()) if PASSWORDS_PATH.exists() else {}

    try:
        print("Users")
        ids: dict[str, str] = {}
        for p in [*PEOPLE, SERVICE_ACCOUNT]:
            ids[p.alias] = ensure_user(g, p, domain, passwords)["id"]
        for p in PEOPLE:
            if p.manager:
                set_manager(g, ids[p.alias], ids[p.manager])

        if warn_priya and g.first("/users", {"$filter": "startswith(displayName,'Priya')", "$select": "userPrincipalName"}):
            print("  ! a 'Priya' user exists from an earlier run; reset before demoing onboarding")

        print("Licenses")
        available = skus(g)
        primary, p1 = pick_sku(available, SKU_PRIMARY), pick_sku(available, SKU_P1)
        if not primary:
            raise SystemExit(f"No Business Basic or Business Premium SKU found. SKUs: {sorted(available)}")
        print(f"  primary SKU {primary['skuPartNumber']} ({free_seats(primary)} free of {primary['prepaidUnits']['enabled']})")
        # During the Business Premium trial every staff member gets a seat (realistic,
        # and fewer seat-holders). On the paid mix only the flagged four are licensed.
        on_trial = primary["skuPartNumber"] == "SPB"
        # consumedUnits lags behind assignLicense, so count seats ourselves
        free = free_seats(primary)
        for p in PEOPLE:
            license = p.license or ("primary" if on_trial else None)
            if not license:
                continue
            sku = (p1 or primary) if license == "p1" else primary
            try:
                if ensure_license(g, ids[p.alias], sku["skuId"]):
                    free -= sku is primary
                    print(f"  + {sku['skuPartNumber']} -> {p.alias}")
            except GraphError as exc:
                raise SystemExit(f"Could not license {p.alias}: {exc.message}\nAdd seats in the admin center and re-run.")

        # Fill the pool to 0 free seats so onboarding hits the spend-approval beat.
        # The mock Pax8 purchase frees the seat held by `seat-holder`.
        n = 1
        while free > 0 or n == 1:
            alias = "seat-holder" if n == 1 else f"seat-holder-{n}"
            holder = Person(alias, "Seat", "Holder" if n == 1 else f"Holder {n}", "Placeholder (do not use)", "IT")
            hid = ensure_user(g, holder, domain, passwords, enabled=False)["id"]
            # A seat freed by deleting a user (reset) can take a minute to show as available
            for attempt in range(6 if n == 1 else 1):
                try:
                    if ensure_license(g, hid, primary["skuId"]):
                        free -= 1
                        print(f"  + {primary['skuPartNumber']} -> {alias}")
                    break
                except GraphError as exc:
                    error = exc.message
                    time.sleep(15 if n == 1 else 0)
            else:
                print(f"  ! {alias} could not take a seat ({error}); the mock purchase needs one spare seat")
                break
            n += 1
        print(f"  {primary['skuPartNumber']}: 0 free seats")

        print("Groups")
        group_ids = {grp.alias: ensure_group(g, grp, ids)["id"] for grp in GROUPS}
        dl = g.first("/groups", {"$filter": f"mailNickname eq '{DL_ALIAS}'"})
        if not dl:
            print(f"  ! manual step: create distribution list '{DL_ALIAS}@{domain}' in the Exchange admin center and add Marcus")
        elif not any(m["id"] == ids["marcus"] for m in g.list(f"/groups/{dl['id']}/members", {"$select": "id"})):
            print(f"  ! manual step: add Marcus to the '{DL_ALIAS}' distribution list in the Exchange admin center")
        else:
            print(f"  = distribution list {DL_ALIAS} has Marcus")

        if files:
            print("Team site files")
            drive = group_drive(g, group_ids[TEAM_SITE_GROUP])
            for name, versions in FILES.items():
                ensure_versioned_file(g, drive["id"], name, versions)
    finally:
        if passwords:
            PASSWORDS_PATH.write_text(json.dumps(passwords, indent=2))
            PASSWORDS_PATH.chmod(0o600)
    return ids, group_ids


def main() -> None:
    g, env = app_graph()
    domain = env["M365_DOMAIN"]
    print(f"Seeding Northwind Parking in {domain}")
    run(g, env)
    print("\nDone. Manual steps left:")
    print(f"  - Sign in as dennis@{domain} on your phone and register Microsoft Authenticator")
    print(f"    (password in {PASSWORDS_PATH.name}).")
    print(f"  - Distribution list '{DL_ALIAS}' with Marcus, if flagged above.")


if __name__ == "__main__":
    main()
