"""Headless runs, for Week 1 and tests.

    uv run tech run "hey team, we have a new hire..." --from tgarcia@<domain>
    uv run tech run - --from tgarcia < ticket.txt        # ticket from stdin; a bare name gets @<domain>

Prints the run's events as they are appended (the same stream the UI will read), then the plan.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from tech.agents.intake import run_intake
from tech.agents.investigator import investigate
from tech.core.config import REPO, Settings
from tech.core.observability import configure_logfire
from tech.core.store import Event, Store
from tech.graph.client import graph_from_settings
from tech.playbooks import load_client_facts


def show(event: Event) -> None:
    p = event.payload
    match event.type:
        case "state":
            extra = f"  ({p['kind']}: {p['reason']})" if p.get("reason") else ""
            print(f"== {p['to']}{extra}", flush=True)
        case "triage":
            t = p["triage"]
            print(f"   intents={t['intents']}  requester={t['requester']['email']} "
                  f"approved={p['requester_approved']}  out_of_scope={t['out_of_scope_asks']}", flush=True)
        case "tool_call":
            args = ", ".join(f"{k}={v}" for k, v in p["args"].items())
            status = "" if p["ok"] else f"  ! {p['error']}"
            print(f"   [{p['t']:5.1f}s] {p['tool']}({args}){status}", flush=True)
        case "finding":
            print(f"   [{p['t']:5.1f}s] * {p['text']}", flush=True)


async def run(text: str, from_address: str) -> int:
    settings = Settings.load()
    configure_logfire(environment="cli")
    if settings.anthropic_api_key:  # the Anthropic provider reads it from the environment
        os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)
    if not from_address.count("@"):
        from_address = f"{from_address}@{settings.domain}"
    if settings.database_url.startswith("sqlite:///"):
        (REPO / "data").mkdir(exist_ok=True)
    store = Store(settings.database_url)
    facts = load_client_facts(settings.domain)
    graph = graph_from_settings(settings)

    ticket = store.create_ticket(text, from_address)
    run_id = store.create_run(ticket.id).id
    print(f"run {run_id}", flush=True)

    async def printer() -> None:
        async for event in store.stream(run_id):
            show(event)

    printing = asyncio.create_task(printer())
    try:
        triage, check = await run_intake(store, run_id, text, from_address, facts)
        result = await investigate(store, run_id, text, triage, requester_approved=check.approved,
                                   graph=graph, facts=facts, domain=settings.domain)
        await asyncio.sleep(0)  # let the printer drain the last events
    finally:
        printing.cancel()
        await graph.aclose()

    if result.plan is not None:
        print(json.dumps(result.plan.model_dump(mode="json", by_alias=True, exclude_defaults=True), indent=2))
    return 0 if result.outcome == "planned" else 2


def main() -> None:
    parser = argparse.ArgumentParser(prog="tech")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="triage and investigate one ticket")
    run_p.add_argument("ticket", help="ticket text, or - to read it from stdin")
    run_p.add_argument("--from", dest="from_address", required=True, help="sender email (or name@ the tenant)")
    args = parser.parse_args()

    if args.command == "run":
        text = sys.stdin.read() if args.ticket == "-" else args.ticket
        sys.exit(asyncio.run(run(text, args.from_address)))


if __name__ == "__main__":
    main()
