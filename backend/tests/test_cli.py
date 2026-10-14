"""The CLI printer runs in a background task, where an exception is silent: every event shape must print.
And `save-playbook`: draft, ask, save (Build-Day19)."""

import pytest
from conftest import LEARNED_RESTORE, TAYLOR, drafter_model

from tech import runner
from tech.cli import save_playbook, show
from tech.core.store import Event, RunState

EVENTS = [
    ("state", {"from": None, "to": "RECEIVED"}),
    ("state", {"from": "PLAN_CHECKED", "to": "HELD", "reason": "not an approver"}),
    ("state", {"from": "PLAN_CHECKED", "to": "ESCALATED", "kind": "gate", "reason": "g: blocked", "findings": []}),
    ("state", {"from": "PLAN_CHECKED", "to": "AWAITING_APPROVAL", "steps": ["tap"]}),
    ("triage", {"triage": {"intents": ["user.onboard"], "requester": {"email": "a@b"}, "out_of_scope_asks": []},
                "requester_approved": True, "requester_scopes": {}}),
    ("tool_call", {"tool": "users.list", "args": {}, "ok": True, "error": None, "result": [], "t": 1.0}),
    ("tool_call", {"tool": "users.get", "args": {"user_id": "x"}, "ok": False, "error": "404", "result": None,
                   "t": 1.0}),
    ("finding", {"text": "a finding", "t": 2.0}),
    ("lint_result", {"attempt": 1, "ok": False, "problems": ["fix this"], "t": 3.0}),
    ("lint_result", {"attempt": 2, "ok": True, "problems": [], "t": 4.0}),
    ("gate_decision", {"step_id": "tap", "action": "tap.create", "decision": "approve",
                       "rule_id": "risk.security.credential", "reason": "r", "risk_class": "security.credential"}),
    ("plan", {"plan": {}, "usage": {}}),
    ("approval", {"by": "tgarcia@x", "decision": "approve", "answers": {"upn_format": "pnair"},
                  "released_hold": False}),
    ("approval", {"by": "tgarcia@x", "decision": "reject", "answers": {}, "released_hold": False}),
    ("step_started", {"step_id": "s1", "action": "user.create", "params": {}, "snapshot": {}, "t": 5.0}),
    ("step_verified", {"step_id": "s1", "action": "user.create", "output": {}, "observed": {}, "unchecked": [],
                       "t": 6.0}),
    ("step_verified", {"step_id": "s2", "action": "license.purchase", "output": {}, "observed": {}, "unchecked": [],
                       "skipped": "already true", "t": 6.0}),
    ("step_failed", {"step_id": "s3", "action": "group.addMember", "error": "403 x: no", "kind": "graph", "t": 7.0}),
    ("state", {"from": "EXECUTING", "to": "ESCALATED", "kind": "execution", "reason": "s3: 403",
               "failed_step": "s3", "done": ["s1"], "findings": [],
               "rollback": [{"step_id": "s1", "action": "user.create", "compensate": "DELETE /users/{id}"}]}),
]


@pytest.mark.parametrize(("type_", "payload"), EVENTS)
def test_every_event_prints(type_, payload, capsys):
    show(Event(run_id=1, seq=1, type=type_, payload=payload))
    assert type_ == "plan" or capsys.readouterr().out.strip()


def test_parse_answers():
    from tech.cli import parse_answers

    assert parse_answers(["upn_format=pnair", "start_date = 2026-10-19"]) == {
        "upn_format": "pnair", "start_date": "2026-10-19"}
    with pytest.raises(SystemExit):
        parse_answers(["pnair"])


def test_show_prints_close_out_events_without_their_text(capsys):
    from tech.cli import show
    from tech.core.store import Event

    show(Event(run_id=1, seq=1, type="done_check", payload={"probe": "user_in_groups", "ok": False, "detail": "x"}))
    show(Event(run_id=1, seq=2, type="message_blocked",
               payload={"kind": "reply", "reason": "secret", "hits": [{"kind": "tap_like", "start": 3}]}))
    show(Event(run_id=1, seq=3, type="message_sent",
               payload={"kind": "reply", "to": "t@x", "subject": "S", "text": "T"}))
    show(Event(run_id=1, seq=4, type="time_entry", payload={"work_type": "AI Automation", "minutes": 3,
                                                           "started_at": "a", "ended_at": "b"}))
    out = capsys.readouterr().out
    assert "FAILED user_in_groups" in out and "blocked reply (secret: tap_like)" in out
    assert "sent reply to t@x: S" in out and "AI Automation 3 min" in out


# ------------------------------------------------------------------ save-playbook (Build-Day19 part 3)


async def done_restore(rt, restore_gated) -> int:
    run_id, _, _ = restore_gated()
    assert await runner.approve(rt, run_id, by=TAYLOR) == RunState.DONE
    return run_id


async def test_save_playbook_drafts_asks_and_saves(rt, restore_gated, capsys):
    rt.drafter_model = drafter_model(LEARNED_RESTORE.read_text())
    run_id = await done_restore(rt, restore_gated)
    asked = []

    code = await save_playbook(rt, run_id, by="aditya", autonomy="A3", ask=lambda q: asked.append(q) or "y")

    assert code == 0 and asked == ["approve as file.restore_version at A3? [y/N] "]
    assert "saved file.restore_version v1 at A3, approved by aditya" in capsys.readouterr().out
    assert rt.library.autonomy()["file.restore_version"] == "A3"


async def test_save_playbook_saves_nothing_on_no_or_a_failing_draft(rt, restore_gated, capsys):
    run_id = await done_restore(rt, restore_gated)
    rt.drafter_model = drafter_model(LEARNED_RESTORE.read_text())
    assert await save_playbook(rt, run_id, by="aditya", ask=lambda q: "n") == 1

    rt.drafter_model = drafter_model(LEARNED_RESTORE.read_text().replace("version_restored", "file_is_fine"))
    assert await save_playbook(rt, run_id, by="aditya", yes=True) == 1
    assert "'file_is_fine', which is not a probe" in capsys.readouterr().out
    assert not rt.library.has("file.restore_version")


async def test_library_reset_lists_asks_and_resets(rt, capsys):
    from conftest import add_learned_restore

    from tech.cli import library_reset

    add_learned_restore(rt.library, "A3")
    assert await library_reset(rt, ask=lambda _: "n") == 1
    assert rt.library.has("file.restore_version")
    assert "delete file.restore_version v1 (agent_run, approved)" in capsys.readouterr().out

    assert await library_reset(rt, ask=lambda _: "y") == 0
    assert not rt.library.has("file.restore_version")
    assert await library_reset(rt) == 0
    assert "already the shipped one" in capsys.readouterr().out
