"""Executor (Tech.MD §4.6): runs an approved plan's steps against the tenant. Code only, no model.

Steps run one at a time in dependency order (`depends_on` plus the steps their `$ref`s point at).
For each step:

  1. Already verified in this run (a step_verified event)? Skip it: this is how a resumed run carries on.
  2. Resolve `$ref:<step>.output.id` from earlier outputs and re-validate the params.
     `.output.tap` refs stay refs: the link handler reads the TAP from memory, so it never sits in params.
  3. Idempotency: if the target state is already true, record step_verified (skipped) and move on.
  4. Snapshot, act, then verify by reading back, polling while the directory catches up. The handler's
     own check decides, plus every key of the plan's `expected` that the read observed; other expected
     keys are listed as unchecked.

Transient Graph errors are retried by the client (429, 5xx, replication lag). Each wait, the client's or a
verify poll's, is a step_retry event, which the workspace shows as "↻ retried". Anything else, a verify
mismatch, or a Deviation stops the run with a step_failed event; the runner escalates it (no replanner
until Week 3). Every payload passes `Secrets.guard` before it is appended. A technician's console step
runs through the same `run_step`, its step events carrying `actor` (absent = the agent).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import logfire

from tech.core.models import Plan, StepBase, parse_ref
from tech.core.store import Store
from tech.execution.catalogue import ACTIONS
from tech.graph import client
from tech.graph.client import GraphClient, GraphError
from tech.graph.writes import Deviation, Secrets, StepContext, same
from tech.safety.lint import params_of

VERIFY_DELAYS = (0.0, 2.0, 4.0, 8.0, 16.0)


@dataclass
class ExecResult:
    ok: bool
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    failed_step: str | None = None
    error: str | None = None


def _refs(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set().union(*map(_refs, value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*map(_refs, value)) if value else set()
    ref = parse_ref(value)
    return {ref[0]} if ref else set()


def order(plan: Plan) -> list[StepBase]:
    """Plan order, except a step waits for every step it depends on or references. The linter has already
    checked that these point backwards; a cycle or unknown step raises."""
    ids = {s.id for s in plan.steps}
    needs = {s.id: (set(s.depends_on) | _refs(params_of(s))) for s in plan.steps}
    if unknown := {d for deps in needs.values() for d in deps} - ids:
        raise ValueError(f"steps depend on unknown steps {sorted(unknown)}")
    done: list[StepBase] = []
    pending = list(plan.steps)
    while pending:
        ready = next((s for s in pending if needs[s.id] <= {d.id for d in done}), None)
        if ready is None:
            raise ValueError(f"dependency cycle among {[s.id for s in pending]}")
        done.append(ready)
        pending.remove(ready)
    return done


def resolve(value: Any, outputs: dict[str, dict[str, Any]]) -> Any:
    """Replace `$ref:<step>.output.<field>` with that step's output, except `.output.tap` (kept as a ref)."""
    if isinstance(value, dict):
        return {k: resolve(v, outputs) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, outputs) for v in value]
    ref = parse_ref(value)
    if ref is None or ref[1] == "tap":
        return value
    step_id, name = ref
    if name not in outputs.get(step_id, {}):
        raise Deviation(f"{value} can't be resolved: step {step_id} has no output {name!r}")
    return outputs[step_id][name]


def _timestamp(value: Any) -> bool:
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def compare(expected: dict[str, Any], observed: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(mismatches, unchecked keys) between the plan's expected state and what the verify read saw.

    A key the read doesn't show is unchecked, and so is a timestamp the plan couldn't have known (it wrote
    "after approval time" for when sessions were revoked, Build-Day16 live run 2): the handler's own verify
    already checked that the time moved."""
    mismatches, unchecked = [], []
    for key, want in expected.items():
        if key not in observed or (_timestamp(observed[key]) and not _timestamp(want)):
            unchecked.append(key)
        elif not same(want, observed[key]):
            mismatches.append(f"{key}: expected {want!r}, saw {observed[key]!r}")
    return mismatches, unchecked


@dataclass
class Execution:
    """One pass over a run's steps: the store, the tenant, and the secrets held in memory meanwhile."""

    store: Store
    run_id: int
    graph: GraphClient
    domain: str
    secrets: Secrets = field(default_factory=Secrets)
    link_key: str | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    t0: float = field(default_factory=time.monotonic)
    actor: str = "agent"  # or technician:{email} for a console step (Build-Day20)

    def emit(self, type: str, payload: dict[str, Any]) -> None:
        if self.actor != "agent" and type.startswith("step_"):
            payload = payload | {"actor": self.actor}  # absent means the agent, so agent payloads don't change
        self.secrets.guard(payload)
        self.store.append_event(self.run_id, type, payload | {"t": round(time.monotonic() - self.t0, 1)})

    async def run_step(self, step: StepBase, outputs: dict[str, dict[str, Any]], *,
                       resumed: bool = False) -> tuple[bool, dict[str, Any], str | None]:
        """Run one step: (ok, output, error). Records step_started / step_verified / step_failed."""
        spec = ACTIONS[step.action]
        base = {"step_id": step.id, "action": step.action}
        ctx = StepContext(graph=self.graph, store=self.store, run_id=self.run_id, step_id=step.id,
                          domain=self.domain, secrets=self.secrets, link_key=self.link_key, resumed=resumed,
                          sleep=self.sleep)
        listener = client.on_retry.set(lambda reason, wait: self.emit("step_retry", base | {"reason": reason,
                                                                                          "wait": wait}))
        try:
            return await self._run_step(step, spec, base, ctx, outputs)
        finally:
            client.on_retry.reset(listener)

    async def _run_step(self, step, spec, base, ctx, outputs) -> tuple[bool, dict[str, Any], str | None]:
        with logfire.span("step {action}", action=step.action, step_id=step.id, run_id=self.run_id):
            try:
                params = resolve(params_of(step), outputs)
                params = spec.params.model_validate(params).model_dump(mode="json", by_alias=True)
                if (output := await spec.handler.existing(ctx, params)) is not None:
                    self.emit("step_verified", base | {"output": output, "observed": {}, "unchecked": [],
                                                       "skipped": "already true"})
                    return True, output, None
                snapshot = await spec.handler.snapshot(ctx, params)
                self.emit("step_started", base | {"params": params, "snapshot": snapshot})
                output = await spec.handler.act(ctx, params)
                ok, observed, mismatches, unchecked = await self._verify(ctx, step, params, output)
                if not ok or mismatches:
                    error = "verify failed: " + ("; ".join(mismatches) or "the read-back doesn't show the change")
                    self.emit("step_failed", base | {"error": error, "kind": "verify", "observed": observed,
                                                     "output": output})
                    return False, output, error
                self.emit("step_verified", base | {"output": output, "observed": observed, "unchecked": unchecked})
                return True, output, None
            except Deviation as exc:
                error, kind = str(exc), "deviation"
            except GraphError as exc:
                error, kind = f"{exc.status} {exc.code}: {exc.message}", "graph"
            except ValueError as exc:  # params no longer validate once refs are resolved
                error, kind = str(exc), "params"
            self.emit("step_failed", base | {"error": error, "kind": kind})
            return False, {}, error

    async def _verify(self, ctx: StepContext, step: StepBase, params: dict[str, Any], output: dict[str, Any]):
        spec = ACTIONS[step.action]
        for delay in VERIFY_DELAYS:
            if delay:
                self.emit("step_retry", {"step_id": step.id, "action": step.action, "reason": "verify", "wait": delay})
                await self.sleep(delay)
            ok, observed = await spec.handler.verify(ctx, params, output)
            mismatches, unchecked = compare(step.expected, observed)
            if ok and not mismatches:
                break
        return ok, observed, mismatches, unchecked

    async def run(self, plan: Plan, skip: Sequence[str] = ()) -> ExecResult:
        """Every step not yet verified, in dependency order. Outputs of verified steps come from their events."""
        verified = {e.payload["step_id"]: e.payload for e in self.store.events_of(self.run_id, "step_verified")}
        started = {e.payload["step_id"] for e in self.store.events_of(self.run_id, "step_started")}
        outputs = {sid: p.get("output", {}) for sid, p in verified.items()}
        for step in order(plan):
            if step.id in verified or step.id in skip:
                continue
            ok, output, error = await self.run_step(step, outputs, resumed=step.id in started)
            if not ok:
                return ExecResult(False, outputs, step.id, error)
            outputs[step.id] = output
        return ExecResult(True, outputs)
