# Day 5 plan: executor, mocks and runner, then the Week 1 exit

## Context

Day 4 is in place, though not committed: the linter (rules 1-3) runs inside the investigator, and the gate writes a `gate_decision` per step. A CLI run of Priya's ticket ends in AWAITING_APPROVAL. All 135 tests pass. Nothing writes to Graph yet, and nothing can approve a run.

Day 5 adds the write half, following [Build-W1-2.md](Build-W1-2.md) (Day 5) and Tech.MD §4.5, §4.6, §7 and §9:

- **Executor.** It runs the approved plan against Graph. For each step it runs an idempotency check, takes a snapshot, acts, then verifies. Verification is done by code, never by the model.
- **Mocks.** `license.purchase` frees the seat held by `seat-holder`.
- **Runner.** The state machine that `cli run` and the new `cli approve` both drive.

**Outcome (the Week 1 exit):**

1. `tenant/reset.py`
2. `tech run <Priya ticket> --from tgarcia`
3. `tech approve <run>`
4. Priya exists in Graph with the right UPN and is in the mirrored groups. She has an active TAP and a scheduled license follow-up, the seat-holder's seat is free, and every step is `step_verified`. The TAP value appears in no event and no log.

## Decisions

- **The event log is the source of truth across processes.** `tech approve` runs in a new process, so it rebuilds what it needs from the run's events: the plan from the last `plan` event, the intents from `triage`, the decisions from `gate_decision`, and the outputs and idempotency of finished steps from `step_verified`. There is no new step table.
- **Graph needs a password for a new user.** The executor generates a random one (`secrets.token_urlsafe(24)`, `forceChangePasswordNextSignIn`), sends it once and keeps it nowhere. The user signs in with the TAP. This fits the playbook rule "never a human-set password".
- **The TAP stays in memory.** `tap.create` puts the value in an in-memory `Secrets` map on the execution. `link.sendOneTime` reads it from there and hands it to `links.create()`. Events record only the TAP method's id, start time and lifetime. Before each append, a guard checks that no secret held in `Secrets` appears in the payload; a hit raises an error. The Day 8 scanner replaces this guard.
- **Verify against `expected`.** The plan's `expected` is free-form, written by the model. Each handler returns an `observed` dict of canonical facts (for example `exists`, `userPrincipalName`, `usageLocation`, `isMember`, `hasSku`). Code then checks two things:
  - the handler's own check, which is authoritative;
  - every `expected` key that also appears in `observed`, compared case-insensitively.

  Expected keys the handler doesn't know are listed as `unchecked` in the event, not failed.
- **Deviations escalate in Week 1** (no replanner until Week 3). These all lead to `step_failed`, then ESCALATED with `kind="execution"`:
  - a non-retryable `GraphError`;
  - a verify mismatch after the verify polls;
  - a target that exists but wasn't created by this run.

  The escalation payload carries the findings, the steps done, the failing step and the error. It also carries `rollback`, the compensations for the verified steps (Tech.MD §9). They are offered, not run.
- **Order.** Steps run one after another in a stable topological order, using `depends_on` plus implicit dependencies from `$ref`. Running steps in parallel is Day 9 tuning.
- **TAP lifetime fix.** The tenant policy is 60-480 minutes (setup_app.py), but the Day 3 plan asked for 720, which Graph would reject with a 400. Tighten `TapCreateParams.lifetimeInMinutes` to `ge=60, le=480`, with a description that names the policy. The model then sees the bound, and lint rule 1 catches a bad value.

## New and changed files

### `graph/writes.py`: one handler per Graph action

A `Handler` protocol with four async methods, each taking `(graph, params: dict, ctx)`:

- `existing(...)` returns the output dict if the target state is already true, otherwise None.
- `snapshot(...)` returns the pre-state.
- `act(...)` returns the output.
- `verify(..., output)` returns `(ok, observed)`.

`ctx` gives the handler the run's `Secrets` and whether this run created an object. A `HANDLERS: dict[action, Handler]` maps each action to its handler.

| Action | Existing | Act | Verify (polled) |
|---|---|---|---|
| `user.create` | `GET /users/{upn}`. Created by this run → done. Otherwise **deviation: "user already exists"**, which catches a missed reset or a real duplicate | `POST /users` with the params, the generated password and `accountEnabled` | `GET /users/{id}` (retry_replication): UPN, usageLocation and displayName match |
| `user.update` | every changed property is already equal | `PATCH /users/{id}` | changed properties match |
| `license.assign` | `licenseDetails` has the skuId | `POST assignLicense` (retry_replication) | `licenseDetails` has the skuId |
| `group.addMember` | 400 "already exist" on add, treated as done | `POST /groups/{gid}/members/$ref` (retry_replication: new users can 404 for ~60 s) | `GET /users/{uid}/memberOf?$select=id` contains gid |
| `tap.create` | a TAP from this run that is already verified while its link isn't sent → deviation (the value is gone). Any other existing TAP → deviation | `POST .../temporaryAccessPassMethods`, then the value goes into `Secrets` | the TAP method id is in the list. `isUsable` is not compared: a TAP that starts on the start date reads back "NotYetValid" until then |

Verify polls `VERIFY_DELAYS = (0, 2, 4, 8, 16)` seconds, since directory reads lag behind writes. `GraphClient` already handles 429, 5xx and replication lag on the act call. The executor doesn't retry acts itself.

### `execution/mocks.py`

`license.purchase`:

- **Existing:** `seat-holder@{domain}` no longer has the skuId.
- **Act:** `assignLicense` with `removeLicenses=[skuId]` on seat-holder.
- **Verify:** seat-holder's `licenseDetails` lacks the skuId. Verify doesn't read `subscribedSkus.consumedUnits` because it lags (seed.py comment); the available count is recorded in `observed` for information only.
- **Compensate:** give the seat back.

### `execution/links.py`: minimal (the view page comes on Days 6 and 8)

- A `Link` table: id, run_id, step_id, token, ciphertext, user_id, to, includes, note, expires_at, viewed_at.
- `create(store, ...) -> Link` encrypts the TAP with Fernet. The key is a new `LINK_KEY` setting in `.env`; `Settings` creates it on first load if it's missing.
- Expiry is the TAP's lifetime. Each link is idempotent on (run_id, step_id).

### `execution/scheduler.py`: minimal

- A `Followup` table: id, run_id, step_id, kind, run_at, params, status (scheduled, done or failed).
- `schedule(...)` is idempotent on (run_id, step_id).
- `run_now(store, graph, followup_id)` runs `FOLLOWUP_ACTIONS[kind]` through the same executor step path. This is cheap now, and the Day 6 "run now" button reuses it.

### `execution/catalogue.py`

`ActionSpec` gains `handler`: the matching `HANDLERS` entry, the mock, or the link or scheduler handler. The `verify` and `compensate` strings stay as docs and are used for the rollback list.

### `execution/executor.py`

`Execution(store, run_id, graph, domain, link_key).run(plan) -> ExecResult(ok, outputs, failed_step, error)`. The runner applies the approver's answers before calling it. For each step, in order:

1. Skip the step if it already has a `step_verified` event (resume).
2. Resolve the refs: `$ref:<s>.output.id` comes from `outputs`, and `.output.tap` from `Secrets`. Re-validate the step against its params model.
3. Call `existing`. If the target state is already true, record `step_verified` with `{skipped: "already true"}`.
4. Emit `step_started` with the action, the resolved params and the snapshot. The secret guard runs before the append.
5. Act, then verify, inside a Logfire span `step {action}`.
6. Emit `step_verified` with `output`, `observed`, `unchecked` and `t`, or `step_failed` with the error, whether it was retryable, and the observed state.

### `runner.py`: the state machine

- A `TRANSITIONS` map from Tech.MD §3. `transition(store, run_id, to, **detail)` refuses moves that aren't allowed, so for example `approve` on a DONE run fails.
- `start(store, graph, text, from_address, facts, domain)`: intake, then investigate, then `gate.check`. This logic moves out of `cli.run`. If the gate leaves the run at PLAN_CHECKED (nothing to approve and the requester is approved), it goes straight to `_execute`.
- `approve(store, graph, run_id, *, by, answers, reject=False)`:
  - Allowed from AWAITING_APPROVAL or HELD.
  - `by` must be an approver for the run's intents (`facts.is_approver`). On a HELD run, this approval also confirms the requester, as Tech.MD §8 says.
  - Writes an `approval` event with `{by, decision, answers}`.
  - `reject` leads to ESCALATED with `kind="rejected"`. Rejecting single steps needs the Week 3 replanner.
- **Overrides** (`apply_answers(plan, answers)`):
  - `upn_format=<local or full address>` sets `user.create` `userPrincipalName` and `mailNickname`, adding `@{domain}` if it's missing.
  - `start_date=YYYY-MM-DD` moves each `followup.schedule` `runAt` to that date, keeping its time.

  The plan is then re-validated, so `nickname_matches_upn` still holds. Overrides change no IDs or actions, so the gate decisions stand.
- `_execute` goes EXECUTING → `executor.execute` → CLOSING → DONE. CLOSING is a pass-through until close-out lands on Day 8. A failure leads to ESCALATED with the hand-off payload.
- `resume(run_id)` is allowed from EXECUTING. It re-enters `_execute`, which skips the verified steps. If a TAP was created but its link wasn't sent, resume escalates, as above.

### `cli.py`

- `run` calls `runner.start`.
- New commands:
  - `tech approve <run_id> [--by tgarcia] [--confirm key=value ...] [--reject]`. `--by` defaults to the first `user.onboard` approver.
  - `tech resume <run_id>`
  - `tech followup run <id>`
- The printer is shared by all commands. It replays the run's history after `seq` and prints the new event types. `show()` gains:
  - `step_started`: `→ step  action`
  - `step_verified`: `✓ step  (skipped: …)`
  - `step_failed`: `✗ step  error`
  - `approval`: the approval line
- Exit codes add 0 for DONE. ESCALATED stays 2.

### `core/store.py`

Add the `Link` and `Followup` tables (SQLModel `create_all`; there are no migrations yet). Add `last_event(run_id, type)` and `events_of(run_id, type)` helpers.

## Tests (offline, respx and in-memory store)

- **`test_executor.py`, handler cases** (all handlers and the mock run against `fake_tenant.py`, a stateful Graph stand-in behind respx, rather than in separate `test_writes.py` and `test_mocks.py` files):
  - acting sends the right request and body;
  - `existing` short-circuits;
  - verify polls until the read matches, using a fake sleep;
  - a mismatch is reported;
  - `user.create` on an existing UPN is a deviation;
  - the password is never in the returned output.
- **`test_executor.py`, plan cases, using the conftest onboarding plan:**
  - it runs in dependency order and resolves `$ref` IDs from earlier outputs;
  - a resume skips verified steps;
  - a 403 leads to `step_failed`, then ESCALATED with `rollback` for the done steps;
  - a group add that 404s twice before succeeding has no deviation;
  - **the TAP value is in no event payload** (scan every event), and the link ciphertext decrypts to it.
- **`test_runner.py`:**
  - `approve` with `upn_format=pnair` changes the created UPN and mailNickname;
  - `start_date` moves `runAt`;
  - approving from DONE is refused;
  - a non-approver `--by` is refused;
  - approving a HELD run executes it;
  - a reject leads to ESCALATED;
  - PLAN_CHECKED with nothing to approve runs automatically.
- **`test_cli.py`:** add the new event shapes to `EVENTS`.
- **`test_live_onboard.py`** (`-m live`): the Week 1 exit as a test. It runs `start` and then `approve`, then checks the result through Graph.

## Verification

1. `uv run pytest` and `uv run ruff check backend`.
2. `uv run tenant/reset.py`, then `uv run tenant/reset.py --dry-run` shows nothing to undo.
3. `uv run tech run "<Priya ticket>" --from tgarcia` ends in AWAITING_APPROVAL, with the purchase and the TAP marked approve.
4. `uv run tech approve <run> --confirm upn_format=priya.nair`. Every step should print ✓ and the run should reach DONE.
5. Check in Graph or the Entra admin center:
   - Priya has the right UPN and usageLocation US;
   - she is in Accounting and Finance Files, but not in RDS-Users or the org-wide group;
   - she has an active TAP;
   - seat-holder has no license, and the pool shows 1 free seat;
   - a `Followup` row exists for the license on the start date.
6. `uv run tech followup run <id>`: Priya now has the license, with a `step_verified` event.
7. Search the store and the `.logfire` data for the TAP value. It should appear nowhere.
8. `uv run tenant/reset.py --dry-run` lists Priya, her TAP and the seat-holder's seat as the only things to undo.
9. Run `tech approve` again on the DONE run. It should be refused.

## Risks

- **TAP timing for a future start date.** A TAP that starts now and lasts 8 hours will have expired by Priya's Monday. The plan should set `startDateTime` on the start date. Today that's the model's choice, so check the live plan. If it's wrong, add one gotcha line to `user.onboard.yaml` rather than overriding it in code.
- **Seat-freeing lag.** The license follow-up's `assignLicense` can fail for about a minute after the purchase frees the seat (seed.py waits 15 s × 6). That only matters for "run now" during the demo, so the follow-up's act retries "no available licenses" a few times.
- **Week 1 exit wall clock.** It's sequential, with 4-5 Graph writes and verify polls. Measure it in Logfire now; the under-3-minute bar is Day 9.
