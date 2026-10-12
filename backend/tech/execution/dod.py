# backend/tech/execution/dod.py
"""Definition-of-done probes (Tech.MD §4.8): after execution, code re-checks the playbook's DoD against live state.

A playbook DoD item is `{probe: args}`. The `$` expressions in args ($new_user.upn, $plan.mirrored_groups...)
document intent; they aren't evaluated. Each probe derives its targets from the run itself (the user.create
step's output, the plan's group.addMember steps, the investigation's memberOf reads), so a playbook can't
point a probe at something the run didn't do. Only `$client.<field>` is resolved, from the client facts.

A probe never raises: a Graph error, or a DoD item with no probe, is a failed check.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from typing import Any

from tech.core.models import Plan
from tech.core.store import Store
from tech.execution import links, scheduler
from tech.graph.client import GraphClient, GraphError
from tech.graph.writes import get_or_none, license_skus, member_group_ids, same
from tech.playbooks import ClientFacts, Playbook

NO_USER = "no user.create step verified in this run"
INACTIVE_TAP = {"Expired", "DisabledByPolicy", "OneTimeUsed"}


@dataclass(frozen=True)
class Probe:
    probe: str
    ok: bool
    detail: str

    def payload(self) -> dict[str, Any]:
        return {"probe": self.probe, "ok": self.ok, "detail": self.detail}


@dataclass
class DodContext:
    store: Store
    graph: GraphClient
    run_id: int
    plan: Plan
    facts: ClientFacts
    requester: str

    def new_user(self) -> dict[str, Any] | None:
        """The user.create step's output in this run: {id, userPrincipalName}."""
        for event in self.store.events_of(self.run_id, "step_verified"):
            if event.payload["action"] == "user.create":
                return event.payload.get("output") or None
        return None

    def mirrored_groups(self) -> dict[str, str]:
        """groupId (lowercase) -> name, for every group.addMember step in the plan."""
        return {s.params.groupId.lower(): s.params.groupName or s.params.groupId
                for s in self.plan.steps if s.action == "group.addMember"}

    def planned_skus(self) -> set[str]:
        return {s.params.skuId.lower() for s in self.plan.steps
                if s.action == "license.assign"
                or (s.action == "followup.schedule" and s.params.kind == "license.assign")}

    def peer_groups(self) -> set[str] | None:
        """Display names (casefolded) from the investigation's memberOf reads; None if it made none."""
        reads = [e.payload for e in self.store.events_of(self.run_id, "tool_call")
                 if e.payload["tool"] == "users.memberOf" and e.payload["ok"]]
        if not reads:
            return None
        return {(g.get("displayName") or "").casefold() for r in reads for g in r["result"] or []}

    def leaver(self) -> str | None:
        """The leaver's id: the subject of the offboard writes (disable, revoke, mailbox...). The user
        isn't created in this run, so it's taken from the step params, not a step_verified output."""
        order = ["user.update", "sessions.revoke", "mailbox.autoReply", "mailbox.forwardRule",
                 "group.removeMember"]
        for action in order:
            for step in self.plan.steps:
                if step.action == action:
                    return step.params.userId
        return None

    def removed_groups(self) -> dict[str, str]:
        """groupId (lowercase) -> name, for every group.removeMember step in the plan."""
        return {s.params.groupId.lower(): s.params.groupName or s.params.groupId
                for s in self.plan.steps if s.action == "group.removeMember"}

    def owned_to_transfer(self) -> dict[str, str]:
        """groupId (lowercase) -> new owner id, for every group.addOwner step (ownership transfer)."""
        return {s.params.groupId.lower(): s.params.userId for s in self.plan.steps if s.action == "group.addOwner"}


Check = Callable[[DodContext, dict[str, Any]], Awaitable[Probe]]
PROBES: dict[str, Check] = {}


def probe(name: str) -> Callable[[Check], Check]:
    def register(fn: Check) -> Check:
        PROBES[name] = fn
        return fn
    return register


@probe("user_exists")
async def user_exists(ctx: DodContext, args: dict[str, Any]) -> Probe:
    if (user := ctx.new_user()) is None:
        return Probe("user_exists", False, NO_USER)
    found = await get_or_none(ctx.graph, f"/users/{user['id']}", params={"$select": "id,userPrincipalName"})
    if found is None:
        return Probe("user_exists", False, f"{user['userPrincipalName']} is not in the directory")
    return Probe("user_exists", True, found["userPrincipalName"])


@probe("user_property")
async def user_property(ctx: DodContext, args: dict[str, Any]) -> Probe:
    if (user := ctx.new_user()) is None:
        return Probe("user_property", False, NO_USER)
    want = {k: getattr(ctx.facts, v.removeprefix("$client."), None)
            if isinstance(v, str) and v.startswith("$client.") else v for k, v in args.items()}
    found = await get_or_none(ctx.graph, f"/users/{user['id']}", params={"$select": ",".join(["id", *want])})
    if found is None:
        return Probe("user_property", False, f"{user['userPrincipalName']} is not in the directory")
    wrong = [f"{k} is {found.get(k)!r}, expected {v!r}" for k, v in want.items() if not same(v, found.get(k))]
    return Probe("user_property", not wrong, "; ".join(wrong) or ", ".join(f"{k}={v}" for k, v in want.items()))


@probe("user_has_sku")
async def user_has_sku(ctx: DodContext, args: dict[str, Any]) -> Probe:
    if (user := ctx.new_user()) is None:
        return Probe("user_has_sku", False, NO_USER)
    if not (skus := ctx.planned_skus()):
        return Probe("user_has_sku", False, "no license: the plan assigns none and schedules none")
    if await license_skus(ctx.graph, user["id"]) & skus:
        return Probe("user_has_sku", True, "has the license")
    later = [f for f in scheduler.for_run(ctx.store, ctx.run_id)
             if f.kind == "license.assign" and f.status == "scheduled"]
    if later:
        return Probe("user_has_sku", True, f"license follow-up scheduled for {later[0].run_at:%Y-%m-%d %H:%M} UTC")
    return Probe("user_has_sku", False, "no license, and no license follow-up is scheduled")


@probe("user_in_groups")
async def user_in_groups(ctx: DodContext, args: dict[str, Any]) -> Probe:
    if (user := ctx.new_user()) is None:
        return Probe("user_in_groups", False, NO_USER)
    if not (want := ctx.mirrored_groups()):
        return Probe("user_in_groups", True, "the plan mirrors no groups")
    have = await member_group_ids(ctx.graph, user["id"])
    missing = [f"{name} ({gid})" if name.lower() != gid else gid for gid, name in want.items() if gid not in have]
    return Probe("user_in_groups", not missing, f"not in {', '.join(missing)}" if missing else f"in all {len(want)}")


@probe("tap_active")
async def tap_active(ctx: DodContext, args: dict[str, Any]) -> Probe:
    if (user := ctx.new_user()) is None:
        return Probe("tap_active", False, NO_USER)
    taps = [t async for t in ctx.graph.list(f"/users/{user['id']}/authentication/temporaryAccessPassMethods")]
    live = [t for t in taps if t.get("methodUsabilityReason") not in INACTIVE_TAP]
    if not live:
        return Probe("tap_active", False, f"no usable TAP ({len(taps)} found)")
    return Probe("tap_active", True, f"TAP {live[0].get('methodUsabilityReason') or 'present'}")


@probe("message_sent")
async def message_sent(ctx: DodContext, args: dict[str, Any]) -> Probe:
    approval = ctx.store.last_event(ctx.run_id, "approval")
    allowed = {ctx.requester.casefold()} | ({approval.payload["by"].casefold()} if approval else set())
    problems = []
    if not any(link.to.casefold() in allowed for link in links.for_run(ctx.store, ctx.run_id)):
        problems.append(f"no one-time link to {ctx.requester} or the approver")
    if not any(e.payload["kind"] == "reply" for e in ctx.store.events_of(ctx.run_id, "message_sent")):
        problems.append("no reply was sent")
    return Probe("message_sent", not problems, "; ".join(problems) or f"reply and one-time link to {ctx.requester}")


@probe("needs_technician_listed")
async def needs_technician_listed(ctx: DodContext, args: dict[str, Any]) -> Probe:
    if (peer := ctx.peer_groups()) is None:
        return Probe("needs_technician_listed", True, "the investigation didn't read the peer's groups")
    text = " ".join(f"{t.item} {t.reason}" for t in ctx.plan.needs_technician).casefold()
    due = [g for g in ctx.facts.group_handling if g.handling == "technician" and g.group.casefold() in peer]
    missing = [g.group for g in due if not any(n.casefold() in text for n in (g.group, *g.aka))]
    if missing:
        return Probe("needs_technician_listed", False, f"not handed to a technician: {', '.join(missing)}")
    return Probe("needs_technician_listed", True, ", ".join(g.group for g in due) or "nothing outside Graph")


# ------------------------------------------------------------------ offboarding probes (Tech.MD §6.2)

NO_LEAVER = "no offboarding step names the leaver in this run"


@probe("account_disabled")
async def account_disabled(ctx: DodContext, args: dict[str, Any]) -> Probe:
    if (leaver := ctx.leaver()) is None:
        return Probe("account_disabled", False, NO_LEAVER)
    user = await get_or_none(ctx.graph, f"/users/{leaver}", params={"$select": "id,accountEnabled"})
    if user is None:
        return Probe("account_disabled", False, "the leaver is not in the directory")
    return Probe("account_disabled", user.get("accountEnabled") is False,
                 "sign-in disabled" if user.get("accountEnabled") is False else "the account is still enabled")


@probe("sessions_revoked")
async def sessions_revoked(ctx: DodContext, args: dict[str, Any]) -> Probe:
    if (leaver := ctx.leaver()) is None:
        return Probe("sessions_revoked", False, NO_LEAVER)
    user = await get_or_none(ctx.graph, f"/users/{leaver}", params={"$select": "id,signInSessionsValidFromDateTime"})
    if user is None:
        return Probe("sessions_revoked", False, "the leaver is not in the directory")
    when = user.get("signInSessionsValidFromDateTime")
    return Probe("sessions_revoked", when is not None,
                 f"sessions revoked at {when}" if when else "sessions were never revoked")


@probe("auto_reply_set")
async def auto_reply_set(ctx: DodContext, args: dict[str, Any]) -> Probe:
    if not any(s.action == "mailbox.autoReply" for s in ctx.plan.steps):
        return Probe("auto_reply_set", True, "no auto-reply was requested")
    if (leaver := ctx.leaver()) is None:
        return Probe("auto_reply_set", False, NO_LEAVER)
    settings = await get_or_none(ctx.graph, f"/users/{leaver}/mailboxSettings")
    auto = (settings or {}).get("automaticRepliesSetting") or {}
    on = auto.get("status") in {"scheduled", "alwaysEnabled"}
    return Probe("auto_reply_set", on, f"auto-reply {auto.get('status')}" if on else "auto-reply is off")


@probe("forward_rule_active")
async def forward_rule_active(ctx: DodContext, args: dict[str, Any]) -> Probe:
    if not any(s.action == "mailbox.forwardRule" for s in ctx.plan.steps):
        return Probe("forward_rule_active", True, "no forwarding was requested")
    if (leaver := ctx.leaver()) is None:
        return Probe("forward_rule_active", False, NO_LEAVER)
    rules = [r async for r in ctx.graph.list(f"/users/{leaver}/mailFolders/inbox/messageRules")]
    forwards = [t["emailAddress"]["address"] for r in rules
                for t in (r.get("actions") or {}).get("forwardTo") or []]
    return Probe("forward_rule_active", bool(forwards),
                 f"forwards to {', '.join(forwards)}" if forwards else "no forwarding rule is active")


@probe("removed_from_groups")
async def removed_from_groups(ctx: DodContext, args: dict[str, Any]) -> Probe:
    if (leaver := ctx.leaver()) is None:
        return Probe("removed_from_groups", False, NO_LEAVER)
    if not (want := ctx.removed_groups()):
        return Probe("removed_from_groups", True, "the plan removes no groups")
    still_in = await member_group_ids(ctx.graph, leaver)
    bad = [name for gid, name in want.items() if gid in still_in]
    return Probe("removed_from_groups", not bad,
                 f"still in {', '.join(bad)}" if bad else f"removed from all {len(want)}")


@probe("ownership_transferred")
async def ownership_transferred(ctx: DodContext, args: dict[str, Any]) -> Probe:
    if not (want := ctx.owned_to_transfer()):
        return Probe("ownership_transferred", True, "the plan transfers no ownership")
    missing = []
    for gid, new_owner in want.items():
        owners = {o["id"].lower() async for o in ctx.graph.list(f"/groups/{gid}/owners", {"$select": "id"})}
        if new_owner.lower() not in owners:
            missing.append(gid)
    return Probe("ownership_transferred", not missing,
                 f"new owner missing on {', '.join(missing)}" if missing else f"transferred {len(want)}")


@probe("followup_scheduled")
async def followup_scheduled(ctx: DodContext, args: dict[str, Any]) -> Probe:
    want = args.get("type", "license_review")
    kinds = {"license_review": "license.review", "verify_new_method": "mfa.verifyNewMethod"}
    kind = kinds.get(want, want)
    due = [f for f in scheduler.for_run(ctx.store, ctx.run_id) if f.kind == kind and f.status == "scheduled"]
    return Probe("followup_scheduled", bool(due),
                 f"{kind} scheduled for {due[0].run_at:%Y-%m-%d}" if due else f"no {kind} follow-up scheduled")


async def check(ctx: DodContext, playbook: Playbook, *, only: Collection[str] | None = None,
                exclude: Collection[str] = ()) -> list[Probe]:
    """Run the playbook's DoD items in order (optionally a subset). Never raises on Graph errors."""
    results = []
    for item in playbook.definition_of_done:
        name, args = next(iter(item.items()))
        if (only is not None and name not in only) or name in exclude:
            continue
        if (fn := PROBES.get(name)) is None:
            results.append(Probe(name, False, f"no probe for {name!r}"))
            continue
        try:
            results.append(await fn(ctx, args or {}))
        except GraphError as exc:
            results.append(Probe(name, False, f"Graph {exc.status} {exc.code}: {exc.message}"))
    return results
