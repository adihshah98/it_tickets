"""Mocked actions (Tech.MD §7, "Mocks and our own services").

license.purchase stands in for a Pax8 order. The demo tenant's pool is seeded at 0 free seats, with one
spare seat parked on the disabled `seat-holder` user (seed.py). "Buying" a seat removes seat-holder's
license, so one real seat becomes free for the new hire; tenant/reset.py gives it back.

requester.confirmOutOfBand stands in for reaching the subject's manager on a number already on file
(oob_contacts). It is not a plan step: the demo's "Manager confirmed via SMS" button records an
`oob_confirmed` event, which an MFA reset needs before it may run (runner.accept, the oob_confirmed probe).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

from tech.graph.writes import Deviation, Handler, assign_license, get_or_none, license_skus

if TYPE_CHECKING:
    from tech.core.store import Event, Store

SEAT_HOLDER = "seat-holder"


def confirm_out_of_band(store: Store, run_id: int, *, by: str, contact: str = "manager",
                        via: str = "sms") -> Event:
    """Record that `by` confirmed the request with the subject's `contact` over `via` (mocked: no SMS is sent)."""
    return store.append_event(run_id, "oob_confirmed", {"by": by, "contact": contact, "via": via, "mock": True})


class LicensePurchase(Handler):
    async def _holder(self, ctx) -> str:
        upn = f"{SEAT_HOLDER}@{ctx.domain}"
        user = await get_or_none(ctx.graph, f"/users/{quote(upn)}", params={"$select": "id"})
        if user is None:
            raise Deviation(f"{upn} is missing; run tenant/seed.py so the mock purchase has a seat to free")
        return user["id"]

    async def existing(self, ctx, p):
        holder = await self._holder(ctx)
        if p["skuId"].lower() not in await license_skus(ctx.graph, holder):
            return {"skuId": p["skuId"], "quantity": p["quantity"], "seatFrom": SEAT_HOLDER, "mock": "pax8"}
        return None

    async def snapshot(self, ctx, p):
        return {"seatHolderHasSku": True}

    async def act(self, ctx, p):
        # One spare seat exists however many were "bought"; the demo only ever needs one.
        await assign_license(ctx, await self._holder(ctx), remove=[p["skuId"]])
        return {"skuId": p["skuId"], "quantity": p["quantity"], "seatFrom": SEAT_HOLDER, "mock": "pax8"}

    async def verify(self, ctx, p, output):
        # Not subscribedSkus: consumedUnits lags assignLicense by a minute or more (seed.py).
        has = p["skuId"].lower() in await license_skus(ctx.graph, await self._holder(ctx))
        return not has, {"seatHolderHasSku": has}
