# Week 4 Day 16: the eval harness

## Context

[Build-W4.md](../Weekly/Build-W4.md) Day 16: `cases.yaml` holds Tech.MD §11's 28 cases as data, and
`eval_harness.py` runs them in two tiers. The offline tier runs in `uv run pytest`; the live tier runs with
`-m live` against the tenant, with a reset between cases. Cases 1-22 must pass; 23-28 are `xfail` until
their Week 4 day.

The day has four parts. Each part ends with green tests and a commit, then a stop for review.

| Part | What | Touches product code? | Status |
| ---- | ---- | --------------------- | ------ |
| 1 | Offline tier: FakeTenant reads, harness, all 28 cases | No (tests only) | **Done** |
| 2 | Fix the three gaps the cases found (D1-D3) | **Yes**: lint, runner, close-out | **Done** (D1-D3 as recommended) |
| 3 | Seed gap: a disabled former employee for #3 | Seed script only | **Done**, seeded on the tenant |
| 4 | Live tier, run once, results recorded here | No, but **costs money and writes to the tenant** | **Built**; the run needs approval |

---

## Part 1 (done): the offline tier

**Files** (tests only, plus one line of config):

- `backend/tests/fake_tenant.py`: the investigator's reads on top of the existing writes:
  - `GET /users` (search by name, list all, the `id eq` signInActivity read)
  - `/users/{id}/manager`
  - `ownedObjects`
  - `memberOf` returning full group objects, so groups classify
  - `subscribedSkus` with a seat pool
  - `signIns` filtered by user
  - `add_group` / `add_sku` helpers
- `backend/tests/eval_harness.py`:
  - a miniature of `tenant/seed.py`: 10 users, 10 groups, 4 of 4 seats used, Dennis's and Luis's phones;
  - recorded model output: a `TestModel` for intake, and a `FunctionModel` investigator that plays the
    case's reads, then its plan;
  - the case runner: `runner.start`, then the case's `then` actions (`confirm_oob`, `approve`, reject);
  - the checks.
- `backend/tests/evals/cases.yaml`: cases 1-28, plus 5b (the admin step forced in: the linter rejects it,
  and the gate blocks it when evaluated directly). The file's header documents the format.
- `pyproject.toml`: `python_files` includes `eval_harness.py`.

**Every case checks** the expectations it lists:
- intent, lint, gate decision per step, a direct gate evaluation, state after start, final state;
- escalation kind and reason, the DoD probes that ran and passed, the number of Graph writes, approvals;
- reads that succeeded, read results, follow-ups, and what the plan says.

It also checks two invariants on every case:
- **zero gate bypasses:** every `step_started` comes after a non-`block` gate decision for that step, and an
  `approve` step only starts after an approval;
- **no TAP in any event.**

**Result:**
- 19 of the 22 in-scope cases pass, as does 5b.
- 3 are `xfail` on the gaps below.
- 23-28 are `xfail` with their Week 4 day; each fails for its stated reason (checked with `--runxfail`).
- `uv run pytest`: 305 passed, 9 xfailed. `ruff` is clean.

**What offline does and doesn't prove.** Intake and the investigator are recorded, so the intent and the
plan's content are inputs, not results. Offline proves that, given that plan, the linter, gate, executor
and DoD do the right thing. The model's judgement is the live tier's job.

---

## Findings from reading the code

1. **Three cases hit real gaps** in the product. They are decisions D1-D3 below.
2. **The Day 9 `onboard_*.yaml` variations were never committed.** There is no `backend/tests/evals/`
   in git history. Nothing was folded in. Recreating them means 5 live intake runs (cheap, Haiku);
   that is optional and not part of this plan.
3. **Seed gaps** (the W3 open item):
   - **#3** needs a disabled user whose name matches the new hire. The live seed has none (part 3).
   - **#9** (never signed in): no gap, because seeded users never sign in interactively.
   - **#14** and **#16**: no gap; neither needs extra tenant state.
   - **#12**: the live tenant can't be seeded with foreign sign-ins, so #12 is offline only. This is
     already allowed by Tech.MD §10.
4. **Case 8 corrected my expectation, not the code.** A non-approver's offboard makes `disable`/`revoke`
   `approve`, not `auto`. That matches Tech.MD §8 ("on an offboard with an approved requester").

---

## Decisions (part 2)

### D1. A plan that only asks the requester (#3; #24 later)

- **Today:** Tech.MD §4.3 rule 6 allows "a `questions_for_requester` pause instead" of a plan. But rule 4
  rejects any plan whose steps don't cover the definition of done, so a no-step plan with a question fails
  lint. After retries it escalates as `lint`. If it got past lint, close-out would run the DoD on nothing
  and escalate as `dod`.
- **Recommendation:**
  - `lint.py`: skip rule 4 when `questions_for_requester` is non-empty, exactly as rule 6 already does.
  - `runner.start`: a gated plan with **no steps** and questions goes `PLAN_CHECKED → ESCALATED`,
    `kind="questions"`, with the questions in the payload. Nothing is written, and the technician sends
    the questions.
  - The workspace already renders any escalation kind and its fields. There is no web change.
- **Alternative:** a new `AWAITING_REQUESTER` state. It is more accurate, but it touches the state machine,
  the web types and the UI. Defer it to Week 5 with the PSA integration, which owns "waiting on customer".

### D2. A plan that does the safe part now and asks about the rest (#18)

- **Today:** "turn him off" → disable + revoke + questions. Rule 4 rejects it, because there is no
  `group.removeMember`/`group.addOwner` step.
- **Recommendation:** the same rule 4 change as D1. With steps, the run then proceeds normally: all-auto, so
  it executes straight away, and the DoD probes pass trivially for things not planned. It ends `DONE`, and
  the reply carries the questions.
- **Caveat:** `DONE` while questions are open is slightly generous. It's the same "waiting on customer"
  problem as D1, and gets the same Week 5 answer.

### D3. Two intents in one ticket (#14)

- **Today:** one plan covers both intents (`Plan.intent` holds one), and lint rules 4-6 already check both
  playbooks. Close-out, though, runs the DoD only for `plan.intent`'s playbook, so the onboarding half of
  "offboard Kevin + onboard his replacement" is never probed.
- **Recommendation:** `closeout.py` runs the DoD items of **every triaged intent's playbook**, de-duplicated
  by probe name. A single-intent run is unchanged.
- **Alternative:** split a two-intent ticket into two runs. That matches Tech.MD's "two plans/jobs"
  literally, but it's a runner and API change plus a UI change. Note the reinterpretation in Tech.MD
  instead: one run, one plan, both playbooks' lint and DoD.

### Part 2 work, if D1-D3 are accepted as recommended

| File | Change |
| ---- | ------ |
| `safety/lint.py` | Rule 4 is skipped when the plan pauses for questions; docstring updated |
| `runner.py` | The no-step question pause → `ESCALATED`, `kind="questions"` |
| `closeout.py` | DoD for every triaged intent's playbook, de-duplicated |
| `tests/test_lint.py`, `test_runner.py` | Unit tests for each |
| `evals/cases.yaml` | Remove the `xfail` on #3, #14, #18 |

**Gate:** 22/22 in-scope offline, plus 5b. The existing onboarding/offboarding/MFA tests are unchanged.

**Result (part 2):**
- 22/22 in-scope cases pass, plus 5b and a new 3b: the rehire question from a non-approver escalates with
  the questions instead of sitting in HELD, because releasing that hold would only run an empty plan.
- `uv run pytest`: 311 passed, 6 xfailed (23-28). `ruff` is clean.
- No existing test changed.

---

## Part 3: the seed gap for #3

- `tenant/seed.py`: add **Nina Alvarez**, `nina.alvarez@`, Accounting, **disabled**, unlicensed.
  - Use the first.last format on purpose. As the newest account she sorts first in `users.list`, and a
    legacy-format address there could sway the format the investigator proposes for Priya in the live demo.
  - She is in `PEOPLE`, so `reset.py` keeps her.
- Run `uv run tenant/seed.py` once. It is idempotent, but it **writes to the tenant**, so I'll ask first.

**Result (part 3, 2026-10-13):**
- `Person` has `enabled` (default True). `seed.run` passes it to `ensure_user`, so seed and reset both keep
  Nina disabled. A disabled person gets no trial seat.
- Seeded: `nina.alvarez@` was created, disabled, unlicensed, with Marcus as manager (checked with Graph).
- `reset.py --dry-run` leaves her alone: she is on the roster, so she isn't deleted, and the re-seed
  keeps her disabled.

---

## Part 4: the live tier

- **What runs:** cases marked `live: true` in `cases.yaml`, i.e. the demo scenarios plus one refusal.
  - #1 onboarding, #7 offboarding, #11 MFA reset, #20 `reports@` refusal.
  - #23 file restore is added on Day 18.
- **How:**
  - Before each case, `uv run tenant/reset.py`.
  - Then the real intake and investigator models, through `runner.start`.
  - Then the case's `then` actions, against the real tenant.
- **What it asserts:** only the deterministic parts, never plan text:
  - the intent;
  - the gate decision for each **action**, taken from the case's offline expectations (for example
    `tap.create` → approve);
  - DONE with every DoD probe passing, or the expected escalation kind;
  - zero bypasses, and no TAP in events.
- **Cost:** about 0.2-0.4 USD per run (the Day 5 measurement), so about 1-2 USD for the four. It takes a
  few minutes, mostly resets and replication waits.
- **Running it writes to the demo tenant.** I'll build it, then ask before the one recorded run. The results
  go into a "Live results" section here.

**Built (part 4):**
- `eval_harness.test_live`, marked `live`, runs the cases with `live: true` (1, 7, 11, 20). Run it with
  `uv run pytest -m live -k test_live -s`.
- `live_expect(case)` derives the live bar from the offline `expect`:
  - intent, lint, state after start, final state, the escalation kind, and the DoD;
  - the gate per action, using what the offline plan's step with that action got (for example
    `license.purchase` → `approve/risk.spend`);
  - not step ids, plan text, read results or write counts, because those vary live.
- Live runs use an in-memory store, not `data/tech.db`.
- A refused approval (say, the live run escalated instead) is reported as a failure, not a crash.
- Each case prints its final state and the investigator's token usage.
- The usual invariants still apply: zero bypasses, and the run's real TAP (decrypted from its link) appears
  in no event.
- `test_live_cases_are_in_scope_and_judged_per_action` pins the live set and the derivation offline.

---

## Stop points

1. **Now:** review part 1 and decide D1-D3.
2. After part 2: review the diff to `lint.py`, `runner.py` and `closeout.py`.
3. Before part 3's seed run and part 4's live run: approve the tenant writes and the spend.

---

## Live results

### Run 1 (2026-10-13): 1 of 4 passed, 7 min 22 s

| Case | Result | Why |
| ---- | ------ | --- |
| 1 onboarding | **Pass**: DONE, every DoD probe passed | Investigator: 4 requests, 13 tool calls, 81k input tokens (46k cached), 4.8k output |
| 7 offboarding | Error | `httpx.ReadTimeout` on the investigator's `signIns.list` read (`GET /auditLogs/signIns`, 60 s client timeout) |
| 11 MFA reset | Error | `tenant/reset.py` timed out reading Temporary Access Passes before the case started |
| 20 `reports@` | Error | `httpx.ReadTimeout` on `signIns.list`, as in #7 |

**None of the failures is the agent judging wrongly; Graph was slow.** Timed right after the run:
- `GET /auditLogs/signIns?$top=15` took **33.6 s**;
- `GET /users` took 0.2 s.

With the per-user `$filter`, the sign-in read went past 60 s. Dennis's happy path doesn't touch this
endpoint (his sign-ins are the demo mock), but every other sign-in read does: the #12/#20 refusal beats,
and any offboarding where the model chooses to check sign-ins.

**Product gap the run exposed.** A transport error during investigation isn't handled.
- The investigator's `_read` catches `GraphError` only, so an `httpx.ReadTimeout` propagates out of
  `runner.start`.
- The run is left in `INVESTIGATING`. Through the API, the background task fails and the run hangs.
- A Graph error (4xx/5xx), by contrast, goes back to the model as a tool error, and the model carries on.

**Tenant state afterwards:** clean.
- #20's reset completed before its investigation timed out, and #20 writes nothing.
- #11 never ran, so Dennis still has Authenticator.

**Fix (after run 1):** in `graph/client.py`, no response from Graph is now handled like a Graph error.
- A GET that gets no response is tried once more, after 2 s.
- If it still gets none, or if a write gets none, `GraphTransportError` is raised. It is a `GraphError`
  subclass with status 0, so the existing handlers cover it:
  - the investigator tells the model the read failed, and the model carries on;
  - the executor fails the step, with the usual escalation and rollback offer;
  - a DoD probe fails.
- A write is never repeated, because it may already have been applied.
- `tenant/graph.py` (seed and reset) also retries a GET that gets no response, up to 3 tries.
- Tests: a read retried after a timeout, a persistent timeout surfacing as a `GraphError`, a write not
  repeated, and the investigator carrying on after a timed-out read.

**Not yet done:** a live re-run of #7, #11 and #20.

### Why the sign-in reads are slow, and what changed (2026-10-13)

`/auditLogs/signIns` searches Entra's sign-in log store; it isn't an indexed directory read. Timings on
the tenant:

| Query | Time | Rows |
| ----- | ---- | ---- |
| `userId eq` Kevin, top 15 (what the agent sends) | 39.1 s | 0 |
| The same plus `createdDateTime ge` 14 days | 57.2 s | 0 |
| No filter, top 15 | 28.4 s | 15 |
| `createdDateTime ge` 14 days only | 19.4 s | 15 |
| `GET /users` | 0.2 s | 5 |

Filters don't help, and a query with no matches is among the slowest. The investigator's budget is 90 s,
so one sign-in read can use most of it.

**Changes:**
1. **The model is steered away from the read.** The `sign_ins` tool description now says it takes
   20-60 s, is meant for MFA resets and suspected takeovers, and that `users_get`'s `signInActivity`
   answers "when did they last sign in" instantly. That covers the offboarding case, #7.
2. **The MFA subject's sign-ins are prefetched.**
   - On an `mfa.reset` run, investigation begins by resolving the subject: intake's `subject_user_hint`
     if it matches exactly one user, else the sender.
   - It then starts that user's `signIns.list` (top 15) in the background.
   - When the model asks for the same read, it awaits the prefetch. The `tool_call` event and the
     transcript are identical to a normal read, and the subject lookup is never in the transcript, so
     grounding still needs the model's own reads.
   - An unused prefetch is cancelled. The Dennis demo mock still applies, because it goes through the
     same `reads.fetch`.
   - **Limit:** the subject is only known after intake, so the prefetch hides the model's first turn
     (roughly 10-20 s), not the whole read. Not yet measured live.
   - Tests in `test_agents.py`:
     - one Graph query when the model asks, and the prefetch is under way before its first turn;
     - no trace when the model never asks;
     - no prefetch off an MFA reset, or when the hint matches more than one user;
     - the demo mock is honoured.
