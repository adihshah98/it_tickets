"""Follow-ups (Tech.MD §7): work a plan schedules for later, e.g. assigning the license on the start date.

A followup.schedule step stores a row with params already resolved (no `$ref`s), so it can run
without the plan. Running one goes through the executor's step path (runner.run_followup); the
demo's "run now" button uses the same call. There is no clock-driven runner yet.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import select

from tech.core.store import Followup, NotFound, Store, utcnow
from tech.graph.writes import Handler


def get(store: Store, followup_id: int) -> Followup:
    with store.session() as s:
        row = s.get(Followup, followup_id)
        if row is None:
            raise NotFound(f"follow-up {followup_id} not found")
        return row


def for_step(store: Store, run_id: int, step_id: str) -> Followup | None:
    with store.session() as s:
        return s.exec(select(Followup).where(Followup.run_id == run_id, Followup.step_id == step_id)).first()


def for_run(store: Store, run_id: int) -> list[Followup]:
    with store.session() as s:
        return list(s.exec(select(Followup).where(Followup.run_id == run_id).order_by(Followup.id)))


def mark(store: Store, followup_id: int, status: str) -> None:
    with store.session() as s:
        row = s.get(Followup, followup_id)
        row.status = status
        row.done_at = utcnow()
        s.add(row)
        s.commit()


def _output(row: Followup) -> dict[str, Any]:
    return {"followupId": row.id, "kind": row.kind, "runAt": row.run_at.isoformat()}


class FollowupSchedule(Handler):
    async def existing(self, ctx, p):
        row = for_step(ctx.store, ctx.run_id, ctx.step_id)
        return _output(row) if row else None

    async def act(self, ctx, p):
        row = Followup(run_id=ctx.run_id, step_id=ctx.step_id, kind=p["kind"],
                       run_at=datetime.fromisoformat(p["runAt"]), params=p)
        with ctx.store.session() as s:
            s.add(row)
            s.commit()
        return _output(row)

    async def verify(self, ctx, p, output):
        row = for_step(ctx.store, ctx.run_id, ctx.step_id)
        return row is not None, {"exists": row is not None, "status": row.status if row else None}
