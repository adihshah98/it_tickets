"""Mocked actions (Tech.MD §7, "Mocks and our own services").

license.purchase stands in for a Pax8 order. The demo tenant's pool is seeded at 0 free seats, with one
spare seat parked on the disabled `seat-holder` user (seed.py). "Buying" a seat removes seat-holder's
license, so one real seat becomes free for the new hire; tenant/reset.py gives it back.
"""

from __future__ import annotations

from urllib.parse import quote

from tech.graph.writes import Deviation, Handler, assign_license, get_or_none, license_skus

SEAT_HOLDER = "seat-holder"


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
