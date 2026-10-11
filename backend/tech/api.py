"""HTTP API for the workspace UI: REST + server-sent events (Tech.MD §2).

    uv run uvicorn tech.api:create_app --factory --port 8000 --no-access-log

    POST /tickets                    start a run (triage, investigation, gate) in the background
    GET  /runs                       recent runs, for the inbox
    GET  /runs/{id}                  snapshot: the run as the workspace shows it, its close-out, plus `last_seq`
    GET  /runs/{id}/events           SSE: history after Last-Event-ID (or ?after=), then live
    POST /runs/{id}/approval         approve (with confirm answers) or reject; executes in the background
    POST /runs/{id}/resume           carry on an execution a dead process left in EXECUTING
    POST /followups/{id}/run-now     run a scheduled follow-up now (the demo's license assignment)
    GET  /l/{token}                  one-time link: who it's for and when it expires, never the TAP
    POST /l/{token}/reveal           one-time link: the TAP, once

Requests that start work answer 202 at once; the work runs as a tracked task and shows up on the event
stream. Checks that can fail (wrong state, not an approver, bad answers) run before the 202, so the
caller gets a 409 rather than an event. A task that crashes escalates its run (kind "error") instead of
leaving it stuck mid-state.

The event bus is in-process: a run driven by the CLI replays here, but its live events reach only the
CLI's own process.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any, Literal

import logfire
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from tech import closeout, runner
from tech.core.config import Settings
from tech.core.store import Event, NotFound, RunState, Store
from tech.execution import links, scheduler

TERMINAL = {s.value for s in runner.TERMINAL}
DEFAULT_WEB_ORIGIN = "http://localhost:3000"


# ------------------------------------------------------------------ background work


class TaskRunner:
    """Runs long work (agents, execution) as asyncio tasks, keyed by run, and escalates a run whose task
    crashes. `drain` waits for everything (tests); `cancel` stops everything (shutdown)."""

    def __init__(self, store: Store):
        self.store = store
        self._tasks: dict[asyncio.Task[None], int] = {}

    def spawn(self, run_id: int, work: Coroutine[Any, Any, Any], *, what: str, escalate: bool = True) -> None:
        task = asyncio.create_task(self._guard(run_id, work, what, escalate))
        self._tasks[task] = run_id
        task.add_done_callback(self._tasks.pop)

    def busy(self, run_id: int) -> bool:
        return run_id in self._tasks.values()

    async def _guard(self, run_id: int, work: Coroutine[Any, Any, Any], what: str, escalate: bool) -> None:
        try:
            await work
        except Exception as exc:
            logfire.exception("{what} failed for run {run_id}", what=what, run_id=run_id)
            error = f"{type(exc).__name__}: {exc}"
            self.store.append_event(run_id, "error", {"where": what, "error": error})
            if escalate and self.store.get_run(run_id).state.value not in TERMINAL:
                runner.transition(self.store, run_id, RunState.ESCALATED, kind="error", reason=f"{what}: {error}",
                                  findings=runner.findings(self.store, run_id))

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def cancel(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await self.drain()


# ------------------------------------------------------------------ event stream


def _sse(event: Event) -> ServerSentEvent:
    return ServerSentEvent(id=str(event.seq), event=event.type, data=event.payload)


async def event_stream(store: Store, run_id: int, after_seq: int = 0, *, busy: Callable[[], bool] = lambda: False,
                       poll: float = 1.0) -> AsyncIterator[ServerSentEvent]:
    """History after `after_seq`, then live events, until the run is DONE or ESCALATED with no work left
    on it (a "run now" on a DONE run keeps the stream open until it finishes); then an `end` event.
    Subscribes before replaying, so nothing appended in between is lost, and drops anything replayed."""
    queue = store.bus.subscribe(run_id)
    try:
        last = after_seq
        for event in store.events(run_id, after_seq):
            last = event.seq
            yield _sse(event)
        while True:
            # set_state commits and publishes without an await in between, so once the run reads terminal
            # its final events are already in the queue
            finished = store.get_run(run_id).state.value in TERMINAL and not busy()
            try:
                event = queue.get_nowait() if finished else await asyncio.wait_for(queue.get(), poll)
            except (asyncio.QueueEmpty, TimeoutError):
                if finished:
                    break
                continue
            if event.seq > last:
                last = event.seq
                yield _sse(event)
        yield ServerSentEvent(event="end", data={"state": store.get_run(run_id).state.value, "last_seq": last})
    finally:
        store.bus.unsubscribe(run_id, queue)


# ------------------------------------------------------------------ snapshot


def _step_status(store: Store, run_id: int) -> dict[str, dict[str, Any]]:
    """Per step id, its latest execution state from step_started / step_verified / step_failed."""
    status: dict[str, dict[str, Any]] = {}
    for event in store.events(run_id):
        p = event.payload
        match event.type:
            case "step_started":
                status[p["step_id"]] = {"status": "running"}
            case "step_verified":
                status[p["step_id"]] = {"status": "skipped" if p.get("skipped") else "verified",
                                        "output": p.get("output"), "observed": p.get("observed"),
                                        "unchecked": p.get("unchecked", [])}
            case "step_failed":
                status[p["step_id"]] = {"status": "failed", "error": p["error"], "observed": p.get("observed")}
    return status


def snapshot(store: Store, run_id: int, web_origin: str, domain: str) -> dict[str, Any]:
    """The run as the workspace shows it, rebuilt from its events. Plan params are the effective ones (the
    approver's answers applied). `last_seq` is where the event stream should pick up."""
    run = store.get_run(run_id)
    ticket = store.get_ticket(run.ticket_id)
    events = store.events(run_id)
    triage = store.last_event(run_id, "triage")
    plan_event = store.last_event(run_id, "plan")
    approval = store.last_event(run_id, "approval")

    plan = steps = None
    if plan_event is not None:
        try:
            effective = runner.effective_plan(store, run_id, domain)
        except runner.RunError:  # answers that no longer fit; show the plan as proposed
            effective = runner.load_plan(store, run_id)
        plan = effective.model_dump(mode="json", by_alias=True)
        decisions = {e.payload["step_id"]: e.payload for e in store.events_of(run_id, "gate_decision")}
        status = _step_status(store, run_id)
        steps = []
        for step in plan.pop("steps"):
            decision = decisions.get(step["id"], {})
            steps.append(step | {k: decision.get(k) for k in ("decision", "rule_id", "reason")}
                         | {"status": "pending"} | status.get(step["id"], {}))

    outcome = None
    if run.state in (RunState.ESCALATED, RunState.HELD):
        outcome = store.last_event(run_id, "state").payload

    link_rows = []
    for row in links.for_run(store, run_id):
        viewed = row.viewed_at is not None
        link_rows.append({"id": row.id, "step_id": row.step_id, "to": row.to,
                          "expires_at": links.as_utc(row.expires_at).isoformat(),
                          "viewed_at": row.viewed_at.isoformat() if viewed else None,
                          "url": None if viewed else f"{web_origin}/l/{row.token}"})

    return {
        "run": {"id": run.id, "state": run.state.value, "created_at": run.created_at.isoformat(),
                "updated_at": run.updated_at.isoformat()},
        "ticket": {"id": ticket.id, "text": ticket.text, "from": ticket.from_address},
        "last_seq": events[-1].seq if events else 0,
        "triage": triage.payload["triage"] if triage else None,
        "requester_approved": triage.payload["requester_approved"] if triage else None,
        "findings": runner.findings(store, run_id),
        "plan": plan,
        "steps": steps,
        "approval": approval.payload if approval else None,
        "outcome": outcome,
        "followups": [{"id": f.id, "step_id": f.step_id, "kind": f.kind, "run_at": f.run_at.isoformat(),
                       "status": f.status} for f in scheduler.for_run(store, run_id)],
        "links": link_rows,
        "closeout": closeout.view(store, run_id, link_rows),
    }


# ------------------------------------------------------------------ requests


class TicketIn(BaseModel):
    text: str = Field(min_length=1)
    from_: str = Field(alias="from", min_length=1, description="sender email, or a name at the tenant's domain")


class ApprovalIn(BaseModel):
    by: str
    decision: Literal["approve", "reject"] = "approve"
    answers: dict[str, str] = {}


# ------------------------------------------------------------------ app


def create_app(runtime: runner.Runtime | None = None, *, web_origin: str = DEFAULT_WEB_ORIGIN) -> FastAPI:
    """uvicorn calls this with no arguments (--factory): the runtime is built from settings, and closed at
    shutdown. Tests pass their own runtime."""
    owned = runtime is None
    if owned:
        settings = Settings.load()
        runtime = runner.Runtime.from_settings(settings, environment="api")
        web_origin = settings.web_origin
    tasks = TaskRunner(runtime.store)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await tasks.cancel()
            if owned:
                await runtime.graph.aclose()

    app = FastAPI(title="AI technician", lifespan=lifespan)
    app.state.tasks = tasks
    app.add_middleware(CORSMiddleware, allow_origins=[web_origin], allow_methods=["*"], allow_headers=["*"])
    if owned:
        logfire.instrument_fastapi(app, excluded_urls="/l/.*")  # the link token is in the path
    rt = runtime

    @app.exception_handler(runner.RunError)
    async def refused(request: Request, exc: runner.RunError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(NotFound)
    async def not_found(request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse({"detail": exc.args[0] if exc.args else "not found"}, status_code=404)

    @app.post("/tickets", status_code=202)
    async def create_ticket(body: TicketIn) -> dict[str, int]:
        address = body.from_.strip()
        address = address if address.count("@") else f"{address}@{rt.domain}"
        ticket = rt.store.create_ticket(body.text, address)
        run_id = rt.store.create_run(ticket.id).id
        tasks.spawn(run_id, runner.start(rt, run_id, body.text, address), what="start")
        return {"run_id": run_id, "ticket_id": ticket.id}

    @app.get("/runs")
    async def list_runs(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
        return [{"id": run.id, "state": run.state.value, "from": ticket.from_address,
                 "subject": ticket.text.strip().splitlines()[0][:120] if ticket.text.strip() else "",
                 "created_at": run.created_at.isoformat(), "updated_at": run.updated_at.isoformat()}
                for run, ticket in rt.store.list_runs(limit)]

    @app.get("/runs/{run_id}")
    async def get_run(run_id: int) -> dict[str, Any]:
        return snapshot(rt.store, run_id, web_origin, rt.domain)

    def existing_run(run_id: int) -> int:
        """A dependency, so an unknown run is a 404 before a stream starts."""
        return rt.store.get_run(run_id).id

    @app.get("/runs/{run_id}/events", response_class=EventSourceResponse)
    async def run_events(run_id: int = Depends(existing_run), after: int = Query(0, ge=0),
                         last_event_id: int | None = Header(None)) -> AsyncIterator[ServerSentEvent]:
        after_seq = last_event_id if last_event_id is not None else after
        async for item in event_stream(rt.store, run_id, after_seq, busy=lambda: tasks.busy(run_id)):
            yield item

    @app.post("/runs/{run_id}/approval", status_code=202)
    async def approval(run_id: int, body: ApprovalIn) -> dict[str, str]:
        plan = runner.accept(rt, run_id, by=body.by, answers=body.answers, reject=body.decision == "reject")
        if plan is not None:
            tasks.spawn(run_id, runner.execute(rt, run_id, plan), what="execute")
        return {"state": rt.store.get_run(run_id).state.value}

    @app.post("/runs/{run_id}/resume", status_code=202)
    async def resume(run_id: int) -> dict[str, str]:
        state = rt.store.get_run(run_id).state
        if state != RunState.EXECUTING or tasks.busy(run_id):
            raise runner.RunError(f"run {run_id} is {state}; only an EXECUTING run with nothing running resumes")
        tasks.spawn(run_id, runner.resume(rt, run_id), what="resume")
        return {"state": state.value}

    @app.post("/followups/{followup_id}/run-now", status_code=202)
    async def run_now(followup_id: int) -> dict[str, Any]:
        row = scheduler.get(rt.store, followup_id)
        if row.status == "done" or tasks.busy(row.run_id):
            raise runner.RunError(f"follow-up {followup_id} already ran, or its run is busy")
        # a failed follow-up is reported on the run's stream; it doesn't escalate a finished run
        tasks.spawn(row.run_id, runner.run_followup(rt, followup_id), what="followup", escalate=False)
        return {"followup_id": followup_id, "run_id": row.run_id}

    @app.get("/l/{token}")
    async def view_link(token: str) -> dict[str, Any]:
        link = links.by_token(rt.store, token)
        view = links.public_view(rt.store, link)
        if view["expired"]:
            raise HTTPException(410, "this link has expired")
        return view

    @app.post("/l/{token}/reveal")
    async def reveal_link(token: str) -> dict[str, Any]:
        try:
            link, tap = links.reveal(rt.store, token, rt.link_key)
        except links.Gone as exc:
            raise HTTPException(410, str(exc)) from exc
        rt.store.append_event(link.run_id, "link_viewed", {"link_id": link.id, "step_id": link.step_id,
                                                           "at": link.viewed_at.isoformat()})
        return {"temporaryAccessPass": tap} | links.public_view(rt.store, link)

    return app
