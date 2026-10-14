"""Close-out (Tech.MD §4.8): what happens once every step has verified. The runner calls `close_out`.

  1. DoD probes (code, execution/dod.py) against live state; each is a done_check event. The DoD is every
     triaged intent's playbook's, so a two-intent plan is checked on both halves. A run with no playbook
     gets one built in code (no_playbook_dod): a probe per action that has one, and the reply.
  2. Sonnet drafts the reply and the PSA note (agents/closeout.py). A model error is an error event.
  3. The secret scanner checks each text against patterns and every secret the run holds (the TAP, the
     new user's password, link tokens). A hit blocks that text: message_blocked, with hit kinds and
     offsets, never the text. The reply also waits for the DoD: it isn't sent while a probe has failed.
     The PSA note is always written, because it is the technician's hand-off.
  4. Texts that pass are message_sent: written to our ticket view (no PSA or email yet, Week 5).
  5. The message_sent probe, now that the reply went out or didn't.
  6. A time entry: work type "AI Automation", the run's working minutes (time waiting on an approver excluded).

Returns the failed probes; the runner moves the run to DONE, or to ESCALATED with them.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from tech.agents.closeout import DRAFT_ERRORS, LINK_PLACEHOLDER, build_context, draft
from tech.agents.closeout import MODEL as CLOSEOUT_MODEL
from tech.core.models import Plan
from tech.core.store import RunState, Store, utcnow
from tech.execution import dod, links
from tech.graph.client import GraphClient
from tech.graph.writes import Secrets
from tech.playbooks import ClientFacts, Playbook, PlaybookLibrary
from tech.safety.scanner import scan

WORK_TYPE = "AI Automation"
WAITING = {RunState.AWAITING_APPROVAL.value, RunState.HELD.value}
PLACEHOLDER_RE = re.compile(r"\{\{\s*link\s*\}\}", re.IGNORECASE)


def with_link(body: str, has_link: bool) -> str:
    """Normalise the model's link placeholder; add a link line if it left one out."""
    body = PLACEHOLDER_RE.sub(LINK_PLACEHOLDER, body)
    if has_link and LINK_PLACEHOLDER not in body:
        body = f"{body.rstrip()}\n\nSign-in link (opens once): {LINK_PLACEHOLDER}"
    return body


def work_minutes(states: list[tuple[str, datetime]], end: datetime) -> int:
    """Minutes the run spent working, from its state changes: every interval except waiting on an approver."""
    seconds = 0.0
    ends = [at for _, at in states[1:]] + [end]
    for (state, start), stop in zip(states, ends, strict=True):
        if state not in WAITING:
            seconds += (links.as_utc(stop) - links.as_utc(start)).total_seconds()
    return max(1, math.ceil(seconds / 60))


def record(store: Store, run_id: int, kind: str, *, to: str | None, subject: str | None, text: str,
           known: set[str], held: str | None = None) -> bool:
    """Scan one outbound text, then record it as sent, or as blocked (by a secret, or held for the DoD)."""
    if hits := scan(f"{subject or ''}\n{text}", known=known):
        store.append_event(run_id, "message_blocked", {"kind": kind, "reason": "secret",
                                                       "hits": [h.payload() for h in hits]})
        return False
    if held:
        store.append_event(run_id, "message_blocked", {"kind": kind, "reason": "dod", "detail": held, "hits": []})
        return False
    store.append_event(run_id, "message_sent", {"kind": kind, "to": to, "subject": subject, "text": text})
    return True


def dod_playbook(playbooks: dict[str, Playbook]) -> Playbook | None:
    """The plan's playbook, with the DoD items of every other triaged intent's playbook added (each probe
    once). One plan can cover two intents (offboard Kevin, onboard his replacement); both halves are checked."""
    if not playbooks:
        return None
    items: dict[str, dict] = {}
    for p in playbooks.values():
        for item in p.definition_of_done:
            items.setdefault(next(iter(item)), item)
    first = next(iter(playbooks.values()))
    return first.model_copy(update={"definition_of_done": list(items.values())})


def no_playbook_dod(plan: Plan) -> Playbook | None:
    """The DoD of a run with no playbook (Tech.MD §4.9). Every step already verified on the way to CLOSING,
    so this adds what a verify can't show: the outcome probes and that the requester got a reply."""
    if not plan.steps:
        return None
    actions = list(dict.fromkeys(s.action for s in plan.steps))
    items = [{dod.NO_PLAYBOOK_PROBES[a]: {}} for a in actions if a in dod.NO_PLAYBOOK_PROBES]
    return Playbook(intent=plan.intent, goal="no playbook: the plan's own steps", must_investigate=[],
                    definition_of_done=[*items, {"message_sent": {"to": "$requester"}}], allowed_actions=actions)


async def close_out(store: Store, graph: GraphClient, run_id: int, plan: Plan, *, facts: ClientFacts,
                    library: PlaybookLibrary, secrets: Secrets, model: Any = CLOSEOUT_MODEL,
                    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                    definition: Playbook | None = None, note: str | None = None) -> list[str]:
    """Run the close-out for an executed run (state CLOSING). Returns the names of failed DoD probes.
    `definition` replaces the playbook's definition of done, and `note` is a technician's note for the PSA note: a
    run a technician finished in the console passes both (runner.technician_close, Build-Day20)."""
    triage = store.last_event(run_id, "triage").payload["triage"]
    requester = {"name": triage["requester"]["name"], "email": triage["requester"]["email"]}
    playbook = (definition or dod_playbook(library.for_intents([plan.intent, *triage["intents"]]))
                or no_playbook_dod(plan))
    ctx = dod.DodContext(store, graph, run_id, plan, facts, requester["email"], sleep=sleep)

    probes = await dod.check(ctx, playbook, exclude=("message_sent",)) if playbook else []
    for p in probes:
        store.append_event(run_id, "done_check", p.payload())

    run_links = links.for_run(store, run_id)
    known = secrets.known() | {row.token for row in run_links}
    has_link = any(row.to.casefold() == requester["email"].casefold() for row in run_links)
    try:
        context = build_context(store, run_id, plan, probes, requester=requester, playbook=playbook)
        if note:
            context["technician_note"] = note
        drafts = await draft(context, model=model)
    except DRAFT_ERRORS as exc:
        store.append_event(run_id, "error", {"where": "closeout", "error": f"{type(exc).__name__}: {exc}"})
        drafts = None

    if drafts is not None:
        record(store, run_id, "reply", to=requester["email"], subject=drafts.reply_subject,
               text=with_link(drafts.reply_body, has_link), known=known,
               held=None if all(p.ok for p in probes) else "the definition of done isn't met")
        record(store, run_id, "psa_note", to=None, subject=None, text=drafts.psa_note, known=known)

    if playbook:
        for p in await dod.check(ctx, playbook, only=("message_sent",)):
            probes.append(p)
            store.append_event(run_id, "done_check", p.payload())

    states = [(e.payload["to"], e.created_at) for e in store.events_of(run_id, "state")]
    end = utcnow()
    store.append_event(run_id, "time_entry", {
        "work_type": WORK_TYPE, "minutes": work_minutes(states, end),
        "started_at": links.as_utc(states[0][1]).isoformat(), "ended_at": end.isoformat(),
    })
    return [p.probe for p in probes if not p.ok]


def view(store: Store, run_id: int, link_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The close-out as the workspace shows it. The reply's link placeholder becomes the link's URL while it's
    unviewed, so the token is only ever in the served snapshot, never in an event."""
    triage = store.last_event(run_id, "triage")
    to = triage.payload["triage"]["requester"]["email"].casefold() if triage else ""
    link = next((row for row in link_rows if row["to"].casefold() == to), None)
    fill = (link["url"] or "(the one-time link, already opened)") if link else "(no link)"
    entry = store.last_event(run_id, "time_entry")
    return {
        "checks": [e.payload for e in store.events_of(run_id, "done_check")],
        "messages": [e.payload | {"text": e.payload["text"].replace(LINK_PLACEHOLDER, fill)}
                     for e in store.events_of(run_id, "message_sent")],
        "blocked": [e.payload for e in store.events_of(run_id, "message_blocked")],
        "time_entry": entry.payload if entry else None,
    }
