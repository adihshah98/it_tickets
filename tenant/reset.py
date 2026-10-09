# /// script
# requires-python = ">=3.11"
# dependencies = ["azure-identity>=1.17", "httpx>=0.27", "openpyxl>=3.1"]
# ///
"""Reset the Northwind Parking demo tenant to its seeded state (Tech.MD §10).

    uv run tenant/reset.py --dry-run        # show what would change
    uv run tenant/reset.py                  # quick rehearsal: everything, files with all 3 versions now
    uv run tenant/reset.py --files base     # evening before a demo: files get only yesterday's 2 versions
    uv run tenant/reset.py --files overwrite  # morning of the demo: add only this morning's overwrite

What it undoes:
  - users the demo created (e.g. Priya), soft-deleted (Entra recycle bin, 30 days)
  - offboarding: re-enables accounts, restores group memberships and ownership
  - auto-replies and inbox rules on staff mailboxes
  - Temporary Access Passes on staff
  - the spare seat: back to `seat-holder` so the pool shows 0 free
  - versions on the team-site spreadsheets
Then checks Dennis still has Authenticator (re-register by hand if the MFA demo removed it).

Never touches users created before the seed (your admin account) or names in RESET_KEEP
(comma-separated UPNs in tenant/.env).
"""

from __future__ import annotations

import argparse
import time
from urllib.parse import quote

import seed
from graph import Graph, GraphError, app_graph

DRY = False


def act(desc: str, fn, *args, **kw) -> None:
    print(f"  {'would ' if DRY else ''}{desc}")
    if not DRY:
        fn(*args, **kw)


def roster_upns(domain: str) -> set[str]:
    return {f"{p.alias}@{domain}".lower() for p in [*seed.PEOPLE, seed.SERVICE_ACCOUNT]}


def delete_demo_users(g: Graph, domain: str, keep: set[str]) -> None:
    users = list(g.list("/users", {"$select": "id,userPrincipalName,displayName,createdDateTime"}))
    roster = roster_upns(domain)
    seeded = [u["createdDateTime"] for u in users if u["userPrincipalName"].lower() in roster]
    if not seeded:
        raise SystemExit("No seeded users found. Run seed.py first.")
    baseline = min(seeded)  # anyone created before the seed (your admin) is never touched
    for u in users:
        upn = u["userPrincipalName"].lower()
        if upn in roster or upn in keep or upn.startswith("seat-holder") or u["createdDateTime"] < baseline:
            continue
        act(f"delete {u['userPrincipalName']} ({u['displayName']})", g.request, "DELETE", f"/users/{u['id']}")


def sync_groups(g: Graph, ids: dict[str, str], group_ids: dict[str, str]) -> None:
    """Seed only adds; remove anyone the demo added and restore owners (offboarding hands Kevin's groups to Taylor)."""
    for grp in seed.GROUPS:
        gid = group_ids[grp.alias]
        for rel, aliases in (("owners", grp.owners), ("members", grp.members)):
            want = {ids[a] for a in aliases}
            for obj in g.list(f"/groups/{gid}/{rel}", {"$select": "id,displayName"}):
                if obj["id"] not in want:
                    act(f"remove {obj.get('displayName')} from {grp.name} {rel}",
                        g.request, "DELETE", f"/groups/{gid}/{rel}/{obj['id']}/$ref")


def reset_mailboxes(g: Graph, ids: dict[str, str]) -> None:
    for p in seed.PEOPLE:
        uid = ids[p.alias]
        try:
            settings = g.get(f"/users/{uid}/mailboxSettings", params={"$select": "automaticRepliesSetting"})
        except GraphError as exc:
            if exc.status in (400, 404) or "Mailbox" in exc.code:
                continue  # unlicensed or no mailbox yet
            raise
        if settings["automaticRepliesSetting"]["status"] != "disabled":
            act(f"turn off auto-reply for {p.alias}", g.patch, f"/users/{uid}/mailboxSettings",
                {"automaticRepliesSetting": {"status": "disabled"}})
        for rule in g.list(f"/users/{uid}/mailFolders/inbox/messageRules"):
            act(f"delete inbox rule '{rule['displayName']}' for {p.alias}",
                g.request, "DELETE", f"/users/{uid}/mailFolders/inbox/messageRules/{rule['id']}")


def clear_taps(g: Graph, ids: dict[str, str]) -> None:
    for p in seed.PEOPLE:
        for tap in g.list(f"/users/{ids[p.alias]}/authentication/temporaryAccessPassMethods"):
            act(f"delete Temporary Access Pass for {p.alias}", g.request, "DELETE",
                f"/users/{ids[p.alias]}/authentication/temporaryAccessPassMethods/{tap['id']}")


def reset_files(g: Graph, group_ids: dict[str, str], stage: str) -> None:
    drive_id = seed.group_drive(g, group_ids[seed.TEAM_SITE_GROUP])["id"]
    for name, versions in seed.FILES.items():
        path = f"/drives/{drive_id}/root:/{quote(name)}"
        if stage == "overwrite":
            item = g.get(path)
            count = len(g.get(f"/drives/{drive_id}/items/{item['id']}/versions")["value"])
            if count != 2:
                print(f"  ! {name} has {count} versions, expected 2 from `--files base`; skipping")
                continue
            act(f"upload this morning's overwrite of {name}", g.request, "PUT", f"{path}:/content",
                content=versions[-1], headers={"Content-Type": "application/octet-stream"})
            continue
        try:
            item = g.get(path)
            act(f"delete {name} (site recycle bin)", g.request, "DELETE", f"/drives/{drive_id}/items/{item['id']}")
        except GraphError as exc:
            if exc.status != 404:
                raise
        upload = versions if stage == "all" else versions[:-1]
        if DRY:
            print(f"  would upload {name} with {len(upload)} versions")
            continue
        for data in upload:
            g.request("PUT", f"{path}:/content", content=data, headers={"Content-Type": "application/octet-stream"})
            time.sleep(2)
        print(f"  uploaded {name} with {len(upload)} versions")


def check_dennis(g: Graph, ids: dict[str, str]) -> None:
    methods = g.get(f"/users/{ids['dennis']}/authentication/methods")["value"]
    if any(m["@odata.type"].endswith("microsoftAuthenticatorAuthenticationMethod") for m in methods):
        print("  = Dennis has Microsoft Authenticator")
    else:
        print("  ! Dennis has no Authenticator: re-register it at https://aka.ms/mysecurityinfo before the MFA demo")


def main() -> None:
    global DRY
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print what would change, change nothing")
    parser.add_argument("--files", choices=["all", "base", "overwrite"], default="all",
                        help="all: 3 versions now · base: 2 versions (run the evening before) · overwrite: add the 3rd only (morning of)")
    args = parser.parse_args()
    DRY = args.dry_run

    g, env = app_graph()
    domain = env["M365_DOMAIN"]
    keep = {u.strip().lower() for u in env.get("RESET_KEEP", "").split(",") if u.strip()}
    print(f"Resetting Northwind Parking in {domain}{' (dry run)' if DRY else ''}")

    print("Demo-created users")
    delete_demo_users(g, domain, keep)

    if DRY:
        print("Seed state (accounts, managers, licenses, seat-holder, group members)")
        print("  would re-run seed.py's idempotent steps")
        ids = {p.alias: g.get(f"/users/{quote(p.alias + '@' + domain)}", params={"$select": "id"})["id"]
               for p in seed.PEOPLE}
        group_ids = {grp.alias: g.first("/groups", {"$filter": f"mailNickname eq '{grp.alias}'"})["id"]
                     for grp in seed.GROUPS}
    else:
        print("Seed state (accounts, managers, licenses, seat-holder, group members)")
        ids, group_ids = seed.run(g, env, files=False, warn_priya=False)  # just deleted; directory may lag

    print("Group members and owners")
    sync_groups(g, ids, group_ids)
    print("Mailboxes")
    reset_mailboxes(g, ids)
    print("Temporary Access Passes")
    clear_taps(g, ids)
    print(f"Team site files ({args.files})")
    reset_files(g, group_ids, args.files)
    print("Dennis")
    check_dennis(g, ids)

    # TODO once the app exists: delete the learned file.restore_version playbook and its
    # autonomy setting, and clear runs, so the "no playbook" beat starts fresh.
    print("\nDone." + (" Nothing was changed." if DRY else " Not yet reset: app-side playbooks and runs (no app yet)."))


if __name__ == "__main__":
    main()
