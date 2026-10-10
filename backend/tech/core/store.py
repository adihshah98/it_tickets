"""Run store: tickets, runs and the append-only event log (Tech.MD §3).

Every state transition and every thing the UI shows is an event. The same events are the
audit trail and, later, the replay log. Events are numbered per run (seq 1, 2, 3...) so a
client can resume a stream from the last seq it saw.

Approval, link and follow-up tables arrive with the days that need them.
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
    "step_started", "step_verified", "step_failed", "replan", "done_check", "message_sent", "error",
    "playbook_drafted", "playbook_approved",
})


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

    def _session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

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
                raise KeyError(f"run {run_id} not found")
            return run

    def set_state(self, run_id: int, state: RunState, **detail: Any) -> Event:
        with self._session() as s:
            run = s.get(Run, run_id)
            if run is None:
                raise KeyError(f"run {run_id} not found")
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
