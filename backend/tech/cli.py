"""Headless runs, for Week 1 and tests.

    uv run tech run "hey team, we have a new hire..." --from tgarcia@<domain>
    uv run tech run - --from tgarcia < ticket.txt        # ticket from stdin; a bare name gets @<domain>
    uv run tech approve 7 --confirm upn_format=priya.nair [--by tgarcia] [--reject]
    uv run tech oob 7 --by tgarcia                       # MFA reset: "Manager confirmed via SMS" (mocked)
    uv run tech resume 7                                 # carry on an execution a crashed process left
    uv run tech followup list 7 | run 3                  # scheduled follow-ups; "run now"
    uv run tech save-playbook 20 --by aditya [--intent file.restore_version] [--autonomy A3] [--yes]

`run` triages, investigates (linter in the loop), gates and, with nothing to approve, executes.
`approve` executes an approved plan. Each prints the run's events as they are appended (the same
stream the API serves over SSE: `uv run uvicorn tech.api:create_app --factory`). Both work on the same store.
`save-playbook` drafts a playbook from a finished no-playbook run (one Opus call), prints it with any
problems, and saves it on confirmation (exit 0 saved, 1 not saved).

Exit code: 0 done or awaiting approval, 1 refused (e.g. approving a DONE run), 2 escalated, 3 held.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from tech import runner
from tech.core.config import Settings
from tech.core.store import Event, RunState, Store
from tech.execution import scheduler
from tech.playbooks import PlaybookRejected


def show(event: Event) -> None:
    p = event.payload
    match event.type:
        case "state":
            label = f"{p['kind']}: " if p.get("kind") else ""
            extra = f"  ({label}{p['reason']})" if p.get("reason") else ""
            print(f"== {p['to']}{extra}", flush=True)
            for key in ("diagnosis", "suggested_next_step", "missing_action"):  # a no-playbook escalation
                if p.get(key):
                    print(f"   {key.replace('_', ' ')}: {p[key]}", flush=True)
            for item in p.get("rollback", []):
                print(f"   rollback offered: {item['step_id']}: {item['compensate']}", flush=True)
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
        case "lint_result" if p["ok"]:
            print(f"   [{p['t']:5.1f}s] lint ok (attempt {p['attempt']})", flush=True)
        case "lint_result":
            print(f"   [{p['t']:5.1f}s] lint rejected (attempt {p['attempt']}):", flush=True)
            for problem in p["problems"]:
                print(f"       - {problem}", flush=True)
        case "gate_decision":
            print(f"   {p['step_id']:<24} {p['action']:<18} {p['decision']:<8} {p['rule_id']}", flush=True)
        case "approval":
            answers = "  " + ", ".join(f"{k}={v}" for k, v in p["answers"].items()) if p["answers"] else ""
            print(f"   {p['decision']}d by {p['by']}{answers}", flush=True)
        case "step_started":
            print(f"   [{p['t']:5.1f}s] -> {p['step_id']:<24} {p['action']}", flush=True)
        case "step_verified":
            skipped = f"  (skipped: {p['skipped']})" if p.get("skipped") else ""
            print(f"   [{p['t']:5.1f}s] ok {p['step_id']:<24} {p['action']}{skipped}", flush=True)
        case "step_failed":
            print(f"   [{p['t']:5.1f}s] FAILED {p['step_id']:<20} {p['action']}  {p['error']}", flush=True)
        case "done_check":
            mark = "ok" if p["ok"] else "FAILED"
            print(f"   dod {mark} {p['probe']}: {p['detail']}", flush=True)
        case "message_sent":
            print(f"   sent {p['kind']}{' to ' + p['to'] if p.get('to') else ''}: {p.get('subject') or ''}", flush=True)
            print("     " + p["text"].replace("\n", "\n     "), flush=True)
        case "message_blocked":
            why = ", ".join(h["kind"] for h in p["hits"]) or p.get("detail", "")
            print(f"   blocked {p['kind']} ({p['reason']}: {why})", flush=True)
        case "time_entry":
            print(f"   time entry: {p['work_type']} {p['minutes']} min", flush=True)


@asynccontextmanager
async def printing(store: Store, run_id: int, after_seq: int = 0) -> AsyncIterator[None]:
    async def printer() -> None:
        async for event in store.stream(run_id, after_seq):
            show(event)

    task = asyncio.create_task(printer())
    try:
        yield
    finally:
        await asyncio.sleep(0)  # let the printer drain the last events
        task.cancel()


def exit_code(store: Store, run_id: int) -> int:
    return {RunState.ESCALATED: 2, RunState.HELD: 3}.get(store.get_run(run_id).state, 0)


async def with_runtime(work: Callable[[runner.Runtime], Awaitable[int]]) -> int:
    rt = runner.Runtime.from_settings(Settings.load(), environment="cli")
    try:
        return await work(rt)
    except (runner.RunError, KeyError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    finally:
        await rt.graph.aclose()


async def run(text: str, from_address: str) -> int:
    async def work(rt: runner.Runtime) -> int:
        address = from_address if from_address.count("@") else f"{from_address}@{rt.domain}"
        run_id = rt.store.create_run(rt.store.create_ticket(text, address).id).id
        print(f"run {run_id}", flush=True)
        async with printing(rt.store, run_id):
            await runner.start(rt, run_id, text, address)
        plan = rt.store.last_event(run_id, "plan")
        if plan is not None:
            print(json.dumps(plan.payload["plan"], indent=2))
        return exit_code(rt.store, run_id)

    return await with_runtime(work)


async def continue_run(run_id: int, action: Callable[[runner.Runtime], Awaitable[object]]) -> int:
    async def work(rt: runner.Runtime) -> int:
        seen = max((e.seq for e in rt.store.events(run_id)), default=0)
        async with printing(rt.store, run_id, after_seq=seen):
            await action(rt)
        for row in scheduler.for_run(rt.store, run_id):
            print(f"   follow-up {row.id}: {row.kind} at {row.run_at:%Y-%m-%d %H:%M}  [{row.status}]")
        if runner.can_save_playbook(rt.store, run_id):
            print(f"   no playbook for this ticket type: save it with `tech save-playbook {run_id} --by <you>`",
                  flush=True)
        return exit_code(rt.store, run_id)

    return await with_runtime(work)


async def followups(command: str, ident: int) -> int:
    async def work(rt: runner.Runtime) -> int:
        if command == "list":
            for row in scheduler.for_run(rt.store, ident):
                print(f"{row.id}: {row.kind} at {row.run_at:%Y-%m-%d %H:%M}  [{row.status}]  {row.params}")
            return 0
        row = scheduler.get(rt.store, ident)
        seen = max((e.seq for e in rt.store.events(row.run_id)), default=0)
        async with printing(rt.store, row.run_id, after_seq=seen):
            ok = await runner.run_followup(rt, ident)
        return 0 if ok else 2

    return await with_runtime(work)


async def save_playbook(rt: runner.Runtime, run_id: int, *, by: str, intent: str | None = None,
                        autonomy: str = "A2", yes: bool = False, ask: Callable[[str], str] = input) -> int:
    """Draft a playbook from a run, show it, and approve it on confirmation. 0 saved, 1 not saved."""
    print(f"drafting a playbook from run {run_id}...", flush=True)
    draft = await runner.draft_playbook(rt, run_id, intent=intent)
    print(f"--- {draft.intent} (from run {run_id}, {draft.source.kind}) ---\n{draft.yaml}", flush=True)
    if draft.problems:
        print("the draft fails its checks:", *(f"  - {p}" for p in draft.problems), sep="\n")
        return 1
    if not yes and ask(f"approve as {draft.intent} at {autonomy}? [y/N] ").strip().lower() not in ("y", "yes"):
        print("not saved")
        return 1
    try:
        row = rt.library.approve(draft.intent, draft.yaml, autonomy=autonomy, by=by, source=draft.source.kind,
                                 source_run_id=run_id)
    except PlaybookRejected as exc:
        print("not saved:", *(f"  - {p}" for p in exc.problems), sep="\n")
        return 1
    print(f"saved {draft.intent} v{row.version} at {autonomy}, approved by {by}")
    return 0


def parse_answers(pairs: list[str]) -> dict[str, str]:
    answers = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"--confirm takes key=value, not {pair!r}")
        answers[key.strip()] = value.strip()
    return answers


def main() -> None:
    parser = argparse.ArgumentParser(prog="tech")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="triage, investigate and gate one ticket")
    run_p.add_argument("ticket", help="ticket text, or - to read it from stdin")
    run_p.add_argument("--from", dest="from_address", required=True, help="sender email (or name@ the tenant)")
    approve_p = sub.add_parser("approve", help="approve (or reject) a run's plan and execute it")
    approve_p.add_argument("run_id", type=int)
    approve_p.add_argument("--by", help="the approver (default: the client's first onboarding approver)")
    approve_p.add_argument("--confirm", action="append", default=[], metavar="KEY=VALUE",
                           help=f"answer a confirm question: {', '.join(runner.ANSWER_KEYS)}")
    approve_p.add_argument("--reject", action="store_true")
    oob_p = sub.add_parser("oob", help="record out-of-band confirmation on a waiting MFA reset (mocked)")
    oob_p.add_argument("run_id", type=int)
    oob_p.add_argument("--by", required=True, help="the technician who reached the subject's manager")
    resume_p = sub.add_parser("resume", help="carry on an interrupted execution")
    resume_p.add_argument("run_id", type=int)
    followup_p = sub.add_parser("followup", help="list a run's follow-ups, or run one now")
    followup_p.add_argument("action", choices=["list", "run"])
    followup_p.add_argument("id", type=int, help="run id for list, follow-up id for run")
    save_p = sub.add_parser("save-playbook", help="draft a playbook from a finished no-playbook run, and save it")
    save_p.add_argument("run_id", type=int)
    save_p.add_argument("--by", required=True, help="who approves the playbook")
    save_p.add_argument("--intent", help="the job's intent slug, if not the one the run proposes")
    save_p.add_argument("--autonomy", choices=["A1", "A2", "A3"], default="A2")
    save_p.add_argument("--yes", action="store_true", help="save without asking")
    args = parser.parse_args()

    match args.command:
        case "run":
            text = sys.stdin.read() if args.ticket == "-" else args.ticket
            code = asyncio.run(run(text, args.from_address))
        case "approve":
            answers = parse_answers(args.confirm)

            async def approve(rt: runner.Runtime) -> object:
                by = args.by or rt.facts.approvers["user.onboard"][0]
                return await runner.approve(rt, args.run_id, by=by, answers=answers, reject=args.reject)

            code = asyncio.run(continue_run(args.run_id, approve))
        case "oob":
            async def oob(rt: runner.Runtime) -> None:
                runner.confirm_oob(rt, args.run_id, by=args.by)

            code = asyncio.run(continue_run(args.run_id, oob))
        case "resume":
            code = asyncio.run(continue_run(args.run_id, lambda rt: runner.resume(rt, args.run_id)))
        case "followup":
            code = asyncio.run(followups(args.action, args.id))
        case "save-playbook":
            code = asyncio.run(with_runtime(lambda rt: save_playbook(
                rt, args.run_id, by=args.by, intent=args.intent, autonomy=args.autonomy, yes=args.yes)))
    sys.exit(code)


if __name__ == "__main__":
    main()
