# Day 8 Close-out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After every step verifies, the run closes itself out. Code re-checks the playbook's definition of done against live Graph state. Sonnet drafts the reply to Taylor and the PSA note. A secret scanner checks both before they are recorded. A time entry is logged, and the workspace shows each item. A run only reaches DONE when its definition of done is met; otherwise it goes to ESCALATED.

**Architecture:** `runner.execute` now goes EXECUTING → CLOSING → `closeout.close_out(...)` → DONE or ESCALATED (`kind="dod"`). Close-out is made of four separate parts:
- `safety/scanner.py` is a pure scanner: regexes plus the run's known secret values.
- `execution/dod.py` holds the DoD probes. These are Graph reads, like verify.
- `agents/closeout.py` is the Sonnet agent. It sees a summary built from events and never sees the ticket, a TAP or a link URL.
- `tech/closeout.py` is the orchestration. It writes `done_check`, `message_sent`, `message_blocked` and `time_entry` events.

The snapshot gains a `closeout` block, and the workspace renders it.

**Tech Stack:** Python 3.12, PydanticAI (`Agent(output_type=...)`, `FunctionModel` in tests), SQLModel event store, respx `FakeTenant`, FastAPI snapshot. Next.js 16.4 / React 19.3 / Tailwind 4 in `web/`.

**Spec:** [Build-W1-2.md](Build-W1-2.md), "Day 8. Close-out", and [Tech.MD](Tech.MD) §4.8. The DoD items come from `backend/tech/playbooks/user.onboard.yaml` (`definition_of_done`).

## Global Constraints

- Close-out model: `anthropic:claude-sonnet-5-5`, Sonnet 5.5 (Tech.MD §2 model table).
- No write tool is ever registered on any agent (Tech.MD §2). The close-out agent has **no tools**, only `output_type`.
- A TAP value or a link token never enters an event, a log, or a model prompt. The reply carries the literal placeholder `{{link}}`, and the URL is substituted only when the snapshot is served.
- Every outbound text goes through the scanner. A hit blocks that send and emits an event. The event holds hit kinds and offsets only, **never the matched text**.
- Time entry work type is exactly `"AI Automation"` and records actual duration (Tech.MD §4.8). Here, actual duration means the run's wall-clock time minus time spent in AWAITING_APPROVAL or HELD.
- Each new event type is added to **both** `EVENT_TYPES` in `backend/tech/core/store.py` and `EVENT_TYPES` in `web/lib/useRun.ts`, or the browser never receives it.
- Python: ruff (line length 120, `E,F,I,B,UP`), `from __future__ import annotations`, and a module docstring on every new module, in the voice of the existing ones.
- Offline suite: `uv run pytest` must stay green with **no network**. The `rt` fixture gets a `FunctionModel` for close-out.
- Web: no new runtime dependencies. Before writing a component, read the relevant guide in `web/node_modules/next/dist/docs/` (see `web/AGENTS.md`).

## Review Focus

1. **A known secret that is empty or very short.** If `""` or a 3-character value were treated as a known secret, every text would match and every reply would be blocked. Known values shorter than 8 characters are ignored, since a TAP is at least 8 characters. Covered in Task 1.
2. **The model mangles or drops the link placeholder.** For example, it writes `{{ link }}`, `{{Link}}`, or nothing. The placeholder is normalised, or a "Sign-in link" line is appended when the run has a link. The reply must never ship without the link. Covered in Task 4.
3. **Sonnet errors or times out.** The run must not crash or hang in CLOSING. Close-out writes an `error` event, sends no reply, and the run escalates with `message_sent` failed. Covered in Task 4.
4. **Requester email case differs from the link's `to`.** The plan might say `TGarcia@…` while triage says `tgarcia@…`. All matching is case-insensitive. Covered in Task 2.
5. **A playbook DoD item with no probe** (a typo, or a future playbook). It shows up as a failed check `no probe for '…'`. It must not raise a `KeyError` that crashes close-out. Covered in Task 2.

---

## Decisions

- **The probes don't interpret the playbook's `$` expressions.** Each probe derives its targets from the run itself:
  - the new user from the `user.create` step's `step_verified` output;
  - the mirrored groups from the plan's `group.addMember` steps;
  - the SKU from the plan's `license.assign` or license follow-up;
  - the peer's groups from the investigation's `users.memberOf` tool-call results.

  This way a probe can't check something the run didn't do. `user_property` is the only probe that resolves anything: `$client.<field>` reads from the client facts.
- **`needs_technician_listed` matches by name.** Each `technician` group in `client_facts.group_handling` that the peer belongs to must be named in a `needs_technician` item. The match is on the group name or one of its new `aka` names. For RDS-Users these are `NW-TS-01`, `hosted desktop` and `RDS`; for VPN-Users it is `VPN`.
- **The model never sees the ticket text.** The ticket is untrusted, and the reply doesn't need it, so leaving it out removes an injection surface. The model also never sees the link URL, so neither a Logfire trace nor the model output can hold the token.
- **The reply waits on the DoD.** If a Graph probe failed, the reply is not sent. The event is `message_blocked` with reason `"dod"`, and `message_sent` then fails. The PSA note is always written, because it is the technician's hand-off.
- **A failed DoD escalates.** The transition is CLOSING → ESCALATED with `kind="dod"`, `failed_probes`, and `findings`. An escalation write-up for runs that fail in execution is out of scope (Week 3).
- **The executor's `Secrets.guard` is now built on the scanner** (known values only, no patterns) and gains `known()`, so there is a single "contains a secret" implementation. The patterns apply only to outbound text, because event payloads such as tool results are full of random-looking IDs.
- **The link note is outbound text too.** `LinkSend.act` scans `note` (written by the investigator, shown on the link page). A hit raises `Deviation`, so the link is not created and the run escalates.
- **Files differ from the Build-W1-2 layout:** DoD probes go in `execution/dod.py` and orchestration in `tech/closeout.py`, rather than putting everything in `agents/closeout.py`. That keeps "model writes text" apart from "code decides".

## File map

| File | Change | Responsibility |
|---|---|---|
| `backend/tech/safety/scanner.py` | create | `scan(text, known, patterns)` → `list[Hit]` |
| `backend/tech/graph/writes.py` | modify `Secrets` | `known()`; `guard` uses the scanner |
| `backend/tech/execution/links.py` | modify `LinkSend.act` | scan the link note |
| `backend/tech/core/store.py` | modify `EVENT_TYPES` | add `message_blocked`, `time_entry` |
| `backend/tech/playbooks/loader.py`, `client_facts.yaml` | modify | `GroupHandling.aka` |
| `backend/tech/execution/dod.py` | create | `Probe`, `DodContext`, `PROBES`, `check()` |
| `backend/tech/agents/closeout.py` | create | `CloseoutDrafts`, `build_context()`, `draft()` |
| `backend/tech/closeout.py` | create | `close_out()`, `record()`, `with_link()`, `work_minutes()`, `view()` |
| `backend/tech/runner.py` | modify | `Runtime.closeout_model`; `execute` calls close-out |
| `backend/tech/cli.py` | modify `show` | print close-out events |
| `backend/tech/api.py` | modify `snapshot` | `closeout` block with the link filled in |
| `backend/tests/conftest.py` | modify | `drafts_model()`; `rt` uses it; `executed` fixture |
| `backend/tests/test_scanner.py`, `test_dod.py`, `test_closeout.py` | create | |
| `backend/tests/test_runner.py`, `test_api.py`, `test_cli.py` | modify | close-out assertions |
| `web/lib/types.ts`, `web/lib/run.ts`, `web/lib/useRun.ts` | modify | types, refresh-on, event list |
| `web/components/Closeout.tsx`, `web/components/Workspace.tsx` | modify | DoD, reply, PSA note, time entry |
| `docs/demo/Build-W1-2.md` | modify | Day 8 points here, done |

---

### Task 1: Secret scanner

**Files:**
- Create: `backend/tech/safety/scanner.py`
- Modify: `backend/tech/graph/writes.py` (the `Secrets` class, lines 43-67)
- Modify: `backend/tech/execution/links.py` (`LinkSend.act`, lines 112-123)
- Test: `backend/tests/test_scanner.py`

**Interfaces:**
- Produces:
  - `scanner.Hit(kind: Literal["known_secret", "password", "token", "tap_like"], start: int, end: int)` with `.payload() -> {"kind", "start"}`.
  - `scanner.scan(text: str, *, known: Iterable[str] = (), patterns: bool = True) -> list[Hit]`, with hits sorted by `start`.
  - `scanner.MIN_KNOWN = 8`.
  - `Secrets.known() -> set[str]`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_scanner.py
"""Secret scanner: known values, password/token/TAP-like patterns, and what must not trip it."""

import pytest
from conftest import DOMAIN, NEW, TAYLOR, plan_with, step
from fake_tenant import TAP_VALUE

from tech import runner
from tech.core.store import RunState
from tech.graph.writes import SecretLeak, Secrets
from tech.safety.scanner import MIN_KNOWN, scan

URL = "http://localhost:3000/l/AbC_dEf-123xyzQRS456tuvWXY789ab"


def kinds(text, **kw):
    return [h.kind for h in scan(text, **kw)]


def test_a_known_value_is_found_anywhere():
    assert kinds(f"note:{TAP_VALUE}.", known=[TAP_VALUE], patterns=False) == ["known_secret"]


def test_short_or_empty_known_values_are_ignored():
    assert scan("anything at all", known=["", "a", "x" * (MIN_KNOWN - 1)]) == []


def test_a_tap_like_string_is_caught_without_knowing_it():
    assert kinds(f"Her pass: {TAP_VALUE}") != []
    assert "tap_like" in kinds(f"use {TAP_VALUE} to sign in")


@pytest.mark.parametrize("text", [
    "password: Welcome1", "Password is Summer2026!", "pwd=Hunter22", "Temporary Access Pass: K9#mQ2xz",
])
def test_password_shapes(text):
    assert "password" in kinds(text)


@pytest.mark.parametrize("text", [
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
    "sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
    "client_secret=AbcdEfgh1234",
])
def test_token_shapes(text):
    assert "token" in kinds(text)


def test_a_normal_reply_and_psa_note_are_clean():
    text = (f"Hi Taylor,\n\nPriya Nair (priya.nair@{DOMAIN}) is set up. Open {URL} on Monday; it opens once.\n"
            "Her password is shown once, through the link. Use the Outlook mobile app, not the built-in iOS Mail.\n"
            "Hosted desktop on NW-TS-01 and VPN are with a technician. License O365_BUSINESS_ESSENTIALS "
            "assigned 2026-10-12T10:00:00Z. Group 48297cf8-723a-4826-a8b0-199373841df5. Placeholder {{link}}. "
            "It's Priya's first day; seat-holder1 kept.")
    assert scan(text) == []


def test_hits_carry_kind_and_offsets_but_never_the_text():
    hit = scan(f"x {TAP_VALUE}", known=[TAP_VALUE])[0]
    assert hit.payload() == {"kind": "known_secret", "start": 2}
    assert TAP_VALUE not in str(hit.payload())


def test_the_executor_guard_uses_known_values_only():
    secrets = Secrets()
    secrets.watch(TAP_VALUE)
    secrets.guard({"result": "Xy7!random#id"})  # tap-like, but not a known value: event payloads are full of these
    with pytest.raises(SecretLeak):
        secrets.guard({"oops": TAP_VALUE})
    assert secrets.known() == {TAP_VALUE}


async def test_a_link_note_with_a_password_is_not_sent(rt, gated, tenant):
    link = step("send_link", "link.sendOneTime", {"to": TAYLOR, "userId": NEW, "tap": "$ref:tap.output.tap",
                                                  "note": "Her password: Welcome1"}, "none", ("tap",))
    run_id = gated(plan_with(link, drop=("send_link",)))

    assert await runner.approve(rt, run_id, by=TAYLOR) == RunState.ESCALATED
    state = rt.store.last_event(run_id, "state").payload
    assert state["failed_step"] == "send_link" and "secret" in state["reason"]
    assert "Welcome1" not in str(rt.store.last_event(run_id, "step_failed").payload)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest backend/tests/test_scanner.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'tech.safety.scanner'`.

- [ ] **Step 3: Write the scanner**

```python
# backend/tech/safety/scanner.py
"""Secret scanner (Tech.MD §4.8): nothing that looks like a credential leaves in outbound text.

Two checks:
  known     values this run holds in memory (the TAP, the new user's random password, link tokens),
            found anywhere in the text. Values shorter than MIN_KNOWN are ignored: "" would match everything.
  patterns  shapes of secrets we don't know: "password: X", bearer/JWT/API-key tokens, and TAP-like
            strings (8-48 characters with upper, lower, digit and a symbol that URLs, emails, GUIDs and
            server names don't use).

Hits carry where and what kind, never the matched text, so a hit can go into an event. The executor's
event guard uses known values only (tool results are full of random-looking IDs); replies, PSA notes and
link notes use both.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

MIN_KNOWN = 8

HitKind = Literal["known_secret", "password", "token", "tap_like"]

SECRETISH = r"(?=\S*[0-9!#$%&*+=?^~|<>{}\\])\S{4,}"  # a value with a digit or a symbol; "shown." isn't one
PATTERNS: list[tuple[HitKind, re.Pattern[str]]] = [
    ("password", re.compile(
        rf"(?i)\b(pass(word|code|phrase)?|pwd|temporary access pass|tap)\b\s*(is|:|=)\s*{SECRETISH}")),
    ("token", re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{5,}")),
    ("token", re.compile(r"(?i)\bbearer\s+[\w\-.~+/]{20,}")),
    ("token", re.compile(r"\bsk-[\w-]{20,}")),
    ("token", re.compile(r"(?i)\b(api[_-]?key|client[_-]?secret|secret|access[_-]?token|token)\b\s*[:=]\s*\S{8,}")),
]
WORD = re.compile(r"\S{8,48}")
STRIP = "()[]<>\"',.;:"
SYMBOL = re.compile(r"[!#$%&*+=?^~|\\]")


@dataclass(frozen=True)
class Hit:
    kind: HitKind
    start: int
    end: int

    def payload(self) -> dict[str, Any]:
        return {"kind": self.kind, "start": self.start}


def _tap_like(word: str) -> bool:
    return (any(c.isupper() for c in word) and any(c.islower() for c in word)
            and any(c.isdigit() for c in word) and bool(SYMBOL.search(word)))


def scan(text: str, *, known: Iterable[str] = (), patterns: bool = True) -> list[Hit]:
    hits: list[Hit] = []
    for value in {v for v in known if v and len(v) >= MIN_KNOWN}:
        start = text.find(value)
        while start != -1:
            hits.append(Hit("known_secret", start, start + len(value)))
            start = text.find(value, start + 1)
    if patterns:
        for kind, pattern in PATTERNS:
            hits += [Hit(kind, m.start(), m.end()) for m in pattern.finditer(text)]
        for m in WORD.finditer(text):
            word = m.group().strip(STRIP)
            if len(word) >= 8 and _tap_like(word):
                hits.append(Hit("tap_like", m.start(), m.end()))
    return sorted(hits, key=lambda h: h.start)
```

- [ ] **Step 4: Make `Secrets` use it and scan the link note**

In `backend/tech/graph/writes.py`, add `from tech.safety import scanner` to the imports and replace `guard`, adding `known` next to it:

```python
    def known(self) -> set[str]:
        """Every secret this execution holds, for the close-out scanner."""
        return set(self._watched)

    def guard(self, payload: Any) -> None:
        """Raise before a payload holding a secret reaches the event log. Known values only: event payloads
        (tool results, outputs) are full of random-looking IDs that the scanner's patterns would flag."""
        if scanner.scan(json.dumps(payload, default=str), known=self._watched, patterns=False):
            raise SecretLeak("an event payload contained a secret; it was not written")
```

In `backend/tech/execution/links.py`, add `from tech.safety import scanner`. At the top of `LinkSend.act`, after the `link_key` check, add:

```python
        if p.get("note") and scanner.scan(p["note"]):
            raise Deviation("the link note looks like it holds a secret (password, token or TAP); it was not sent")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest backend/tests/test_scanner.py backend/tests/test_executor.py -v`
Expected: all PASS. If `test_a_normal_reply_and_psa_note_are_clean` fails, print `scan(text)` and adjust `SYMBOL` / `STRIP`. **Do not** delete phrases from the test.

- [ ] **Step 6: Full suite and lint**

Run: `uv run pytest && uv run ruff check backend`
Expected: all pass, no lint errors.

- [ ] **Step 7: Commit**

```bash
git add backend/tech/safety/scanner.py backend/tech/graph/writes.py backend/tech/execution/links.py backend/tests/test_scanner.py
git commit -m "Add the secret scanner: known values plus password, token and TAP-like patterns"
```

---

### Task 2: Definition-of-done probes

**Files:**
- Create: `backend/tech/execution/dod.py`
- Modify: `backend/tech/core/store.py:41-45` (`EVENT_TYPES`)
- Modify: `backend/tech/playbooks/loader.py:19-22` (`GroupHandling`), `backend/tech/playbooks/client_facts.yaml` (`group_handling`)
- Modify: `backend/tests/conftest.py` (add the `executed` fixture)
- Test: `backend/tests/test_dod.py`

**Interfaces:**
- Consumes: `get_or_none`, `license_skus`, `member_group_ids`, `same` from `tech.graph.writes`; `links.for_run`; `scheduler.for_run`.
- Produces:
  - `dod.Probe(probe: str, ok: bool, detail: str)` with `.payload() -> {"probe", "ok", "detail"}`.
  - `dod.DodContext(store, graph, run_id: int, plan: Plan, facts: ClientFacts, requester: str)`.
  - `dod.PROBES: dict[str, Callable[[DodContext, dict], Awaitable[Probe]]]` with the keys `user_exists`, `user_property`, `user_has_sku`, `user_in_groups`, `tap_active`, `message_sent`, `needs_technician_listed`.
  - `async dod.check(ctx, playbook, *, only: Collection[str] | None = None, exclude: Collection[str] = ()) -> list[Probe]`, which never raises on a Graph error.
  - `GroupHandling.aka: list[str]`.
  - The event types `message_blocked` and `time_entry`.
  - The test fixture `executed(plan=None) -> (run_id, plan)`, a gated run whose plan ran in the fake tenant.

- [ ] **Step 1: Add the `executed` fixture to `backend/tests/conftest.py`** (after `gated`)

```python
@pytest.fixture
def executed(rt, gated, tenant):
    """A gated run whose plan has been executed against the fake tenant (no close-out): (run_id, plan)."""
    from tech.execution.executor import Execution

    async def make(plan: Plan | None = None) -> tuple[int, Plan]:
        plan = plan or plan_with()
        run_id = gated(plan)
        result = await Execution(rt.store, run_id, rt.graph, DOMAIN, link_key=LINK_KEY, sleep=rt.sleep).run(plan)
        assert result.ok, result.error
        return run_id, plan

    return make
```

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/test_dod.py
"""DoD probes against the fake tenant: what passes after a clean run, and each way a check fails."""

from conftest import DOMAIN, FINANCE_FILES, MARCUS, RDS_USERS, TAYLOR, group, plan_with

from tech.core.models import TechnicianItem
from tech.execution import dod
from tech.playbooks import load_playbook

GRAPH_PROBES = ["user_exists", "user_property", "user_has_sku", "user_in_groups", "tap_active",
                "needs_technician_listed"]


def ctx(rt, run_id, plan, requester=TAYLOR):
    return dod.DodContext(rt.store, rt.graph, run_id, plan, rt.facts, requester)


def playbook():
    return load_playbook("user.onboard", DOMAIN)


def by_name(probes):
    return {p.probe: p for p in probes}


def priya(tenant):
    return tenant.user_by_upn(f"priya.nair@{DOMAIN}")["id"]


async def test_a_clean_run_passes_every_graph_probe(rt, executed):
    run_id, plan = await executed()
    probes = await dod.check(ctx(rt, run_id, plan), playbook(), exclude=("message_sent",))
    assert [p.probe for p in probes] == GRAPH_PROBES
    assert all(p.ok for p in probes), [p for p in probes if not p.ok]
    assert "follow-up" in by_name(probes)["user_has_sku"].detail


async def test_a_missing_group_fails_user_in_groups(rt, executed, tenant):
    run_id, plan = await executed()
    tenant.members[FINANCE_FILES].discard(priya(tenant))

    probe = by_name(await dod.check(ctx(rt, run_id, plan), playbook(), only=("user_in_groups",)))["user_in_groups"]
    assert not probe.ok and FINANCE_FILES in probe.detail


async def test_no_license_and_no_follow_up_fails(rt, executed):
    run_id, plan = await executed(plan_with(drop=("assign_license_monday",)))
    probe = (await dod.check(ctx(rt, run_id, plan), playbook(), only=("user_has_sku",)))[0]
    assert not probe.ok and "no license" in probe.detail


async def test_without_a_created_user_the_user_probes_fail(rt, gated, tenant):
    plan = plan_with()
    run_id = gated(plan)
    probes = by_name(await dod.check(ctx(rt, run_id, plan), playbook(), only=("user_exists", "tap_active")))
    assert not probes["user_exists"].ok and "user.create" in probes["user_exists"].detail
    assert not probes["tap_active"].ok


async def test_a_peer_on_the_hosted_desktop_must_be_handed_to_a_technician(rt, executed):
    run_id, plan = await executed()
    rt.store.append_event(run_id, "tool_call", {"tool": "users.memberOf", "args": {"user_id": MARCUS}, "ok": True,
                                                "error": None, "result": [group(RDS_USERS, "RDS-Users", "security")],
                                                "t": 1.0})
    only = ("needs_technician_listed",)
    probe = (await dod.check(ctx(rt, run_id, plan), playbook(), only=only))[0]
    assert not probe.ok and "RDS-Users" in probe.detail

    listed = plan.model_copy(update={"needs_technician": [
        TechnicianItem(item="Hosted desktop access on NW-TS-01", reason="not in Graph")]})
    assert (await dod.check(ctx(rt, run_id, listed), playbook(), only=only))[0].ok


async def test_message_sent_needs_the_link_and_a_reply_and_ignores_case(rt, executed):
    run_id, plan = await executed()
    only = ("message_sent",)
    probe = (await dod.check(ctx(rt, run_id, plan), playbook(), only=only))[0]
    assert not probe.ok and "no reply" in probe.detail

    rt.store.append_event(run_id, "message_sent", {"kind": "reply", "to": TAYLOR, "subject": "s", "text": "t"})
    assert (await dod.check(ctx(rt, run_id, plan, requester=TAYLOR.upper()), playbook(), only=only))[0].ok


async def test_an_unknown_probe_is_a_failed_check_not_a_crash(rt, executed):
    run_id, plan = await executed()
    book = playbook().model_copy(update={"definition_of_done": [{"user_is_happy": {}}]})
    assert (await dod.check(ctx(rt, run_id, plan), book))[0] == dod.Probe("user_is_happy", False,
                                                                       "no probe for 'user_is_happy'")


async def test_a_graph_error_is_a_failed_check(rt, executed, tenant):
    run_id, plan = await executed()
    tenant.fail("GET", f"/users/{priya(tenant)}", 403)
    probe = (await dod.check(ctx(rt, run_id, plan), playbook(), only=("user_exists",)))[0]
    assert not probe.ok and "403" in probe.detail
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest backend/tests/test_dod.py -v`
Expected: collection error, `ImportError: cannot import name 'dod'`.

- [ ] **Step 4: Add `aka` and the event types**

In `backend/tech/playbooks/loader.py`:

```python
class GroupHandling(BaseModel):
    group: str
    handling: Literal["automatic", "technician"]
    note: str
    aka: list[str] = []  # other names a needs_technician item may use for it (close-out's DoD probe)
```

In `backend/tech/playbooks/client_facts.yaml`, extend the two technician rows:

```yaml
  - {group: "RDS-Users", handling: technician, aka: ["NW-TS-01", "hosted desktop", "RDS"],
     note: "Marks hosted desktop (NW-TS-01) access. Do not mirror; list under needs_technician."}
  - {group: "VPN-Users", handling: technician, aka: ["VPN"],
     note: "Marks Meraki VPN access. Do not mirror; list under needs_technician (VPN approver: kevin@{domain})."}
```

In `backend/tech/core/store.py`, add `"message_blocked", "time_entry"` to `EVENT_TYPES`.

- [ ] **Step 5: Write `dod.py`**

```python
# backend/tech/execution/dod.py
"""Definition-of-done probes (Tech.MD §4.8): after execution, code re-checks the playbook's DoD against live state.

A playbook DoD item is `{probe: args}`. The `$` expressions in args ($new_user.upn, $plan.mirrored_groups...)
document intent; they aren't evaluated. Each probe derives its targets from the run itself (the user.create
step's output, the plan's group.addMember steps, the investigation's memberOf reads), so a playbook can't
point a probe at something the run didn't do. Only `$client.<field>` is resolved, from the client facts.

A probe never raises: a Graph error, or a DoD item with no probe, is a failed check.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from typing import Any

from tech.core.models import Plan
from tech.core.store import Store
from tech.execution import links, scheduler
from tech.graph.client import GraphClient, GraphError
from tech.graph.writes import get_or_none, license_skus, member_group_ids, same
from tech.playbooks import ClientFacts, Playbook

NO_USER = "no user.create step verified in this run"
INACTIVE_TAP = {"Expired", "DisabledByPolicy", "OneTimeUsed"}


@dataclass(frozen=True)
class Probe:
    probe: str
    ok: bool
    detail: str

    def payload(self) -> dict[str, Any]:
        return {"probe": self.probe, "ok": self.ok, "detail": self.detail}


@dataclass
class DodContext:
    store: Store
    graph: GraphClient
    run_id: int
    plan: Plan
    facts: ClientFacts
    requester: str

    def new_user(self) -> dict[str, Any] | None:
        """The user.create step's output in this run: {id, userPrincipalName}."""
        for event in self.store.events_of(self.run_id, "step_verified"):
            if event.payload["action"] == "user.create":
                return event.payload.get("output") or None
        return None

    def mirrored_groups(self) -> dict[str, str]:
        """groupId (lowercase) -> name, for every group.addMember step in the plan."""
        return {s.params.groupId.lower(): s.params.groupName or s.params.groupId
                for s in self.plan.steps if s.action == "group.addMember"}

    def planned_skus(self) -> set[str]:
        return {s.params.skuId.lower() for s in self.plan.steps
                if s.action == "license.assign" or (s.action == "followup.schedule" and s.params.kind == "license.assign")}

    def peer_groups(self) -> set[str] | None:
        """Display names (casefolded) from the investigation's memberOf reads; None if it made none."""
        reads = [e.payload for e in self.store.events_of(self.run_id, "tool_call")
                 if e.payload["tool"] == "users.memberOf" and e.payload["ok"]]
        if not reads:
            return None
        return {(g.get("displayName") or "").casefold() for r in reads for g in r["result"] or []}


Check = Callable[[DodContext, dict[str, Any]], Awaitable[Probe]]
PROBES: dict[str, Check] = {}


def probe(name: str) -> Callable[[Check], Check]:
    def register(fn: Check) -> Check:
        PROBES[name] = fn
        return fn
    return register


@probe("user_exists")
async def user_exists(ctx: DodContext, args: dict[str, Any]) -> Probe:
    if (user := ctx.new_user()) is None:
        return Probe("user_exists", False, NO_USER)
    found = await get_or_none(ctx.graph, f"/users/{user['id']}", params={"$select": "id,userPrincipalName"})
    if found is None:
        return Probe("user_exists", False, f"{user['userPrincipalName']} is not in the directory")
    return Probe("user_exists", True, found["userPrincipalName"])


@probe("user_property")
async def user_property(ctx: DodContext, args: dict[str, Any]) -> Probe:
    if (user := ctx.new_user()) is None:
        return Probe("user_property", False, NO_USER)
    want = {k: getattr(ctx.facts, v.removeprefix("$client."), None)
            if isinstance(v, str) and v.startswith("$client.") else v for k, v in args.items()}
    found = await get_or_none(ctx.graph, f"/users/{user['id']}", params={"$select": ",".join(["id", *want])})
    if found is None:
        return Probe("user_property", False, f"{user['userPrincipalName']} is not in the directory")
    wrong = [f"{k} is {found.get(k)!r}, expected {v!r}" for k, v in want.items() if not same(v, found.get(k))]
    return Probe("user_property", not wrong, "; ".join(wrong) or ", ".join(f"{k}={v}" for k, v in want.items()))


@probe("user_has_sku")
async def user_has_sku(ctx: DodContext, args: dict[str, Any]) -> Probe:
    if (user := ctx.new_user()) is None:
        return Probe("user_has_sku", False, NO_USER)
    if not (skus := ctx.planned_skus()):
        return Probe("user_has_sku", False, "no license: the plan assigns none and schedules none")
    if await license_skus(ctx.graph, user["id"]) & skus:
        return Probe("user_has_sku", True, "has the license")
    later = [f for f in scheduler.for_run(ctx.store, ctx.run_id) if f.kind == "license.assign" and f.status == "scheduled"]
    if later:
        return Probe("user_has_sku", True, f"license follow-up scheduled for {later[0].run_at:%Y-%m-%d %H:%M} UTC")
    return Probe("user_has_sku", False, "no license, and no license follow-up is scheduled")


@probe("user_in_groups")
async def user_in_groups(ctx: DodContext, args: dict[str, Any]) -> Probe:
    if (user := ctx.new_user()) is None:
        return Probe("user_in_groups", False, NO_USER)
    if not (want := ctx.mirrored_groups()):
        return Probe("user_in_groups", True, "the plan mirrors no groups")
    have = await member_group_ids(ctx.graph, user["id"])
    missing = [f"{name} ({gid})" if name.lower() != gid else gid for gid, name in want.items() if gid not in have]
    return Probe("user_in_groups", not missing, f"not in {', '.join(missing)}" if missing else f"in all {len(want)}")


@probe("tap_active")
async def tap_active(ctx: DodContext, args: dict[str, Any]) -> Probe:
    if (user := ctx.new_user()) is None:
        return Probe("tap_active", False, NO_USER)
    taps = [t async for t in ctx.graph.list(f"/users/{user['id']}/authentication/temporaryAccessPassMethods")]
    live = [t for t in taps if t.get("methodUsabilityReason") not in INACTIVE_TAP]
    if not live:
        return Probe("tap_active", False, f"no usable TAP ({len(taps)} found)")
    return Probe("tap_active", True, f"TAP {live[0].get('methodUsabilityReason') or 'present'}")


@probe("message_sent")
async def message_sent(ctx: DodContext, args: dict[str, Any]) -> Probe:
    to = ctx.requester.casefold()
    problems = []
    if not any(link.to.casefold() == to for link in links.for_run(ctx.store, ctx.run_id)):
        problems.append(f"no one-time link to {ctx.requester}")
    if not any(e.payload["kind"] == "reply" for e in ctx.store.events_of(ctx.run_id, "message_sent")):
        problems.append("no reply was sent")
    return Probe("message_sent", not problems, "; ".join(problems) or f"reply and one-time link to {ctx.requester}")


@probe("needs_technician_listed")
async def needs_technician_listed(ctx: DodContext, args: dict[str, Any]) -> Probe:
    if (peer := ctx.peer_groups()) is None:
        return Probe("needs_technician_listed", True, "the investigation didn't read the peer's groups")
    text = " ".join(f"{t.item} {t.reason}" for t in ctx.plan.needs_technician).casefold()
    due = [g for g in ctx.facts.group_handling if g.handling == "technician" and g.group.casefold() in peer]
    missing = [g.group for g in due if not any(n.casefold() in text for n in (g.group, *g.aka))]
    if missing:
        return Probe("needs_technician_listed", False, f"not handed to a technician: {', '.join(missing)}")
    return Probe("needs_technician_listed", True, ", ".join(g.group for g in due) or "nothing outside Graph")


async def check(ctx: DodContext, playbook: Playbook, *, only: Collection[str] | None = None,
                exclude: Collection[str] = ()) -> list[Probe]:
    """Run the playbook's DoD items in order (optionally a subset). Never raises on Graph errors."""
    results = []
    for item in playbook.definition_of_done:
        name, args = next(iter(item.items()))
        if (only is not None and name not in only) or name in exclude:
            continue
        if (fn := PROBES.get(name)) is None:
            results.append(Probe(name, False, f"no probe for {name!r}"))
            continue
        try:
            results.append(await fn(ctx, args or {}))
        except GraphError as exc:
            results.append(Probe(name, False, f"Graph {exc.status} {exc.code}: {exc.message}"))
    return results
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest backend/tests/test_dod.py backend/tests/test_playbooks.py -v`
Expected: all PASS. If `test_a_graph_error_is_a_failed_check` hangs, check that the client doesn't retry 403. It shouldn't; only 429/5xx are retried.

- [ ] **Step 7: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff check backend`

```bash
git add backend/tech/execution/dod.py backend/tech/core/store.py backend/tech/playbooks backend/tests/conftest.py backend/tests/test_dod.py
git commit -m "Add definition-of-done probes, checked against live Graph state"
```

---

### Task 3: Close-out drafting agent (Sonnet)

**Files:**
- Create: `backend/tech/agents/closeout.py`
- Modify: `backend/tests/conftest.py` (add `drafts_model`)
- Test: `backend/tests/test_closeout.py` (drafting section)

**Interfaces:**
- Consumes: `dod.Probe` (Task 2); `links.for_run`, `links.as_utc`; `scheduler.for_run`; `Playbook`.
- Produces:
  - `MODEL = "anthropic:claude-sonnet-5-5"`, `TIMEOUT_S = 45.0`, `LINK_PLACEHOLDER = "{{link}}"`.
  - `CloseoutDrafts(reply_subject: str, reply_body: str, psa_note: str)`.
  - `build_context(store, run_id, plan, probes: list[Probe], *, requester: dict[str, str], playbook: Playbook | None) -> dict[str, Any]`.
  - `async draft(context, *, model=MODEL, timeout_s=TIMEOUT_S) -> CloseoutDrafts`. It raises one of `DRAFT_ERRORS`.
  - `DRAFT_ERRORS: tuple[type[Exception], ...]`.
  - Test helper `conftest.drafts_model(reply=..., note=..., subject=...) -> FunctionModel`.

- [ ] **Step 1: Add `drafts_model` to `backend/tests/conftest.py`** (near the top, after the imports)

```python
def drafts_model(reply: str = "Hi Taylor, Priya is set up. Sign-in link: {{link}}. Use the Outlook mobile app.",
                 note: str = "Created priya.nair; groups mirrored; license follow-up Monday.",
                 subject: str = "Priya Nair is set up"):
    """A FunctionModel standing in for Sonnet at close-out: returns these drafts, and keeps the prompts it got."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    def respond(messages, info):
        respond.prompts.append(messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            "reply_subject": subject, "reply_body": reply, "psa_note": note})])

    respond.prompts = []
    model = FunctionModel(respond)
    model.prompts = respond.prompts
    return model
```

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/test_closeout.py
"""Close-out: Sonnet's drafts (context, no secrets), then the orchestration (DoD, scanner, messages, time)."""

import asyncio
import json

import pytest
from conftest import DOMAIN, TAYLOR, drafts_model
from fake_tenant import TAP_VALUE
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.function import FunctionModel

from tech.agents.closeout import LINK_PLACEHOLDER, CloseoutDrafts, build_context, draft
from tech.execution import dod, links
from tech.playbooks import load_playbook

REQUESTER = {"name": "Taylor", "email": TAYLOR}


def context_for(rt, run_id, plan, probes=()):
    return build_context(rt.store, run_id, plan, list(probes), requester=REQUESTER,
                         playbook=load_playbook("user.onboard", DOMAIN))


# ------------------------------------------------------------------ drafting


async def test_the_context_has_what_was_done_and_no_secret_or_ticket(rt, executed):
    run_id, plan = await executed()
    context = context_for(rt, run_id, plan, [dod.Probe("user_exists", True, "ok")])
    text = json.dumps(context)

    assert context["new_user"]["userPrincipalName"] == f"priya.nair@{DOMAIN}"
    assert context["one_time_link"]["write"] == LINK_PLACEHOLDER
    assert {s["action"] for s in context["steps_done"]} >= {"user.create", "group.addMember", "tap.create"}
    assert context["definition_of_done"] == [{"probe": "user_exists", "ok": True, "detail": "ok"}]
    token = links.for_run(rt.store, run_id)[0].token
    assert TAP_VALUE not in text and token not in text and "new hire Priya" not in text


async def test_draft_returns_the_models_drafts(rt, executed):
    run_id, plan = await executed()
    model = drafts_model()
    drafts = await draft(context_for(rt, run_id, plan), model=model)

    assert isinstance(drafts, CloseoutDrafts) and LINK_PLACEHOLDER in drafts.reply_body
    assert "priya.nair@" in str(model.prompts[0])


async def test_draft_times_out():
    async def slow(messages, info) -> ModelResponse:
        await asyncio.sleep(1)
        raise AssertionError("unreachable")

    with pytest.raises(TimeoutError):
        await draft({"requester": REQUESTER}, model=FunctionModel(slow), timeout_s=0.05)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest backend/tests/test_closeout.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'tech.agents.closeout'`.

- [ ] **Step 4: Write the agent**

```python
# backend/tech/agents/closeout.py
"""Close-out writing (Tech.MD §4.8): Sonnet drafts the requester's reply and the PSA note. Nothing else.

The model gets a summary of the run built here from its events: what was done, follow-ups, the DoD
results and the technician hand-offs. It never gets the ticket text (untrusted, and not needed), a TAP
or a link URL. Where the reply carries the sign-in link it writes LINK_PLACEHOLDER; the snapshot puts the
URL in when the workspace shows it, so the token stays out of events, traces and model output.
The secret scanner still checks both drafts in code before they're recorded (tech/closeout.py).
No tools are registered: the agent only returns CloseoutDrafts.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import ContentFilterError, ModelAPIError, UnexpectedModelBehavior
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModelSettings

from tech.core.models import Plan
from tech.core.store import Store
from tech.execution import links, scheduler
from tech.execution.dod import Probe
from tech.playbooks import Playbook

MODEL = "anthropic:claude-sonnet-5-5"
TIMEOUT_S = 45.0
LINK_PLACEHOLDER = "{{link}}"
DRAFT_ERRORS: tuple[type[Exception], ...] = (ModelAPIError, UnexpectedModelBehavior, ContentFilterError,
                                             TimeoutError)
SETTINGS = AnthropicModelSettings(anthropic_effort="low", anthropic_cache_instructions=True)

INSTRUCTIONS = """\
You write the close-out for an AI technician at an MSP, after a job finished. You get a JSON summary of
the run. Write two texts. State only facts that are in the summary.

reply_subject and reply_body: to the requester (requester.name). Plain text, short and friendly, no markdown.
- Say what was set up for the new user in plain words: no object IDs.
- Sign-in: if one_time_link is present, write {{link}} exactly where the link goes, and say it opens once
  and when it expires. Never write a password, passcode or Temporary Access Pass, or ask for one.
- Recommend the Outlook mobile app for email on a phone; the built-in iOS Mail app can hit "needs admin approval".
- Mention the file shares and team groups the new user got.
- Items under needs_technician: say a technician is handling them now.
- Scheduled follow-ups, such as a license on the start date: say when.
- Anything in out_of_scope_requests: say it was not done and needs a separate request.

psa_note: for a technician reading the ticket later. Terse lines: what was done (UPN, group names),
definition-of-done results, follow-ups, items handed to a technician, requests declined.
"""


class CloseoutDrafts(BaseModel):
    reply_subject: str
    reply_body: str = Field(description=f"Plain text to the requester; write {LINK_PLACEHOLDER} where the link goes")
    psa_note: str = Field(description="Internal note on the ticket, for a technician")


def build_closeout(model: Model | str = MODEL) -> Agent[None, CloseoutDrafts]:
    return Agent(model, output_type=CloseoutDrafts, retries=1, name="closeout", model_settings=SETTINGS)


def build_context(store: Store, run_id: int, plan: Plan, probes: list[Probe], *, requester: dict[str, str],
                  playbook: Playbook | None) -> dict[str, Any]:
    """What the model needs to write the close-out, from the run's events. No secrets, no ticket text."""
    verified = {e.payload["step_id"]: e.payload for e in store.events_of(run_id, "step_verified")}
    done = []
    for step in plan.steps:
        if step.id in verified:
            params = step.params.model_dump(mode="json", by_alias=True, exclude_none=True)
            params.pop("tap", None)
            done.append({"action": step.action, "params": params, "skipped": verified[step.id].get("skipped")})
    new_user = next((v.get("output") for v in verified.values() if v["action"] == "user.create"), None)
    link = next((row for row in links.for_run(store, run_id) if row.to.casefold() == requester["email"].casefold()),
                None)
    return {
        "requester": requester,
        "new_user": new_user,
        "steps_done": done,
        "followups": [{"kind": f.kind, "run_at": links.as_utc(f.run_at).isoformat(), "status": f.status}
                      for f in scheduler.for_run(store, run_id)],
        "one_time_link": {"write": LINK_PLACEHOLDER, "expires_at": links.as_utc(link.expires_at).isoformat(),
                          "includes": link.includes} if link else None,
        "needs_technician": [t.model_dump() for t in plan.needs_technician],
        "out_of_scope_requests": plan.out_of_scope_requests,
        "definition_of_done": [p.payload() for p in probes],
        "gotchas": playbook.gotchas if playbook else [],
    }


async def draft(context: dict[str, Any], *, model: Model | str = MODEL, timeout_s: float = TIMEOUT_S) -> CloseoutDrafts:
    """Sonnet's drafts. Raises one of DRAFT_ERRORS on a model error, refusal or timeout."""
    prompt = "<run>\n" + json.dumps(context, indent=2, default=str) + "\n</run>"
    result = await asyncio.wait_for(build_closeout(model).run(prompt, instructions=INSTRUCTIONS), timeout_s)
    return result.output
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest backend/tests/test_closeout.py -v`
Expected: 3 PASS.

- [ ] **Step 6: Lint and commit**

Run: `uv run ruff check backend`

```bash
git add backend/tech/agents/closeout.py backend/tests/conftest.py backend/tests/test_closeout.py
git commit -m "Add the close-out agent: Sonnet drafts the reply and PSA note from a secret-free summary"
```

---

### Task 4: Close-out orchestration, runner wiring, CLI

**Files:**
- Create: `backend/tech/closeout.py`
- Modify: `backend/tech/runner.py` (`Runtime`, lines 67-88; `execute`, lines 244-267)
- Modify: `backend/tech/cli.py` (`show`, lines 31-67)
- Modify: `backend/tests/conftest.py` (the `rt` fixture uses `drafts_model()`)
- Test: `backend/tests/test_closeout.py` (orchestration section), `backend/tests/test_runner.py`, `backend/tests/test_cli.py`

**Interfaces:**
- Consumes: `scanner.scan` (Task 1); `dod.check`, `dod.DodContext` (Task 2); `build_context`, `draft`, `DRAFT_ERRORS`, `LINK_PLACEHOLDER`, `MODEL` (Task 3); `Secrets.known()` (Task 1).
- Produces:
  - `closeout.WORK_TYPE = "AI Automation"`.
  - `closeout.with_link(body: str, has_link: bool) -> str`.
  - `closeout.work_minutes(states: list[tuple[str, datetime]], end: datetime) -> int`.
  - `async closeout.close_out(store, graph, run_id, plan, *, facts, domain, secrets, model) -> list[str]`, returning the names of failed probes.
  - `Runtime.closeout_model: Any = closeout agent MODEL`.
  - Events:
    - `done_check {probe, ok, detail}`
    - `message_sent {kind: "reply"|"psa_note", to, subject, text}`
    - `message_blocked {kind, reason: "secret"|"dod", hits: [{kind, start}], detail?}`
    - `time_entry {work_type, minutes, started_at, ended_at}`
  - A failed DoD gives a final `state` event `{to: "ESCALATED", kind: "dod", reason, failed_probes, findings}`.

- [ ] **Step 1: Give the `rt` fixture a close-out model**

In `backend/tests/conftest.py`, change the `rt` fixture's return to:

```python
    return Runtime(store=store, graph=graph, facts=facts, domain=DOMAIN, link_key=LINK_KEY, sleep=fake_sleep,
                   closeout_model=drafts_model())
```

- [ ] **Step 2: Write the failing tests**

Merge these into the import block at the top of `backend/tests/test_closeout.py`, in ruff's order:

```python
from datetime import UTC, datetime, timedelta

from pydantic_ai.exceptions import ModelHTTPError

from tech import runner
from tech.closeout import WORK_TYPE, with_link, work_minutes
from tech.core.store import RunState
```

Then append:

```python
# ------------------------------------------------------------------ orchestration


def payloads(rt, run_id, type):
    return [e.payload for e in rt.store.events_of(run_id, type)]


async def test_a_clean_run_closes_out_to_done(rt, gated, tenant):
    run_id = gated()
    assert await runner.approve(rt, run_id, by=TAYLOR) == RunState.DONE

    checks = payloads(rt, run_id, "done_check")
    assert [c["probe"] for c in checks][-1] == "message_sent" and all(c["ok"] for c in checks)
    sent = {m["kind"]: m for m in payloads(rt, run_id, "message_sent")}
    assert sent["reply"]["to"] == TAYLOR and LINK_PLACEHOLDER in sent["reply"]["text"]
    assert sent["psa_note"]["text"]
    entry = payloads(rt, run_id, "time_entry")[0]
    assert entry["work_type"] == WORK_TYPE == "AI Automation" and entry["minutes"] >= 1
    everything = json.dumps([e.payload for e in rt.store.events(run_id)])
    assert TAP_VALUE not in everything and links.for_run(rt.store, run_id)[0].token not in everything


async def test_a_reply_containing_the_tap_is_blocked_and_the_run_escalates(rt, gated, tenant):
    rt.closeout_model = drafts_model(reply=f"Her pass is {TAP_VALUE}, link {{{{link}}}}")
    run_id = gated()

    assert await runner.approve(rt, run_id, by=TAYLOR) == RunState.ESCALATED
    blocked = payloads(rt, run_id, "message_blocked")
    assert blocked[0]["kind"] == "reply" and blocked[0]["reason"] == "secret" and blocked[0]["hits"]
    assert all(m["kind"] != "reply" for m in payloads(rt, run_id, "message_sent"))
    state = rt.store.last_event(run_id, "state").payload
    assert state["kind"] == "dod" and "message_sent" in state["failed_probes"]
    assert TAP_VALUE not in json.dumps([e.payload for e in rt.store.events(run_id)])


async def test_a_failed_graph_probe_holds_the_reply_but_writes_the_psa_note(rt, gated, tenant, monkeypatch):
    from tech.execution import dod as dod_module

    async def no_tap(ctx, args):
        return dod_module.Probe("tap_active", False, "no usable TAP (0 found)")

    monkeypatch.setitem(dod_module.PROBES, "tap_active", no_tap)
    run_id = gated()

    assert await runner.approve(rt, run_id, by=TAYLOR) == RunState.ESCALATED
    assert payloads(rt, run_id, "message_blocked")[0] | {"hits": []} == {
        "kind": "reply", "reason": "dod", "detail": "the definition of done isn't met", "hits": []}
    assert [m["kind"] for m in payloads(rt, run_id, "message_sent")] == ["psa_note"]
    assert rt.store.last_event(run_id, "state").payload["failed_probes"] == ["tap_active", "message_sent"]


async def test_a_model_error_escalates_without_crashing(rt, gated, tenant):
    def broken(messages, info):
        raise ModelHTTPError(status_code=529, model_name="sonnet", body="overloaded")

    rt.closeout_model = FunctionModel(broken)
    run_id = gated()

    assert await runner.approve(rt, run_id, by=TAYLOR) == RunState.ESCALATED
    assert payloads(rt, run_id, "error")[0]["where"] == "closeout"
    assert payloads(rt, run_id, "time_entry")  # still logged


@pytest.mark.parametrize("body", ["Open {{ link }} soon", "Open {{Link}} soon"])
def test_a_mangled_placeholder_is_normalised(body):
    assert with_link(body, True) == f"Open {LINK_PLACEHOLDER} soon"


def test_a_missing_placeholder_gets_a_link_line_only_when_there_is_a_link():
    assert with_link("All set.", True).endswith(f"Sign-in link (opens once): {LINK_PLACEHOLDER}")
    assert with_link("All set.", False) == "All set."


def test_work_minutes_excludes_waiting_for_an_approver_and_mixes_naive_and_aware():
    t0 = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
    states = [("RECEIVED", t0), ("AWAITING_APPROVAL", t0 + timedelta(seconds=70)),
              ("EXECUTING", (t0 + timedelta(minutes=30)).replace(tzinfo=None)),  # SQLite hands back naive
              ("CLOSING", t0 + timedelta(minutes=31))]
    assert work_minutes(states, t0 + timedelta(minutes=31, seconds=20)) == 3  # 70 s + 60 s + 20 s -> 2.5 -> 3
    assert work_minutes([("CLOSING", t0)], t0) == 1
```

In `backend/tests/test_runner.py`, change the first test's state assertion so it also checks close-out ran:

```python
    states = [e.payload["to"] for e in rt.store.events_of(run_id, "state")]
    assert states[-4:] == ["AWAITING_APPROVAL", "EXECUTING", "CLOSING", "DONE"]
    assert rt.store.events_of(run_id, "done_check") and rt.store.events_of(run_id, "time_entry")
```

In `backend/tests/test_cli.py`, add:

```python
def test_show_prints_close_out_events_without_their_text(capsys):
    from tech.cli import show
    from tech.core.store import Event

    show(Event(run_id=1, seq=1, type="done_check", payload={"probe": "user_in_groups", "ok": False, "detail": "x"}))
    show(Event(run_id=1, seq=2, type="message_blocked",
               payload={"kind": "reply", "reason": "secret", "hits": [{"kind": "tap_like", "start": 3}]}))
    show(Event(run_id=1, seq=3, type="message_sent", payload={"kind": "reply", "to": "t@x", "subject": "S", "text": "T"}))
    show(Event(run_id=1, seq=4, type="time_entry", payload={"work_type": "AI Automation", "minutes": 3,
                                                           "started_at": "a", "ended_at": "b"}))
    out = capsys.readouterr().out
    assert "FAILED user_in_groups" in out and "blocked reply (secret: tap_like)" in out
    assert "sent reply to t@x: S" in out and "AI Automation 3 min" in out
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest backend/tests/test_closeout.py backend/tests/test_runner.py backend/tests/test_cli.py -v`
Expected: `ImportError: cannot import name 'with_link'`, `TypeError: Runtime.__init__() got an unexpected keyword argument 'closeout_model'`.

- [ ] **Step 4: Write `tech/closeout.py`**

```python
# backend/tech/closeout.py
"""Close-out (Tech.MD §4.8): what happens once every step has verified. The runner calls `close_out`.

  1. DoD probes (code, execution/dod.py) against live state; each is a done_check event.
  2. Sonnet drafts the reply and the PSA note (agents/closeout.py). A model error is an error event.
  3. The secret scanner checks each text against patterns and every secret the run holds (the TAP, the
     new user's password, link tokens). A hit blocks that text: message_blocked, with hit kinds and
     offsets, never the text. The reply also waits for the DoD: it isn't sent while a probe has failed.
     The PSA note is always written, because it is the technician's hand-off.
  4. Texts that pass are message_sent: written to our ticket view (no PSA or email yet, Week 5).
  5. The message_sent probe, now that the reply went out or didn't.
  6. A time entry: work type "AI Automation", the run's working minutes (time waiting on an approver excluded).

Returns the failed probes; the runner moves the run to DONE, or to ESCALATED with them.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

from tech.agents.closeout import DRAFT_ERRORS, LINK_PLACEHOLDER, build_context, draft
from tech.agents.closeout import MODEL as CLOSEOUT_MODEL
from tech.core.models import Plan
from tech.core.store import RunState, Store, utcnow
from tech.execution import dod, links
from tech.graph.client import GraphClient
from tech.graph.writes import Secrets
from tech.playbooks import ClientFacts, load_playbooks
from tech.safety.scanner import scan

WORK_TYPE = "AI Automation"
WAITING = {RunState.AWAITING_APPROVAL.value, RunState.HELD.value}
PLACEHOLDER_RE = re.compile(r"\{\{\s*link\s*\}\}", re.IGNORECASE)


def with_link(body: str, has_link: bool) -> str:
    """Normalise the model's link placeholder; add a link line if it left one out."""
    body = PLACEHOLDER_RE.sub(LINK_PLACEHOLDER, body)
    if has_link and LINK_PLACEHOLDER not in body:
        body = f"{body.rstrip()}\n\nSign-in link (opens once): {LINK_PLACEHOLDER}"
    return body


def work_minutes(states: list[tuple[str, datetime]], end: datetime) -> int:
    """Minutes the run spent working, from its state changes: every interval except waiting on an approver."""
    seconds = 0.0
    ends = [at for _, at in states[1:]] + [end]
    for (state, start), stop in zip(states, ends, strict=True):
        if state not in WAITING:
            seconds += (links.as_utc(stop) - links.as_utc(start)).total_seconds()
    return max(1, math.ceil(seconds / 60))


def record(store: Store, run_id: int, kind: str, *, to: str | None, subject: str | None, text: str,
           known: set[str], held: str | None = None) -> bool:
    """Scan one outbound text, then record it as sent, or as blocked (by a secret, or held for the DoD)."""
    if hits := scan(f"{subject or ''}\n{text}", known=known):
        store.append_event(run_id, "message_blocked", {"kind": kind, "reason": "secret",
                                                       "hits": [h.payload() for h in hits]})
        return False
    if held:
        store.append_event(run_id, "message_blocked", {"kind": kind, "reason": "dod", "detail": held, "hits": []})
        return False
    store.append_event(run_id, "message_sent", {"kind": kind, "to": to, "subject": subject, "text": text})
    return True


async def close_out(store: Store, graph: GraphClient, run_id: int, plan: Plan, *, facts: ClientFacts, domain: str,
                    secrets: Secrets, model: Any = CLOSEOUT_MODEL) -> list[str]:
    """Run the close-out for an executed run (state CLOSING). Returns the names of failed DoD probes."""
    who = store.last_event(run_id, "triage").payload["triage"]["requester"]
    requester = {"name": who["name"], "email": who["email"]}
    playbook = load_playbooks([plan.intent], domain).get(plan.intent)
    ctx = dod.DodContext(store, graph, run_id, plan, facts, requester["email"])

    probes = await dod.check(ctx, playbook, exclude=("message_sent",)) if playbook else []
    for p in probes:
        store.append_event(run_id, "done_check", p.payload())

    run_links = links.for_run(store, run_id)
    known = secrets.known() | {row.token for row in run_links}
    has_link = any(row.to.casefold() == requester["email"].casefold() for row in run_links)
    try:
        drafts = await draft(build_context(store, run_id, plan, probes, requester=requester, playbook=playbook),
                             model=model)
    except DRAFT_ERRORS as exc:
        store.append_event(run_id, "error", {"where": "closeout", "error": f"{type(exc).__name__}: {exc}"})
        drafts = None

    if drafts is not None:
        record(store, run_id, "reply", to=requester["email"], subject=drafts.reply_subject,
               text=with_link(drafts.reply_body, has_link), known=known,
               held=None if all(p.ok for p in probes) else "the definition of done isn't met")
        record(store, run_id, "psa_note", to=None, subject=None, text=drafts.psa_note, known=known)

    if playbook:
        for p in await dod.check(ctx, playbook, only=("message_sent",)):
            probes.append(p)
            store.append_event(run_id, "done_check", p.payload())

    states = [(e.payload["to"], e.created_at) for e in store.events_of(run_id, "state")]
    end = utcnow()
    store.append_event(run_id, "time_entry", {
        "work_type": WORK_TYPE, "minutes": work_minutes(states, end),
        "started_at": links.as_utc(states[0][1]).isoformat(), "ended_at": end.isoformat(),
    })
    return [p.probe for p in probes if not p.ok]
```

- [ ] **Step 5: Wire it into the runner**

In `backend/tech/runner.py`:
- Add `from tech import closeout` and `from tech.agents.closeout import MODEL as CLOSEOUT_MODEL` to the imports.
- Add `closeout_model: Any = CLOSEOUT_MODEL` to `Runtime`, after `sleep`.
- Update the module docstring's state line to mention close-out.
- Replace the `if result.ok:` block in `execute` with:

```python
    if result.ok:
        transition(rt.store, run_id, S.CLOSING)
        failed = await closeout.close_out(rt.store, rt.graph, run_id, plan, facts=rt.facts, domain=rt.domain,
                                          secrets=execution.secrets, model=rt.closeout_model)
        if failed:
            transition(rt.store, run_id, S.ESCALATED, kind="dod",
                       reason=f"definition of done not met: {', '.join(failed)}", failed_probes=failed,
                       findings=findings(rt.store, run_id))
        else:
            transition(rt.store, run_id, S.DONE)
        return
```

Then check for an import cycle: `uv run python -c "import tech.runner"`. `tech.closeout` must not import `tech.runner`.

- [ ] **Step 6: Print close-out events in the CLI**

In `backend/tech/cli.py`, add these cases to `show`:

```python
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
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest backend/tests/test_closeout.py backend/tests/test_runner.py backend/tests/test_cli.py -v`
Expected: all PASS.

- [ ] **Step 8: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff check backend`
Expected: all green. `test_api.py` must pass unchanged, because it builds its app from `rt`.

```bash
git add backend/tech/closeout.py backend/tech/runner.py backend/tech/cli.py backend/tests
git commit -m "Close out executed runs: DoD probes, scanned reply and PSA note, time entry; escalate on a failed DoD"
```

---

### Task 5: Snapshot `closeout` block (API)

**Files:**
- Modify: `backend/tech/closeout.py` (add `view`)
- Modify: `backend/tech/api.py` (`snapshot`, lines 143-195)
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: the Task 4 events; `link_rows` as `snapshot` already builds them (`{"to", "url", "viewed_at", ...}`).
- Produces: `closeout.view(store, run_id, link_rows: list[dict]) -> dict` and `snapshot()["closeout"]`, shaped as:
  `{"checks": [{probe, ok, detail}], "messages": [{kind, to, subject, text}], "blocked": [{kind, reason, hits, detail?}], "time_entry": {work_type, minutes, started_at, ended_at} | None}`.
  In each message's `text`, `{{link}}` is replaced by the requester's link URL while it is unviewed, `(the one-time link, already opened)` once it has been viewed, and `(no link)` if there is none.

- [ ] **Step 1: Write the failing test** (append to `backend/tests/test_api.py`)

```python
async def test_snapshot_shows_the_close_out_with_the_link_filled_in(app, client, rt, gated, tenant):
    run_id = await approved_run(app, client, gated)

    closeout = (await client.get(f"/runs/{run_id}")).json()["closeout"]
    assert closeout["checks"] and all(c["ok"] for c in closeout["checks"])
    assert closeout["time_entry"]["work_type"] == "AI Automation"
    reply = next(m for m in closeout["messages"] if m["kind"] == "reply")
    token = links.for_run(rt.store, run_id)[0].token
    assert f"{ORIGIN}/l/{token}" in reply["text"] and "{{link}}" not in reply["text"]
    assert token not in everything(rt, run_id)  # filled in when served, never stored

    await client.post(f"/l/{token}/reveal")
    reply = next(m for m in (await client.get(f"/runs/{run_id}")).json()["closeout"]["messages"] if m["kind"] == "reply")
    assert token not in reply["text"] and "already opened" in reply["text"]


async def test_snapshot_closeout_is_empty_before_execution(client, gated):
    run_id = gated()
    assert (await client.get(f"/runs/{run_id}")).json()["closeout"] == {
        "checks": [], "messages": [], "blocked": [], "time_entry": None}
```

If `links` is not yet imported in `test_api.py`, add `from tech.execution import links`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest backend/tests/test_api.py -k closeout -v`
Expected: FAIL with `KeyError: 'closeout'`.

- [ ] **Step 3: Add `view` to `backend/tech/closeout.py`**

```python
def view(store: Store, run_id: int, link_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The close-out as the workspace shows it. The reply's link placeholder becomes the link's URL while it's
    unviewed, so the token is only ever in the served snapshot, never in an event."""
    triage = store.last_event(run_id, "triage")
    to = triage.payload["triage"]["requester"]["email"].casefold() if triage else ""
    link = next((row for row in link_rows if row["to"].casefold() == to), None)
    fill = (link["url"] or "(the one-time link, already opened)") if link else "(no link)"
    entry = store.last_event(run_id, "time_entry")
    return {
        "checks": [e.payload for e in store.events_of(run_id, "done_check")],
        "messages": [e.payload | {"text": e.payload["text"].replace(LINK_PLACEHOLDER, fill)}
                     for e in store.events_of(run_id, "message_sent")],
        "blocked": [e.payload for e in store.events_of(run_id, "message_blocked")],
        "time_entry": entry.payload if entry else None,
    }
```

- [ ] **Step 4: Use it in `snapshot`**

In `backend/tech/api.py`, add `from tech import closeout, runner` (replacing `from tech import runner`). Then add this key to the dict `snapshot` returns, after `"links": link_rows,`:

```python
        "closeout": closeout.view(store, run_id, link_rows),
```

Update the module docstring line for `GET /runs/{id}` to say it includes the close-out.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest backend/tests/test_api.py -v`
Expected: all PASS.

- [ ] **Step 6: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff check backend`

```bash
git add backend/tech/closeout.py backend/tech/api.py backend/tests/test_api.py
git commit -m "Serve the close-out in the run snapshot, with the one-time link filled in"
```

---

### Task 6: Workspace close-out sections (web)

**Files:**
- Modify: `web/lib/types.ts` (`Snapshot`)
- Modify: `web/lib/useRun.ts:12-16` (`EVENT_TYPES`)
- Modify: `web/lib/run.ts:44-46` (`REFRESH_ON`)
- Modify: `web/components/Closeout.tsx` (add `DefinitionOfDone`, `Messages`, `TimeEntry`)
- Modify: `web/components/Workspace.tsx`
- Test: `web/tests/run.test.ts`

**Interfaces:**
- Consumes: `snapshot().closeout` (Task 5).
- Produces: the TypeScript types `DoneCheck`, `OutboundMessage`, `BlockedMessage`, `TimeEntry`, and `Snapshot["closeout"]`; the components `DefinitionOfDone({ checks })`, `Messages({ messages, blocked })`, `TimeEntry({ entry })`.

- [ ] **Step 1: Read the Next docs this touches**

Run: `ls web/node_modules/next/dist/docs/` and read the client components guide. These components are `"use client"` modules imported by `Workspace.tsx`, like the existing ones. No new route is added.

- [ ] **Step 2: Write the failing test** (append to `web/tests/run.test.ts`)

```ts
import { REFRESH_ON } from "../lib/run";

describe("close-out events", () => {
  it("refresh the snapshot", () => {
    for (const type of ["done_check", "message_sent", "message_blocked", "time_entry"]) {
      expect(REFRESH_ON.has(type)).toBe(true);
    }
  });
});
```

Merge `REFRESH_ON` into the file's existing `../lib/run` import rather than adding a second import line.

- [ ] **Step 3: Run it to verify it fails**

Run: `cd web && npm test`
Expected: FAIL, `expected false to be true`.

- [ ] **Step 4: Types, event list, refresh list**

In `web/lib/types.ts`, add these types and a `closeout` field on `Snapshot`:

```ts
export interface DoneCheck { probe: string; ok: boolean; detail: string }
export interface OutboundMessage { kind: "reply" | "psa_note"; to: string | null; subject: string | null; text: string }
export interface BlockedMessage {
  kind: "reply" | "psa_note"; reason: "secret" | "dod"; hits: { kind: string; start: number }[]; detail?: string;
}
export interface TimeEntry { work_type: string; minutes: number; started_at: string; ended_at: string }

// in Snapshot:
  closeout: { checks: DoneCheck[]; messages: OutboundMessage[]; blocked: BlockedMessage[]; time_entry: TimeEntry | null };
```

In `web/lib/useRun.ts`, add `"message_blocked", "time_entry"` to `EVENT_TYPES`. In `web/lib/run.ts`, add `"done_check", "message_sent", "message_blocked", "time_entry"` to `REFRESH_ON`.

- [ ] **Step 5: Components** (append to `web/components/Closeout.tsx`; extend its type import)

```tsx
export function DefinitionOfDone({ checks }: { checks: DoneCheck[] }) {
  const met = checks.every((c) => c.ok);
  return (
    <Section title="Definition of done" tone={met ? "good" : "bad"} aside={<span className="text-xs text-neutral-500">checked live in Graph</span>}>
      <ul className="space-y-1 text-sm">
        {checks.map((c) => (
          <li key={c.probe} className="flex gap-2">
            <span className={c.ok ? "text-emerald-700" : "text-red-700"}>{c.ok ? "✔" : "✖"}</span>
            <span className="font-mono">{c.probe}</span>
            <span className="text-neutral-500">{c.detail}</span>
          </li>
        ))}
      </ul>
    </Section>
  );
}

const MESSAGE_TITLE = { reply: "Reply to requester", psa_note: "PSA note" } as const;

export function Messages({ messages, blocked }: { messages: OutboundMessage[]; blocked: BlockedMessage[] }) {
  return (
    <>
      {blocked.map((b) => (
        <Section key={`blocked-${b.kind}`} title={`${MESSAGE_TITLE[b.kind]}: not sent`} tone="bad">
          <p className="text-sm">
            {b.reason === "secret"
              ? `The secret scanner blocked it (${b.hits.map((h) => h.kind).join(", ")}). Nothing was sent.`
              : `Held: ${b.detail ?? "the definition of done isn't met"}.`}
          </p>
        </Section>
      ))}
      {messages.map((m) => (
        <Section key={m.kind} title={MESSAGE_TITLE[m.kind]}>
          {m.to && <p className="text-xs text-neutral-500">To {m.to}</p>}
          {m.subject && <p className="mb-2 font-medium">{m.subject}</p>}
          <p className="whitespace-pre-wrap text-sm">{m.text}</p>
        </Section>
      ))}
    </>
  );
}

export function TimeEntry({ entry }: { entry: TimeEntryT }) {
  return (
    <Section title="Time entry">
      <p className="text-sm"><span className="font-mono">{entry.work_type}</span> · {entry.minutes} min</p>
      <p className="text-xs text-neutral-500">working time, excluding time waiting for an approver</p>
    </Section>
  );
}
```

Import the types as `import type { BlockedMessage, DoneCheck, OutboundMessage, Plan, Snapshot, TimeEntry as TimeEntryT } from "@/lib/types";`. The alias avoids a clash with the component name.

- [ ] **Step 6: Place them in the workspace**

In `web/components/Workspace.tsx`, import `DefinitionOfDone, Messages, TimeEntry` from `./Closeout`. Insert them right after the `Execution` line, before `Followups`:

```tsx
      {snap.closeout.checks.length > 0 && <DefinitionOfDone checks={snap.closeout.checks} />}
      {(snap.closeout.messages.length > 0 || snap.closeout.blocked.length > 0) && (
        <Messages messages={snap.closeout.messages} blocked={snap.closeout.blocked} />
      )}
      {snap.closeout.time_entry && <TimeEntry entry={snap.closeout.time_entry} />}
```

- [ ] **Step 7: Verify**

Run: `cd web && npm test && npx tsc --noEmit && npm run lint && npm run build`
Expected: all clean. The fixture test still ends at seq 48, because the captured stream predates close-out.

- [ ] **Step 8: Commit**

```bash
git add web/lib web/components web/tests
git commit -m "Show the close-out in the workspace: definition of done, reply, PSA note, time entry"
```

---

### Task 7: Live run and docs

**Files:**
- Modify: `docs/demo/Build-W1-2.md` (Day 8)
- Modify: `backend/tests/test_live_onboard.py` (close-out assertions)

- [ ] **Step 1: Extend the live test**

After the existing `== RunState.DONE` assertion in `backend/tests/test_live_onboard.py`, add:

```python
        checks = [e.payload for e in store.events_of(run_id, "done_check")]
        assert checks and all(c["ok"] for c in checks), checks
        reply = next(e.payload for e in store.events_of(run_id, "message_sent") if e.payload["kind"] == "reply")
        assert "{{link}}" in reply["text"] and "outlook" in reply["text"].lower()
        assert store.events_of(run_id, "time_entry")
```

- [ ] **Step 2: Run it live**

Run: `uv run tenant/reset.py && uv run pytest -m live backend/tests/test_live_onboard.py -v`
Expected: PASS.
- If the Outlook assertion fails, read the reply. Tighten `INSTRUCTIONS` and don't loosen the test.
- If `needs_technician_listed` fails, compare the plan's `needs_technician` text with the `aka` names, and add the missing alias to `client_facts.yaml`.

- [ ] **Step 3: Run it in the UI**

Run `uv run tenant/reset.py`, then `uv run uvicorn tech.api:create_app --factory --port 8000 --no-access-log` and, in `web/`, `npm run dev`. Submit the Priya sample as Taylor and approve, confirming `priya.nair`. Then check:
- The state goes CLOSING → DONE.
- **Definition of done** shows 7 ✔.
- **Reply to requester** shows a clickable `/l/…` URL and the Outlook tip.
- **PSA note** and **Time entry** appear.
- Open the link and reload the workspace: the reply now reads "already opened".
- Note the elapsed time against the 3-minute bar, and the close-out's share of it in Logfire, for Day 9.

- [ ] **Step 4: Update the docs**

In `docs/demo/Build-W1-2.md` Day 8:
- Mark `scanner.py`, the probes, the drafts and the time entry as done, with a pointer to [Build-Day8.md](Build-Day8.md).
- Note the file split (`execution/dod.py`, `tech/closeout.py`) and that a failed DoD escalates (`kind="dod"`).

- [ ] **Step 5: Commit**

```bash
git add docs/demo/Build-W1-2.md backend/tests/test_live_onboard.py
git commit -m "Day 8 done: live close-out check, docs"
```

## Verification

1. `uv run pytest` and `uv run ruff check backend`: green, offline.
2. In `web/`: `npm test`, `npx tsc --noEmit`, `npm run lint`, `npm run build`: clean.
3. `uv run pytest -m live` after `tenant/reset.py`: the onboarding reaches DONE with every DoD check ✔ and a reply that carries the link placeholder and the Outlook tip.
4. Manual UI run (Task 7, Step 3).
5. `grep` the event table for the TAP. After a live run, every event payload is free of the TAP and the link token. The live test already asserts the TAP part.

## Risks

- **Scanner false positives on Sonnet prose.** The TAP-like heuristic requires all four character classes plus a symbol outside URL, email and GUID characters. If a live reply is blocked, read the hit kind in the event. Fix the pattern and add the phrase to `test_a_normal_reply_and_psa_note_are_clean`. Don't weaken the known-value check.
- **Close-out adds wall-clock time:** one Sonnet call at low effort, about 5–10 s, plus about 6 Graph reads. Day 9 measures it. If it threatens the 3-minute bar, run the Graph probes and the draft concurrently. The reply is held on the DoD in code anyway.
- **The model drifts on the placeholder.** `with_link` normalises it and appends a link line, so the link can't go missing. The only remaining failure is a URL the model invents, which can't happen because the model never sees one.
- **Runs that escalate during execution get no close-out text.** That is deliberate for now. Tech.MD's escalation write-up (Sonnet) is Week 3, alongside the replanner.
