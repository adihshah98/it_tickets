"""Run store: tickets, runs and the append-only event log (Tech.MD §3).

Every state transition and every thing the UI shows is an event. The same events are the
audit trail and, later, the replay log. Events are numbered per run (seq 1, 2, 3...) so a
client can resume a stream from the last seq it saw.

Links (one-time credential links), follow-ups (scheduled work), playbook versions and per-client
autonomy overrides have their own tables; their modules (execution/links.py, execution/scheduler.py,
playbooks/library.py) own the logic.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint, func
from sqlalchemy.pool import StaticPool
from sqlmodel import Field, Session, SQLModel, create_engine, select


class RunState(StrEnum):
    RECEIVED = "RECEIVED"
    TRIAGED = "TRIAGED"
    INVESTIGATING = "INVESTIGATING"
    PLAN_PROPOSED = "PLAN_PROPOSED"
    PLAN_CHECKED = "PLAN_CHECKED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    REPLANNING = "REPLANNING"
    CLOSING = "CLOSING"
    DONE = "DONE"
    ESCALATED = "ESCALATED"
    HELD = "HELD"


EVENT_TYPES = frozenset({
    "state", "triage", "tool_call", "finding", "plan", "lint_result", "gate_decision", "approval",
    "step_started", "step_retry", "step_verified", "step_failed", "replan", "done_check", "message_sent", "error",
    "playbook_drafted", "playbook_approved", "playbook_demoted", "audit_observed", "link_viewed", "message_blocked",
    "time_entry", "oob_confirmed",
})


class NotFound(KeyError):
    """No such run, ticket, link or follow-up. A KeyError, so callers that catch KeyError still work."""


def utcnow() -> datetime:
    return datetime.now(UTC)


class Ticket(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    text: str
    from_address: str
    created_at: datetime = Field(default_factory=utcnow)


class Run(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    ticket_id: int = Field(foreign_key="ticket.id", index=True)
    state: RunState = RunState.RECEIVED
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Event(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("run_id", "seq"),)

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id", index=True)
    seq: int
    type: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)


class Link(SQLModel, table=True):
    """A one-time link carrying a TAP, encrypted. The token is the URL's secret part."""

    __table_args__ = (UniqueConstraint("run_id", "step_id"),)

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id", index=True)
    step_id: str
    token: str = Field(index=True, unique=True)
    ciphertext: str
    user_id: str
    to: str
    includes: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    note: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    viewed_at: datetime | None = None


class Followup(SQLModel, table=True):
    """Work scheduled for later by a followup.schedule step, e.g. assigning a license on the start date."""

    __table_args__ = (UniqueConstraint("run_id", "step_id"),)

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id", index=True)
    step_id: str
    kind: str
    run_at: datetime
    params: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    status: str = "scheduled"  # scheduled | done | failed
    created_at: datetime = Field(default_factory=utcnow)
    done_at: datetime | None = None


class PlaybookVersion(SQLModel, table=True):
    """One version of one playbook. Shipped and learned playbooks are the same thing: the newest approved
    version of an intent is the one runs use. `yaml` is the text the investigator sees, comments included."""

    __table_args__ = (UniqueConstraint("intent", "version"),)

    id: int | None = Field(default=None, primary_key=True)
    intent: str = Field(index=True)
    version: int
    yaml: str
    content_hash: str
    status: str = "approved"  # draft | approved | retired
    source: str = "shipped"  # shipped | agent_run | technician_run | written | audit_log
    source_run_id: int | None = None
    approved_by: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ClientAutonomy(SQLModel, table=True):
    """A per-intent autonomy level set in the product, overriding client_facts.autonomy."""

    intent: str = Field(primary_key=True)
    level: str  # A1 | A2 | A3
    set_by: str | None = None
    updated_at: datetime = Field(default_factory=utcnow)


class EventBus:
    """In-process pub/sub so SSE clients see events as they are appended."""

    def __init__(self) -> None:
        self._subs: dict[int, set[asyncio.Queue[Event]]] = defaultdict(set)

    def subscribe(self, run_id: int) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subs[run_id].add(queue)
        return queue

    def unsubscribe(self, run_id: int, queue: asyncio.Queue[Event]) -> None:
        self._subs[run_id].discard(queue)
        if not self._subs[run_id]:
            del self._subs[run_id]

    def publish(self, event: Event) -> None:
        for queue in self._subs.get(event.run_id, ()):
            queue.put_nowait(event)

    def subscriber_count(self, run_id: int) -> int:
        return len(self._subs.get(run_id, ()))


class Store:
    def __init__(self, url: str, bus: EventBus | None = None):
        if url.startswith("sqlite"):
            kw: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
            if url in ("sqlite://", "sqlite:///:memory:"):
                kw["poolclass"] = StaticPool  # one shared in-memory database
            self.engine = create_engine(url, **kw)
        else:
            self.engine = create_engine(url)
        SQLModel.metadata.create_all(self.engine)
        self.bus = bus or EventBus()

    def session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    _session = session

    # -------------------------------------------------------------- tickets and runs

    def create_ticket(self, text: str, from_address: str) -> Ticket:
        with self._session() as s:
            ticket = Ticket(text=text, from_address=from_address)
            s.add(ticket)
            s.commit()
            return ticket

    def create_run(self, ticket_id: int) -> Run:
        with self._session() as s:
            run = Run(ticket_id=ticket_id)
            s.add(run)
            s.commit()
        self.append_event(run.id, "state", {"from": None, "to": RunState.RECEIVED.value})
        return run

    def get_run(self, run_id: int) -> Run:
        with self._session() as s:
            run = s.get(Run, run_id)
            if run is None:
                raise NotFound(f"run {run_id} not found")
            return run

    def set_state(self, run_id: int, state: RunState, **detail: Any) -> Event:
        with self._session() as s:
            run = s.get(Run, run_id)
            if run is None:
                raise NotFound(f"run {run_id} not found")
            previous = run.state
            run.state = state
            run.updated_at = utcnow()
            s.add(run)
            s.commit()
        return self.append_event(run_id, "state", {"from": previous.value, "to": state.value, **detail})

    # -------------------------------------------------------------- events

    def append_event(self, run_id: int, type: str, payload: dict[str, Any]) -> Event:
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {type!r}")
        json.dumps(payload)  # raises TypeError now rather than at commit or in the UI
        with self._session() as s:
            last = s.exec(select(func.max(Event.seq)).where(Event.run_id == run_id)).one()
            event = Event(run_id=run_id, seq=(last or 0) + 1, type=type, payload=payload)
            s.add(event)
            s.commit()
        self.bus.publish(event)
        return event

    def events(self, run_id: int, after_seq: int = 0) -> list[Event]:
        with self._session() as s:
            query = select(Event).where(Event.run_id == run_id, Event.seq > after_seq).order_by(Event.seq)
            return list(s.exec(query))

    def events_of(self, run_id: int, type: str) -> list[Event]:
        with self._session() as s:
            query = select(Event).where(Event.run_id == run_id, Event.type == type).order_by(Event.seq)
            return list(s.exec(query))

    def last_event(self, run_id: int, type: str) -> Event | None:
        events = self.events_of(run_id, type)
        return events[-1] if events else None

    def list_runs(self, limit: int = 50) -> list[tuple[Run, Ticket]]:
        """Most recent runs first, each with its ticket."""
        with self._session() as s:
            query = select(Run, Ticket).join(Ticket, Run.ticket_id == Ticket.id).order_by(Run.id.desc()).limit(limit)
            return list(s.exec(query))

    def get_ticket(self, ticket_id: int) -> Ticket:
        with self._session() as s:
            ticket = s.get(Ticket, ticket_id)
            if ticket is None:
                raise NotFound(f"ticket {ticket_id} not found")
            return ticket

    async def stream(self, run_id: int, after_seq: int = 0) -> AsyncIterator[Event]:
        """History after `after_seq`, then live events. Subscribes before replaying, so nothing
        appended in between is lost, and drops anything already replayed."""
        queue = self.bus.subscribe(run_id)
        try:
            last = after_seq
            for event in self.events(run_id, after_seq):
                last = event.seq
                yield event
            while True:
                event = await queue.get()
                if event.seq > last:
                    last = event.seq
                    yield event
        finally:
            self.bus.unsubscribe(run_id, queue)
