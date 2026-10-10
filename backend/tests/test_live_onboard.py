"""The Week 1 exit as a test: Priya's onboarding, live, end to end (Build-W1-2.md, Day 5).

Run after `uv run tenant/reset.py`, with `uv run pytest -m live -k onboard`. Costs one Haiku and one
Opus run (about 0.2-0.4 USD) and writes to the demo tenant; reset again afterwards.
"""

import os

import pytest

from tech import runner
from tech.core.config import Settings, ensure_link_key
from tech.core.store import RunState, Store
from tech.execution import links, scheduler
from tech.graph.client import graph_from_settings
from tech.graph.writes import license_skus, member_group_ids
from tech.playbooks import load_client_facts

pytestmark = pytest.mark.live

TICKET = ("hey team, we have a new hire Priya Nair starting monday in accounting. same setup as Marcus pls, "
          "she'll need office + teams. thx")


async def test_priya_onboarding_end_to_end():
    settings = Settings.load()
    if settings.anthropic_api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)
    store = Store("sqlite://")
    rt = runner.Runtime(store=store, graph=graph_from_settings(settings), facts=load_client_facts(settings.domain),
                        domain=settings.domain, link_key=ensure_link_key(settings))
    taylor = f"tgarcia@{settings.domain}".lower()
    try:
        run_id = store.create_run(store.create_ticket(TICKET, taylor).id).id
        assert await runner.start(rt, run_id, TICKET, taylor) == RunState.AWAITING_APPROVAL
        decisions = {e.payload["action"]: e.payload["decision"] for e in store.events_of(run_id, "gate_decision")}
        assert decisions["license.purchase"] == decisions["tap.create"] == "approve"

        assert await runner.approve(rt, run_id, by=taylor, answers={"upn_format": "priya.nair"}) == RunState.DONE

        g = rt.graph
        priya = await g.get(f"/users/priya.nair@{settings.domain}", params={"$select": "id,usageLocation"})
        assert priya["usageLocation"] == "US"
        names = {grp["displayName"] async for grp in g.list(f"/users/{priya['id']}/memberOf",
                                                            {"$select": "displayName"})}
        assert {"Accounting", "Finance Files"} <= names
        assert not names & {"RDS-Users", "VPN-Users", "Northwind Parking"}
        assert await member_group_ids(g, priya["id"])
        taps = [t async for t in g.list(f"/users/{priya['id']}/authentication/temporaryAccessPassMethods")]
        assert len(taps) == 1
        holder = await g.get(f"/users/seat-holder@{settings.domain}", params={"$select": "id"})
        purchase = next(e.payload for e in store.events_of(run_id, "step_verified")
                        if e.payload["action"] == "license.purchase")
        assert purchase["output"]["skuId"].lower() not in await license_skus(g, holder["id"])
        assert any(f.kind == "license.assign" for f in scheduler.for_run(store, run_id))

        verified = {e.payload["step_id"] for e in store.events_of(run_id, "step_verified")}
        plan = runner.load_plan(store, run_id)
        assert verified == {s.id for s in plan.steps}
        link = next(links.get(store, run_id, s.id) for s in plan.steps if s.action == "link.sendOneTime")
        tap = links.decrypt(link, rt.link_key)
        assert all(tap not in str(e.payload) for e in store.events(run_id))
    finally:
        await rt.graph.aclose()
