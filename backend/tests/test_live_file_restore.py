"""Sophie's file restore, live, with no playbook (Build-W4 Day 18; Product.MD §3.4, first half).

Run after `uv run tenant/reset.py --files base` and then `--files overwrite`, with
`uv run pytest -m live -k file_restore`. Costs one Haiku, one Opus and one Sonnet run (about 0.2-0.4 USD)
and restores a version on the demo tenant's team site; reset again afterwards.

The wording asks for "the version from before I saved over it", with no time of day: a reset stages every
version within a minute, whenever it runs, so "yesterday afternoon" or even "this morning" contradicts the
history and rightly gets a question back instead of a restore (Build-Day18 decision 7 and live run).
"""

import hashlib
import os

import pytest
from test_live_onboard import _diagnosis

from tech import runner
from tech.core.config import Settings, ensure_link_key
from tech.core.store import RunState, Store
from tech.graph.client import graph_from_settings
from tech.graph.writes import file_versions
from tech.playbooks import load_client_facts

pytestmark = pytest.mark.live

TICKET = ("Hi! I accidentally saved over Q3 Parking Rates.xlsx. Can you get back the version from before I saved "
          "over it? Thanks, Sophie")


async def test_sophie_restores_a_file_with_no_playbook():
    settings = Settings.load()
    if settings.anthropic_api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)
    store = Store("sqlite://")
    rt = runner.Runtime(store=store, graph=graph_from_settings(settings), facts=load_client_facts(settings.domain),
                        domain=settings.domain, link_key=ensure_link_key(settings))
    sophie, taylor = f"sbaker@{settings.domain}".lower(), f"tgarcia@{settings.domain}".lower()
    try:
        run_id = store.create_run(store.create_ticket(TICKET, sophie).id).id
        assert await runner.start(rt, run_id, TICKET, sophie) == RunState.AWAITING_APPROVAL, \
            _diagnosis(store, run_id)
        assert store.last_event(run_id, "plan").payload["no_playbook"] is True
        decisions = [(e.payload["action"], e.payload["decision"], e.payload["rule_id"])
                     for e in store.events_of(run_id, "gate_decision")]
        assert decisions == [("file.restoreVersion", "approve", "no_playbook.approve")], decisions

        [step] = runner.load_plan(store, run_id).steps
        p = step.params
        before = await file_versions(rt.graph, p.driveId, p.itemId)
        assert p.versionId == before[1]["id"], f"expected the version before the overwrite: {before}"

        assert await runner.approve(rt, run_id, by=taylor) == RunState.DONE, _diagnosis(store, run_id)
        assert len(store.events_of(run_id, "approval")) == 1
        checks = {e.payload["probe"]: e.payload["ok"] for e in store.events_of(run_id, "done_check")}
        assert checks == {"version_restored": True, "message_sent": True}, checks
        sent = {e.payload["kind"]: e.payload for e in store.events_of(run_id, "message_sent")}
        assert set(sent) == {"reply", "psa_note"}, store.events_of(run_id, "message_blocked")
        assert "lost" in sent["reply"]["text"].lower()                       # the "nothing is lost" note
        assert store.events_of(run_id, "time_entry")
        assert runner.can_save_playbook(store, run_id)

        after = await file_versions(rt.graph, p.driveId, p.itemId)
        assert len(after) == len(before) + 1 and after[0]["size"] == before[1]["size"]
        item = f"/drives/{p.driveId}/items/{p.itemId}"
        current, chosen = await rt.graph.content(f"{item}/content"), \
            await rt.graph.content(f"{item}/versions/{p.versionId}/content")
        assert hashlib.sha256(current).digest() == hashlib.sha256(chosen).digest()
    finally:
        await rt.graph.aclose()
