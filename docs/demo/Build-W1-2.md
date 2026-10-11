# Weeks 1-2 Implementation Plan: Onboarding End to End (Thin)

## Context

[Tech.MD section 13](Tech.MD) sets the exit for Weeks 1-2: **the Priya onboarding runs live, end to end, in under 3 minutes**, so that demo meetings can start. "Before day 1" and the Days 1-2 risk tests are done:

- The tenant is seeded, and reset works: `tenant/seed.py` and `tenant/reset.py`.
- The app has certificate auth and all its permissions.
- App-only writes work.
- Opus 5.5 returns a typed `Plan` through PydanticAI 2.55's default tool output. There were no refusals; runs took 36-69 s and cost 0.17-0.38 USD each. See [spike/results.md](../../spike/results.md).

The repo has no app code yet. It has docs, `tenant/` (a sync httpx Graph client plus the seed and reset scripts) and `spike/model_risk.py`, a working investigator prototype with 12 read tools and a `Plan` model.

**Scope:** only the onboarding scenario (Product.MD section 3.1), with the spend-approval beat, the email-format confirmation and the technician hand-offs. Not in scope until Week 3: offboarding, MFA, the replanner, linter rules 4-7, autonomy levels, safe mode and the no-playbook path.

## Decisions carried in from the spike

- **Graph client.** Port `tenant/graph.py` to **async httpx** and drop `msgraph-sdk`. The httpx client is proven against this tenant: retries, paging and replication-lag handling all work. Graph quirks to code in:
  - Read `signInActivity` only through a `/users` query filtered on `id`.
  - Group mail and security flags decide the group kind (the `classify()` logic in the spike).
- **Output mode.** Use the default `ToolOutput(Plan)`. Turn on prompt caching (`anthropic_cache_instructions`, `anthropic_cache_tool_definitions`) and set `anthropic_effort="high"`.
- **Client facts.** Align them with the seeded tenant:
  - Make `tgarcia@` the onboard and spend approver, and Kevin the VPN approver. This replaces Lauren and Marlene, who don't exist.
  - Mark the `Northwind Parking` org-wide group as "automatic membership, never mirror."
  - Add `RDS-Users` and `VPN-Users` as "flag for technician, do not mirror." This fixes the inconsistency seen in the spike.
  - Add `seat-holder*` as "intentional license reserve, don't flag."
- **License on a future start date.** The playbook stays as written: assign the license on the start date via `followup.schedule`. For the demo, the scheduler has a "run now" button, so the license assignment is visible live.

## Repo layout (new)

```text
it_tickets/
  pyproject.toml            uv project: fastapi, uvicorn, pydantic-ai-slim[anthropic], logfire[fastapi,httpx],
                            httpx, azure-identity, sqlmodel, pyyaml, cryptography
                            dev: pytest, pytest-asyncio, respx, ruff
  backend/tech/
    core/config.py          settings from .env and tenant/.env (API key, Graph cert, Fernet key, domain)
    core/observability.py   Logfire setup and scrubbing
    core/models.py          Triage, Plan, Step (params as a discriminated union per action), GateDecision, events
    core/store.py           SQLModel tables: ticket, run, event, approval, link, followup
    graph/client.py         async Graph client (port of tenant/graph.py)
    graph/reads.py          read tools: functions taking (graph, args), returning trimmed dicts
    graph/writes.py         write actions plus their verify reads
    playbooks/              client_facts.yaml, user.onboard.yaml (from Tech.MD section 6, aligned) + loader.py
    agents/intake.py        Haiku -> Triage
    agents/investigator.py  Opus -> Plan; read tools + record_finding; output_validator = linter
    agents/closeout.py      Sonnet drafts (reply, PSA note), time entry, needs-technician list, DoD probes
    safety/lint.py          rules 1-3 (schema, grounded, in scope)
    safety/gate.py          requester hold, never/catalogue blocks, risk-class rules, blast radius
    safety/scanner.py       secret scanner for outbound text
    execution/catalogue.py  ACTIONS: action -> params model, risk_class, act(), verify(), compensate
    execution/executor.py   idempotency -> snapshot -> act -> verify, retry policy, step-ref resolution
    execution/mocks.py      license.purchase (frees the seat-holder's seat), requester.confirmOutOfBand
    execution/links.py      one-time link service (Fernet-encrypted, view once, expires)
    runner.py               run state machine (RECEIVED -> ... -> DONE / ESCALATED / HELD)
    api.py                  FastAPI: REST + SSE
    cli.py                  headless run / approve, for Week 1 and tests

  safety/ is the "model proposes, code decides" boundary: everything between a Plan and a Graph write.
  Split core/models.py into a models/ package (triage, params, plan, gate) when Week 3 adds the
  offboarding and MFA actions.
  backend/tests/            unit (pure), agent (TestModel/FunctionModel), executor (respx), live (marker)
  web/                      Next.js app: inbox, workspace, approval card, link page
  tenant/, spike/           existing; reuse, don't modify except for client-fact alignment
```

## Week 1: backend runs headless, end to end

### Day 1. Scaffolding, Graph client and store

- `git init` and the uv project, with ruff, pytest and Logfire (`service_name="ai-technician"`).
- Configure Logfire scrubbing for `temporaryAccessPass`, `password` and `token`. Leave httpx body capture off.
- `graph/client.py`: an async port of `tenant/graph.py`. Keep `Graph.request` with its 429/5xx/`retry_404` handling, plus `list()`, `first()` and `q()`.
- `store.py`: SQLModel tables, `append_event(run_id, type, payload)`, and an in-process pub/sub for SSE.
- Tests: retry and backoff behavior with respx, and event append and order.

### Day 2. Schema, catalogue, playbooks and read tools

- `models.py`:
  - `Triage` (Tech.MD section 4.0).
  - `Plan` and `Step`, using the spike's model plus typed params per action: `UserCreateParams`, `LicenseAssignParams`, `GroupAddMemberParams`, `TapCreateParams`, `LicensePurchaseParams`, `FollowupParams`, `LinkSendParams`.
  - ID fields also accept a reference to an earlier step's output, written `ref:<step>.output.id`.
- `catalogue.py`: the onboarding actions from Tech.MD section 7, each with its `risk_class`, verify read and compensation.
- `playbooks/`: YAML extracted from Tech.MD section 6.1 and the shared facts, aligned as described above, with a loader.
- `graph/reads.py`: port the spike's read tools. Each call returns its result and records the **observed IDs** (every GUID in the result) to the run transcript, which feeds the "grounded" lint rule.
- Tests: schema round-trip on the spike's saved plans (`spike/results-*.json`) as fixtures, and the catalogue covering every onboarding action.

### Day 3. Agents

- `agents/intake.py`: Haiku with `output_type=Triage`. Requester authority is checked by code against the client facts.
- `agents/investigator.py`: from the spike, plus:
  - deps carrying the transcript and the event recorder;
  - `record_finding` and every tool call emit events;
  - prompt caching on;
  - `UsageLimits(tool_calls_limit=25)` and a 90 s timeout. On a limit, the run goes to ESCALATED with the findings so far.
- `cli.py run "<ticket>" --from tgarcia@...` prints the streaming findings and the plan.
- Tests: `TestModel`/`FunctionModel` checks that no write tool is registered, that events are recorded, and that a refusal (`finish_reason == "content_filter"`) leads to ESCALATED.

### Day 4. Linter and gate

- `lint.py` rules 1-3, wired in as `@investigator.output_validator`. A failure raises `ModelRetry(reasons)`, with `retries=2`:
  - **Schema:** the action is in the catalogue and its params validate.
  - **Grounded:** every ID-shaped param is in the transcript's observed IDs, or is a reference to an earlier step.
  - **In scope:** the action is in the playbook's `allowed_actions` for a triaged intent.
- `gate.py`: the Tech.MD section 8 rule order, minus autonomy and no-playbook:
  - an unapproved requester leads to HELD;
  - a `never` rule or catalogue block leads to block;
  - spend, data.access and security.credential lead to approve;
  - write.reversible leads to auto;
  - more than 20 writes of one class lead to approve.
- The gate returns `GateDecision(step_id, decision, rule_id, reason)`.
- Tests are pure and table-driven:
  - a role-assignment step is blocked;
  - an invented group ID fails the grounded rule;
  - a purchase step needs approval;
  - a TAP step needs approval;
  - a create step is auto;
  - a non-approver requester gets HELD.

### Day 5. Executor, mocks and runner, then the Week 1 exit

- `graph/writes.py` and `executor.py`. Steps run in dependency order. For each step:
  1. idempotency check (step already applied, or target state already true);
  2. snapshot the pre-state;
  3. act;
  4. verify by reading back against `expected`.
- Retries follow Tech.MD section 4.6. A verify mismatch or a non-retryable error leads to ESCALATED (no replanner until Week 3). A step's output is stored so later steps can reference it.
- `mocks.py`: `license.purchase` removes the `seat-holder` user's license, then returns success. This matches the reset's assumption.
- Approval answers override params. For example, if the approver edits the confirmed email format, the `user.create` UPN changes to match.
- The TAP value goes straight from the executor to `links.create()` and is never put in events or logs.
- `runner.py`: the state machine, persisting every transition as an event. `cli.py approve <run_id> [--confirm upn_format=...]`.
- **Week 1 exit:**
  1. `uv run tenant/reset.py`
  2. `cli run` with Priya's email
  3. `cli approve`
  4. Priya exists in Graph with the right UPN, is in the mirrored groups, has an active TAP and a scheduled license follow-up, the seat-holder's seat was freed, and every step is `step_verified`.

## Week 2: API, UI, close-out and the under-3-minute bar

### Day 6. API

- `api.py`:
  - `POST /tickets` (text, from) starts the run as a background task;
  - `GET /runs/{id}` returns the snapshot;
  - `GET /runs/{id}/events` is the SSE stream (replays history, then goes live);
  - `POST /runs/{id}/approval` takes approve all or reject, plus the confirm answers;
  - `POST /followups/{id}/run-now`;
  - `GET /l/{token}` shows the one-time link's metadata, and `POST /l/{token}/reveal` returns the TAP once. These are two calls so that link previewers can't burn the link (see [Build-Day6.md](Build-Day6.md)).
- Add `logfire.instrument_fastapi`.
- Tests: an SSE client receives events in order, and a reconnect replays history.

### Day 7. Workspace UI (Next.js)

- Inbox: paste a ticket and pick the sender (the seeded contacts).
- The workspace renders the event stream:
  - **Understood**, from Triage;
  - **Investigation log**, from findings and tool calls;
  - **Plan**, with an auto / approve / block label and the rule for each step;
  - **Approval card**, with the risky steps highlighted and editable confirm fields (UPN format, purchase);
  - **Live execution**, showing verified or retried for each step;
  - **Needs a technician**.
- Senders and the sample tickets are hardcoded (no `/contacts`). The one-time link page moved here from Day 8, because the snapshot's link URL already points at the web app (see [Build-Day7.md](Build-Day7.md)).

### Day 8. Close-out

- `links.py`: the TAP is Fernet-encrypted and shown once, then the record is destroyed. (Done: Day 6 wipes it on reveal, and Day 7 built the page with the TAP and the sign-in steps.)
- (Done, see [Build-Day8.md](Build-Day8.md).) `scanner.py`: regexes for TAP-like strings, passwords and tokens, plus the actual TAP values held for the run. Every outbound text goes through it; a hit blocks the send and emits an event.
- (Done.) The close-out is split into `execution/dod.py` (the probes) and `tech/closeout.py` (orchestration); a failed DoD escalates the run (`kind="dod"`):
  - the DoD probes from `user.onboard.yaml` (`user_exists`, usageLocation, `user_in_groups`, `tap_active`, license or follow-up, `needs_technician_listed`);
  - Sonnet (`claude-sonnet-5-5`) drafts the reply to Taylor (link, Outlook mobile tip) and the PSA note;
  - a time entry ("AI Automation", actual duration).
- Each close-out item is shown in the workspace. (Done. The manual UI walkthrough is still pending.)
- Tests (done): the scanner blocks a draft containing the TAP, and a DoD probe fails on a missing group.

### Day 9. Speed and robustness

- Measure in Logfire against the under-3-minute bar. Tune:
  - parallel reads (the prompt already asks for them);
  - caching hit rate;
  - trimming tool output;
  - effort (`high` for the investigator, `low` for intake).
- Bounds produce ESCALATED with a hand-off package (findings, done steps, the failing step).
- `scripts/preflight.py` checks the seed state, the Graph token and permissions, the model API, and that Priya is absent.
- Run 5 phrasing variations of the onboarding ticket, including the injection line and a peer missing from the request. Save them as `backend/tests/evals/onboard_*.yaml` for Week 3's eval harness.

### Day 10. Rehearse and document

- Reset, then run the full demo in the UI 5 times. Record wall-clock time and cost for each run, and fix the flakiest step.
- Update Product.MD section 4 (real vs. mocked) and Tech.MD:
  - the Graph client choice;
  - the output mode;
  - the client-fact alignment;
  - the open decision on MFA mocking from `spike/results.md`, finding 1, to make before Week 3.

## Verification

- `uv run pytest`: the unit, agent (TestModel) and executor (respx) suites pass offline.
- `uv run pytest -m live`: after `tenant/reset.py`, runs the CLI path against the tenant and checks the DoD through Graph.
- Manual: `uvicorn tech.api:create_app --factory` and `npm run dev` in `web/`. Paste Priya's email as Taylor and approve, confirming `priya.nair@`. Then:
  - every step shows verified;
  - the link page shows the TAP exactly once;
  - Priya shows up in the Entra admin center;
  - the full run takes **under 3 minutes**, read from the Logfire trace.
- `uv run tenant/reset.py --dry-run` afterwards shows Priya and her changes as the only things to undo.

## Risks

- **Investigator variance** (for example, mirroring RDS-Users in some runs and not others). The Day 2 client-fact alignment and the Day 9 variation set address it. The linter's in-scope rule catches the worst cases.
- **Next.js takes longer than expected.** Fall back to a server-rendered page for the workspace. The API is the same.
- **Exchange mailbox provisioning lag** doesn't affect onboarding, but it will in Week 3 (auto-reply). Note it now.
