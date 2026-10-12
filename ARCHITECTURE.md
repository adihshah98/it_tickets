# Architecture — The AI Technician

*Last updated: 2026-10-12*

A ground-up tour of the codebase: directory → file → function → how it flows → why it's shaped that way.
Read this top-to-bottom once and you should be able to open any file and know where you are.

Companion docs: [docs/overall/tech-plan.md](docs/overall/tech-plan.md) (the design this implements) ·
[docs/overall/expansion-to-multi-customers.md](docs/overall/expansion-to-multi-customers.md) (what changes to sell it to MSPs).

---

## 1. What the system does, in one breath

A support ticket (email text + a sender) comes in. A cheap model **triages** it, a strong model
**investigates** it with read-only tools and proposes a **typed plan**, deterministic code **lints and
gates** that plan, a human **approves** it, and code **executes** it against a real Microsoft 365 tenant —
snapshotting, acting, and verifying every write by reading it back — then **closes out** with a reply, a
PSA note, and a definition-of-done check. Everything is an event in an append-only log, which is
simultaneously the UI feed, the audit trail, and the resume log.

The one sentence that explains every design choice:

> **The model decides and plans; typed, tested code acts. The model can never call a write tool.**

---

## 2. The governing idea: a state machine over an event log

A ticket becomes a **Run**. A run moves through states; **every** fact about it is an `Event` appended to a
per-run, monotonically-numbered log. Nothing about a run lives only in memory — the plan, the triage, the
approver's answers, which steps finished, their outputs, are all reconstructed by replaying events. That is
what lets a run **resume in a fresh process** after a crash, lets the CLI and the API drive the *same* run,
and gives you audit + replay for free.

```
RECEIVED → TRIAGED → INVESTIGATING → PLAN_PROPOSED → PLAN_CHECKED
    ├─ (nothing to approve) ─────────────→ EXECUTING → CLOSING → DONE
    ├─ AWAITING_APPROVAL → (approve) ─────→ EXECUTING → …
    ├─ HELD → (approver confirms) ────────→ EXECUTING → …
    └─ EXECUTING → REPLANNING → EXECUTING  (Week 3, deviation handling)

any live state ─────────────────────────→ ESCALATED  (or HELD)
```

The legal moves are a hard table (`TRANSITIONS` in [runner.py](backend/tech/runner.py)); an illegal jump
raises. `TERMINAL = {DONE, ESCALATED}`.

---

## 3. Directory map

```
it_tickets/
├─ backend/tech/            the engine (Python, ~3.3k LOC)
│  ├─ runner.py             the state machine — orchestrates every phase
│  ├─ closeout.py           the close-out phase (DoD, reply, PSA note, time entry)
│  ├─ api.py                FastAPI: REST + server-sent events for the web UI
│  ├─ cli.py                headless runs (tech run / approve / resume / followup)
│  ├─ core/
│  │  ├─ config.py          Settings, loaded from .env files
│  │  ├─ observability.py   Logfire setup + secret scrubbing
│  │  ├─ store.py           SQLModel tables + the event log + in-process pub/sub
│  │  └─ models/            the typed contracts between model and code
│  │     ├─ base.py         IDs, $ref syntax, Intent/RiskClass literals  (leaf: no cycles)
│  │     ├─ triage.py       Triage (intake's output)
│  │     ├─ params.py       per-action parameter models (Graph-shaped)
│  │     ├─ plan.py         Plan + the discriminated Step union
│  │     └─ gate.py         GateDecision
│  ├─ agents/               the model-driven steps (PydanticAI agents)
│  │  ├─ intake.py          Haiku → Triage
│  │  ├─ investigator.py    Opus + read-only tools → Plan
│  │  └─ closeout.py        Sonnet → reply + PSA note drafts
│  ├─ safety/               the deterministic guardrails (no model here)
│  │  ├─ lint.py            output validator: 7 rules on a proposed plan
│  │  ├─ policy.py          group-target facts the linter and gate both judge
│  │  ├─ gate.py            the policy engine: auto | approve | block per step
│  │  └─ scanner.py         secret scanner for all outbound text
│  ├─ execution/            turning an approved plan into real changes
│  │  ├─ catalogue.py       the write-action registry (risk class, verify, rollback)
│  │  ├─ executor.py        runs the plan: order → snapshot → act → verify
│  │  ├─ links.py           one-time encrypted credential links
│  │  ├─ scheduler.py       follow-ups (work scheduled for later)
│  │  ├─ mocks.py           mocked actions (license.purchase = Pax8)
│  │  └─ dod.py             definition-of-done probes against live state
│  ├─ graph/                the Microsoft Graph layer
│  │  ├─ client.py          async Graph client with the retry policy
│  │  ├─ reads.py           read tools + the Transcript of observed IDs
│  │  └─ writes.py          write handlers (snapshot/act/verify) + the secret guard
│  └─ playbooks/            runbooks-as-data
│     ├─ loader.py          ClientFacts + Playbook models; load + prompt text
│     ├─ client_facts.yaml  the one demo client's facts
│     ├─ user.onboard.yaml  the onboarding runbook
│     └─ user.offboard.yaml the offboarding runbook
├─ backend/tests/           256 tests (pytest, offline + a `live` tier)
├─ web/                     Next.js console (SSE + snapshot + pure reducer)
├─ tenant/                  scripts to set up / seed / reset the demo M365 tenant
├─ spike/                   model-risk experiments
└─ docs/                    product + tech plans, day-by-day build log
```

---

## 4. The typed contracts (`core/models/`) — read this first

Everything downstream is shaped by these. The package was split so a leaf module (`base.py`) can be imported
by all the others without an import cycle; `models/__init__.py` re-exports the whole surface, so every
`from tech.core.models import …` is unchanged.

### `base.py` — primitives
- `GUID_RE` — the regex that defines "a real object ID." The linter uses it to find every ID in a plan.
- `parse_ref("$ref:s1.output.id") → ("s1", "id")` — the syntax by which **a later step references an
  earlier step's output** (e.g. the license step uses the user the create step just made). A `.output.tap`
  ref is special: it names a secret that is *never* resolved into the plan.
- `ObjectId` / `ObjectIdOrRef` / `TapRef` — annotated `str` types with regex patterns. An ID field must be
  *either* a GUID *or* a `$ref`, enforced by Pydantic at parse time. This is the first line of defense
  against a hallucinated ID.
- `Intent` and `RiskClass` — the closed vocabularies. `RiskClass` is the spine of the safety model:
  `read, write.reversible, access.revoke, data.access, security.credential, spend, destructive.soft, none`.
- `Params` base — `extra="forbid"` (unknown fields rejected — e.g. the model can't sneak in a
  `passwordProfile`), and accepts both Graph camelCase and the model's snake_case via aliases.

### `triage.py` — `Triage`
Intake's output: `intents` (a list — one ticket can hold several), extracted `entities`, the `requester`,
`out_of_scope_asks`, `missing_info`. Pure extraction; no authority judgement here.

### `params.py` — one model per action
Graph-named fields with snake_case aliases. Semantic validators live here, not in prose:
`UserCreateParams.nickname_matches_upn`, `UserUpdateParams.changes_something`,
`MailboxForwardRuleParams.internal_only` (a cheap first guard against free-mail forwarding; the handler
enforces the real internal-only check against the live tenant domain).

### `plan.py` — `Plan` and the `Step` union
`Step` is a **discriminated union on the `action` field**. Each concrete step
(`UserCreateStep`, `LicenseAssignStep`, …) binds one `action` literal to one params model. This is the single
most important shape in the repo: the model is forced to emit exactly the right parameters for whatever
action it chose, and `STEP_TYPES` (derived from the union by reflection) lets code look a step type up by
name. A `Plan` also carries `assumptions`, `confirm_on_approval`, `questions_for_requester`,
`needs_technician`, `out_of_scope_requests`, and `escalate` — so "stop and ask" and "escalate" are typed
outcomes, not free text.

---

## 5. The pipeline, phase by phase

### 5.0 Intake — `agents/intake.py` (Haiku)
`run_intake()` runs the cheap model to produce a `Triage`, then does two things the model is **not** trusted
with:
- **Overrides the requester email** with the actual `from_address` — whoever the email came from, not whoever
  the body claims to be.
- **`check_requester()`** — against `ClientFacts.approvers`, in code, decides per intent whether the sender is
  an approved requester. The result (`RequesterCheck.approved`) rides along; it does not change the plan, it
  changes whether the run will be *held*.

Records a `triage` event, moves to `TRIAGED`.

### 5.1 Investigation — `agents/investigator.py` (Opus)
Where the real reasoning happens, under tight bounds. `investigate()`:
- Loads the playbooks for the triaged intents and builds the instructions (client facts + playbooks as YAML,
  comments included — the comments *are* the reasoning the model needs).
- Builds a PydanticAI `Agent` whose tools are **only the read tools + `record_finding`**. There is no write
  tool object anywhere in the agent's reach. Writes are unreachable *by construction*, not by prompting.
- Every read goes through `_read()` → `run_read()`, which (a) enforces the **read budget** (`MAX_READS=25`;
  when spent, tools return "budget used up, submit now" rather than failing the run), (b) records a
  `tool_call` event, and (c) populates the **`Transcript`** of observed object IDs.
- The agent's `output_type` is `ToolOutput(Plan)` with an **output validator, `lint_plan`** — so a bad plan
  goes *back to the model* as a `ModelRetry` with the problems spelled out (max 2 retries), each a
  `lint_result` event.
- Hard caps on requests/tool-calls plus a 90s wall-clock timeout. A timeout, usage-limit, refusal, model
  error, or an unusable output all land in one place: `ESCALATED` with the findings so far. **A stuck run is
  not a possible outcome.**

On success: a `plan` event (with token-usage accounting), then `PLAN_PROPOSED` — or `ESCALATED` if the model
chose `escalate=true` (a normal, good outcome).

> Why the `Transcript` matters: it is the ground truth the linter checks the plan against. The model cannot
> use an ID it never actually looked up.

### 5.2 Lint — `safety/lint.py` (the output validator)
`lint()` returns a list of plain-English problems; empty means the plan passes. Seven rules:
1. **Schema** — action is in the catalogue, params validate, step ids are unique.
2. **Grounded** — every GUID in the params was in the `Transcript`; every `$ref` points to an *earlier* step
   it depends on and that actually produces that output (`PRODUCES` maps output kind → producing action).
3. **In scope** — the action is allowed by a triaged intent's playbook (`allowed_actions`); a group add
   targets a group the agent may change (`policy.group_problem`).
4. **Covers DoD** — every required definition-of-done item has a step that could satisfy it (`DOD_PRODUCERS`),
   or is covered under `needs_technician`.
5. **Must-investigate** — every tool the playbook marks `must_investigate` was actually called.
6. **Questions resolved** — every `questions_to_resolve` topic is answered (an assumption or a confirm field)
   or deferred to the requester. Deliberately lenient (keyword presence): a false rejection wastes a scarce
   retry, and the gate still gates.
7. **Ordering sane** — `depends_on` points only backwards; no cycles.

Rules 4–6 need a playbook, so they're skipped for an escalation or a no-playbook plan. `policy.group_problem`
is shared with the gate on purpose: the linter uses it to send the model back to fix things; the gate checks
it *again* and blocks, so a plan that somehow skipped the linter still can't add anyone to a privileged or
unmanageable group.

### 5.3 Gate — `safety/gate.py` (the policy engine)
Pure function `evaluate()` + a recording wrapper `check()`. The cardinal rule: **risk classes come from the
catalogue, never from the plan** (the plan's self-assessed `risk_class` is only logged, for the audit trail).

Per step, verdicts are gathered in a fixed **rule order** and resolved by `_Verdicts.final()` (first *block*
wins; otherwise the strictest of `auto < approve < block`):
1. unknown action → block; `group.addMember` to a forbidden group → block; action not in the playbook → block.
2. **autonomy level** (`autonomy_level()` takes the strictest across the step's intents): **A1** blocks every
   write (the client runs this intent as a technician checklist); **A2** leaves the risk rules in force;
   **A3** auto-approves only `write.reversible`/`access.revoke` and *never* the classes a person must see.
3. **risk-class rule** (`_risk_rule`): `spend`, `data.access`, `security.credential`, `destructive.soft`
   **always** need approval; `access.revoke` is auto only when it's an approved offboarding; everything else
   reversible is auto.
4. **blast radius** (run-level): more than `BLAST_RADIUS=20` writes of one class → approve;
   more than `MAX_WRITES=40` total → block.

`check()` writes a `gate_decision` event per step, sets `PLAN_CHECKED`, then routes:
- requester not approved → **HELD** (needs an approver to confirm the requester first);
- any block → **ESCALATED** (kind `gate`);
- anything to approve → **AWAITING_APPROVAL**;
- otherwise the run stays `PLAN_CHECKED` and the runner executes immediately.

### 5.4 Approval — `runner.accept()` / `approve()`
`accept()` is **synchronous on purpose**: the state check, the approver check, and the transition happen with
nothing awaited in between, so a second approval of the same run can't slip through. It verifies the approver
is on the client's list for the relevant scopes (`approval_scopes()` = each triaged intent's list, plus
`spend` if a purchase is in the plan), refuses if the gate blocked anything, applies the approver's answers,
records an `approval` event, and transitions to `EXECUTING` (or `ESCALATED` on reject). The async `approve()`
wraps `accept()` + `execute()`.

**`apply_answers()`** is subtle and important: an approver may override specific *values* (the UPN format, the
start date) but **never actions or IDs** — so the gate's decisions still hold after an edit. It even rewrites
the verify `expected` dicts to match the new UPN, so verification stays consistent.

### 5.5 Execution — `execution/executor.py`
Code only, no model. `Execution.run(plan)`:
- `order()` — topological sort over `depends_on` **plus** the edges implied by `$ref`s; raises on a cycle or
  unknown step.
- For each not-yet-verified step, `run_step()`:
  1. `resolve()` — substitute `$ref:step.output.id` from earlier outputs (a `.output.tap` ref is left alone —
     the link handler reads the TAP from in-memory `Secrets`, so it never enters params or events).
  2. `handler.existing()` — **idempotency**: if the target state is already true, emit `step_verified`
     (skipped) and move on. This is also how a resumed run skips finished steps.
  3. `handler.snapshot()` → `step_started` (with pre-state) → `handler.act()` (the Graph call).
  4. `_verify()` — `handler.verify()` re-reads, polling with back-off (`VERIFY_DELAYS`) while the directory
     catches up; `compare()` checks the plan's `expected` against what was observed. **A step is not done
     until the independent read-back confirms it.** A mismatch is a `step_failed`.
- **Every event payload passes `Secrets.guard`** before it's written — a TAP or password about to hit the log
  raises `SecretLeak`, and the runner escalates (kind `secret_guard`).

On failure the runner escalates with a `rollback` list built from the catalogue's `compensate` strings —
offered to a technician as one button, never run automatically.

### 5.6 Close-out — `closeout.py` + `agents/closeout.py` (Sonnet)
Once every step verifies, `close_out()`:
1. Runs the playbook's **DoD probes** against *live* state (`execution/dod.py`); each is a `done_check` event.
2. Sonnet drafts the requester reply + PSA note (`agents/closeout.py`) from a summary built out of events —
   it **never** sees the ticket text, a TAP, or a link URL. Where the link goes it writes `{{link}}`.
3. The **secret scanner** checks both drafts against patterns *and* every secret the run holds. A hit blocks
   that text (`message_blocked`, with hit kinds + offsets, never the text). The reply also waits on the DoD
   (not sent if a probe failed); the PSA note always goes (it's the hand-off).
4. A `time_entry` with work type "AI Automation" and the run's *working* minutes (approver-wait excluded).

Returns failed probes; the runner moves to `DONE`, or `ESCALATED` (kind `dod`) if any failed.

### 5.7 The multi-agent design — where this sits, and why

[PydanticAI's multi-agent guide](https://pydantic.dev/docs/ai/guides/multi-agent-applications/) defines five
levels of increasing complexity: (1) single agent, (2) **agent delegation** (an agent calls another *from
inside a tool*, sharing `ctx.usage`), (3) **programmatic agent hand-off** (several agents run in succession
and *application code* decides who runs next, each with its own dependencies), (4) **graph-based control
flow** (a state machine drives multiple agents — "the most complex cases"), and (5) deep agents.

This system is a textbook example of **level 3, programmatic agent hand-off**, reaching into level 4:

- Three agents run in succession — intake (Haiku) → investigator (Opus) → close-out (Sonnet) — each a
  separate PydanticAI `Agent` with its **own model and its own dependencies** (`InvestigateDeps` for the
  investigator, `None` for the other two). That is exactly the hand-off pattern's shape.
- **Code, not a model, decides the next step.** `runner.py` sequences the agents; the state machine
  (`RunState` + the `TRANSITIONS` table) governs what may follow what. PydanticAI describes level 4 as "a
  graph-based state machine controls the execution of multiple agents" — that is precisely the control layer
  here, implemented with our own persisted, resumable state machine rather than `pydantic-graph`.

**Why hand-off and not delegation (level 2) — this is the important part.** The agents deliberately do *not*
call each other. The investigator cannot invoke the executor; it can only *return a plan*. If an agent could
delegate to the write path as a tool, the model would — by definition — be able to trigger a write, which is
the one thing the whole architecture exists to prevent (§6). So the choice of hand-off over delegation is not
an accident of convenience; it *is* the safety thesis expressed in the control flow. The deterministic gate
sits in the gap between "plan produced" and "plan executed" precisely because that gap is code, not a model.

> One idiomatic note: the hand-off pattern canonically threads a single `RunUsage` object across runs to total
> tokens. Here each agent's usage is recorded per-run in its `plan`/event payload instead — equivalent for
> accounting, slightly less idiomatic. See §"Should this move to pydantic-graph?" for when the level-4
> framework would start to earn its place.

---

## 6. The safety model as layers (why there are five)

Each layer is independent and testable in isolation. An attacker (or a confused model) has to beat all of
them:

| Layer | File | Stops |
| --- | --- | --- |
| **Typed union + patterns** | `core/models/` | Wrong params, invented-looking IDs, a password field, a raw TAP |
| **Linter (grounding)** | `safety/lint.py` | IDs never looked up, out-of-scope actions, uncovered DoD, bad ordering |
| **Policy gate (risk)** | `safety/gate.py` | Any risky write running without a human; blast-radius abuse |
| **Secret guard** | `graph/writes.py` + `safety/scanner.py` | A credential reaching the event log or an outbound message |
| **Verify + DoD** | `executor.py` + `execution/dod.py` | Claiming success when the live state disagrees |

Plus the structural fact underneath all of them: **the investigator agent has no write tool**. The model
proposes; code disposes.

Prompt-injection defense runs through this: ticket text is wrapped in `<ticket>` tags and labelled untrusted
in both the intake and investigator instructions, the requester identity comes from the envelope not the
body, and anything the ticket asks for beyond its intent lands in `out_of_scope_requests` — which the gate
and close-out treat as "not done."

---

## 7. The Graph layer (`graph/`)

- **`client.py`** — one async `GraphClient`. `request()` owns the retry policy *in code, never the model's*:
  429 honours `Retry-After`; 5xx retried 3×; **replication lag** (404, or 400 "does not exist" on an object
  we just created) retried over ~60s but only when the caller opts in with `retry_replication=True`.
  `list()` auto-pages `@odata.nextLink`. Tokens are app-only, from a certificate, fetched ~hourly in a thread.
- **`reads.py`** — the 13 read tools, each returning *trimmed* dicts (only what the model needs, so prompts
  stay small). The `Transcript` records every call and every observed GUID, and collects per-object facts
  (`displayName`, group `kind`, `roleAssignable`…) so the linter and gate can judge a step's *target*, not
  just its shape. `classify()` turns Graph's flags into `m365 | security | mail_security | distribution_list`.
- **`writes.py`** — a `Handler` per action with the four-method contract (`existing/snapshot/act/verify`),
  handling real-world quirks (a new user 404ing on group-add for a minute, a just-freed license seat,
  "already exists" 400s). Internal-only mail forwarding is enforced **here, in code**, against the live tenant
  domain. `Secrets` holds TAPs in memory only and `guard()`s every payload.

---

## 8. Playbooks (`playbooks/`) — runbooks as data

A playbook is YAML, used two ways: parsed into the `Playbook` Pydantic model for code (lint/gate/DoD), and
passed **verbatim with comments** to the investigator. It declares `must_investigate`, `questions_to_resolve`,
`definition_of_done`, `allowed_actions`, `gotchas`, and `never`. `client_facts.yaml` holds the per-client
truth: `approvers` (per intent), `group_handling` (which of a peer's groups to mirror vs. hand to a
technician), `non_graph_access`, `known_service_accounts`, and **`autonomy`** (A1/A2/A3 per intent — the dial
the gate reads).

This is the growth mechanism: the plan is for ~30 playbooks, and (Week 4) a successful no-playbook run becomes
a drafted playbook after human review. `allowed_actions` + `autonomy` are exactly the levers an MSP needs to
"start in shadow mode, earn autonomy per intent."

---

## 9. Persistence & transport (`core/store.py`, `api.py`, `cli.py`)

- **`store.py`** — SQLModel tables: `Ticket`, `Run`, `Event` (unique `(run_id, seq)`), `Link`, `Followup`.
  `append_event()` validates the type against a frozenset and JSON-roundtrips the payload *before* committing
  (fails early, not in the UI). `EventBus` is in-process pub/sub; `stream()` subscribes **before** replaying
  history so nothing is lost in the gap.
- **`api.py`** — FastAPI. Work that can fail (wrong state, not an approver, bad answers) runs *before* the
  202, so the caller gets a 409, not a dead event stream. Long work (agents, execution) runs under
  `TaskRunner`, which **escalates a run whose task crashes** rather than leaving it stuck. `/runs/{id}/events`
  is SSE: history after `Last-Event-ID`, then live, then an `end` event. `snapshot()` rebuilds the whole UI
  view from events. `/l/{token}` + `/l/{token}/reveal` serve the one-time credential link.
- **`cli.py`** — the same phases, headless, printing the same event stream. Exit codes encode the outcome
  (0 done/awaiting, 1 refused, 2 escalated, 3 held).

### One-time credential links (`execution/links.py`)
The only way a TAP leaves the executor. Fernet-encrypted at rest; a **two-step** view (so a Safe-Links or
chat unfurl can't burn it); `reveal()` wipes the ciphertext in a **single conditional UPDATE** checking
`rowcount == 1`, so two concurrent reveals can't both win. The link token is only ever in the served snapshot,
never in an event.

---

## 10. The frontend (`web/`)

A Next.js console built on three ideas:
- **The server snapshot is the source of truth** for anything derived (effective params, gate decisions,
  follow-ups, links, close-out).
- **`lib/run.ts` is a pure reducer** over the SSE stream — unit-tested against a captured `.sse` fixture —
  keeping only what the snapshot lacks: the investigation log with timings, lint retries, per-step attempts.
- **`lib/useRun.ts`** wires them together: fetch the snapshot, replay+subscribe the stream, and re-fetch the
  snapshot (debounced) whenever a `REFRESH_ON` event makes it stale. Components (`Inbox`, `Workspace`,
  `InvestigationLog`, `PlanSteps`, `ApprovalCard`, `Execution`, `Closeout`, `LinkPage`) just render that.

---

## 11. How a real onboarding flows (end to end)

1. `POST /tickets` → `create_ticket` + `create_run` → `tasks.spawn(runner.start)`; returns 202 with `run_id`.
2. `runner.start`: `run_intake` (Haiku → `Triage`, requester checked) → `investigate` (Opus, read-only tools,
   linter in the loop → `Plan`) → `gate.check`.
3. The onboarding plan touches a `security.credential` step (the TAP), so the gate routes to
   **AWAITING_APPROVAL**. The UI shows the plan, the gate decisions, and the confirm card (UPN format, start
   date).
4. `POST /runs/{id}/approval` with the approver and any answers → `runner.accept` (sync checks) →
   `tasks.spawn(runner.execute)`.
5. `Execution.run`: create user → set usageLocation → (mock) purchase a seat → assign license → add to the
   mirrored groups → create a TAP → send the one-time link → schedule the start-date follow-up. Each step is
   snapshot → act → verify-by-read-back, idempotent, secret-guarded.
6. `close_out`: DoD probes against live state → Sonnet drafts reply + PSA note → scanner clears them →
   `message_sent` + `time_entry` → **DONE**.
7. Throughout, every event streams to the console; the one-time link is revealed once, from the UI.

---

## 12. Testing & tooling

- **256 tests** (`backend/tests/`), `ruff` clean. Pure functions everywhere make the guardrails
  independently testable: `gate.evaluate`, `lint`, `executor.order`/`compare`, `scanner.scan`, and the
  frontend `reduce`.
- A **`FakeTenant`** and `respx` stand in for Graph offline; a `live` pytest marker runs the real scenarios
  against the demo tenant (after `tenant/reset.py`).
- Logfire tracing per run, with extra scrubbing so a token or TAP never reaches a trace
  ([core/observability.py](backend/tech/core/observability.py)).

---

## 13. Deliberate current limits (all in the plan, none accidental)

- **Single tenant.** The investigator prompt names one client; facts/playbooks load one domain; one Graph app.
  This is the central thing that changes to serve MSPs — see
  [docs/overall/expansion-to-multi-customers.md](docs/overall/expansion-to-multi-customers.md).
- **SQLite + in-process event bus.** Correct for the demo; Postgres + a durable job engine (Temporal) are the
  productization path. The `resume` path exists precisely because the durable engine isn't here yet.
- **No API auth.** Expected for a localhost demo.
- **Mocked where honest.** `license.purchase` (Pax8) and the out-of-band confirmation are mocks; the one-time
  link is served by us. Everything against Graph is real.
- **In flight (Week 3) / deferred (Week 4):** MFA reset, the replanner, the eval harness; then the no-playbook
  path and "save as playbook" learning. The `REPLANNING` state and `replan` event are wired but not yet
  driven.

---

## 14. Should this move to `pydantic-graph`?

`pydantic-graph` is PydanticAI's level-4 framework: you model control flow as typed `BaseNode`s whose `run()`
returns the next node, and it provides a `StatePersistence` interface so a graph can be interrupted and
resumed. The shape of this system — a persisted, resumable state machine driving multiple agents — is exactly
what it is built for. So the honest answer is: **not now, and maybe never for the core, but it's the natural
home for one specific piece.**

**Why not now.** The control layer we already have (`RunState` + `TRANSITIONS` + the event log) is small,
fully understood, and does things `pydantic-graph` would not give us for free:

- The **event log is already our state persistence** *and* our audit trail *and* the UI feed *and* the replay
  log — one mechanism, four jobs. `pydantic-graph`'s `StatePersistence` would persist graph state, but we'd
  still keep the event log for audit and streaming, so we'd own *two* persistence layers, not fewer.
- Our transitions encode **domain rules a generic graph doesn't** — "any live state may escalate or be held,"
  the HELD-release path, the synchronous approval that can't race. These are a dozen lines of a dict today.
- A rewrite is pure risk against the safety-critical path for no user-visible gain, and it fights the thing
  that makes the system legible: *code, not a framework, decides what runs next.*

**Where it would genuinely earn its place.** Two futures change the calculus:

1. **The no-playbook diagnose loop (demo v2, Tech.MD §4).** "diagnose → hypothesise → one small fix through
   the gate → observe → repeat, bounded" is a *cyclic, branching* flow where each node's output picks the next
   node. That is what graphs are good at and what a flat transition table is bad at. If and when that loop
   lands, modelling *it* (not the whole run) as a `pydantic-graph` is reasonable.
2. **The durable job engine.** Multi-day work with real wait states (shipment arrives, 30-day timer,
   user action) needs durable execution. The tech-plan already names **Temporal** for this, which subsumes
   what `pydantic-graph`'s persistence offers and adds timers, signals and retries across process restarts.
   For long-running orchestration, Temporal is the more likely target than `pydantic-graph`.

**Net:** keep the hand-off + state-machine core as is; reach for `pydantic-graph` only if the branching
diagnose loop is built, and reach for Temporal (not `pydantic-graph`) for durable multi-day jobs. Adopting the
framework now would be résumé-driven architecture — it would make the "level 4" label literally true at the
cost of a worse system.
