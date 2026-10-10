"""The CLI printer runs in a background task, where an exception is silent: every event shape must print."""

import pytest

from tech.cli import show
from tech.core.store import Event

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
]


@pytest.mark.parametrize(("type_", "payload"), EVENTS)
def test_every_event_prints(type_, payload, capsys):
    show(Event(run_id=1, seq=1, type=type_, payload=payload))
    assert type_ == "plan" or capsys.readouterr().out.strip()
