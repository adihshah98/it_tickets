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
