# Week 3 Implementation Plan: Offboarding, MFA, the Replanner and Autonomy

## Context

Weeks 1-2 shipped the onboarding scenario live, end to end, under 3 minutes ([Build-W1-2.md](Build-W1-2.md)). Day 10 closed the demo-readiness bar; the backend runs the full onboarding path through the UI with close-out, and Day 9 saved onboarding phrasing variations to `backend/tests/evals/onboard_*.yaml` as seed fixtures for this week's eval harness.

Week 3 takes the one-scenario demo to **three live scenarios** (onboarding, offboarding, MFA reset) and makes execution robust; the fourth demo scenario (no-playbook file-restore + learning) is Week 4. The scope wall in [Build-W1-2.md:14](Build-W1-2.md) deferred eight workstreams to "Week 3." Of those, the **no-playbook path and "Save as playbook" learning are moved to Week 4** (they are their own large workstream; [Build-Day4.md:40](Build-Day4.md) already labels the no-playbook path Week 4). Everything else lands here.

**Scope (in Week 3):**
1. **Offboarding** — the `user.offboard` playbook and its write actions ([Tech.MD §6.2](Tech.MD)).
2. **MFA reset** — the `mfa.reset` playbook and its write actions ([Tech.MD §6.3](Tech.MD)), with **mocked clean sign-in and auth-method history for Dennis** (see Decisions).
3. **Replanner + escalation write-up** — the Opus replanner on deviation ([Tech.MD §4.7](Tech.MD)) and the Sonnet hand-off write-up for runs that escalate in execution.
4. **Linter rules 4-7** — definition-of-done covered, must-investigate covered, questions resolved, ordering sane ([Tech.MD §4.3](Tech.MD)).
5. **Autonomy levels A1/A2/A3** in the gate ([Tech.MD §8](Tech.MD)).
6. **Safe mode** — record/replay fallback for the live demo ([Tech.MD §10](Tech.MD)).
7. **Eval harness** — the 28-case matrix ([Tech.MD §11](Tech.MD)), run offline and live.

**Not in scope until Week 4:** the no-playbook path (§4.9), "Save as playbook" learning (§4.10), diagnose mode and devices (demo v2).

**Exit:** offboarding and MFA reset run live, end to end, like onboarding does today; a run that deviates in execution replans (within bounds) or escalates with a written hand-off; the eval harness passes the 22 in-scope cases offline and the three live scenarios pass; the demo can fall back to replay if the network/Graph/model fails.

## Decisions carried in

- **Mock Dennis's sign-in and auth-method history for the MFA happy path, at the read layer.** On the live tenant, Dennis's account and Authenticator were created the same day, and successful sign-ins minutes apart from different cities read as a takeover — the model correctly escalates, which breaks the happy-path demo ([spike/results.md](../../spike/results.md), finding 1). Tech.MD §10 already allows mocked sign-in data. The mock lives in the **read layer** (`graph/reads.py`/`run_read`), scoped to **Dennis's single GUID behind a setting**, returning a clean 14-day `signIns.list` and a known Authenticator method — and recorded as a normal `tool_call` event so the Transcript's grounding and the eval harness see it exactly like a live read. The **suspicious** (eval #12) and **service-account** (`reports@`, #20) paths read real/seeded data so the refusal beats stay genuine; the clean and suspicious cases are asserted in the same test run so the mock can't bleed across.
- **`requester.confirmOutOfBand` is a new mock and a run-level gate precondition, not a plan step.** It does not exist yet (Tech.MD §7 lists it; [mocks.py](../../backend/tech/execution/mocks.py) has only `license.purchase` and the onboarding mocks). Model it as the demo's "Manager confirmed via SMS" button that records an `oob_confirmed` event; the gate requires that event before any `security.credential` step on an `mfa.reset` run, and `runner.accept()` refuses EXECUTING until it is recorded — so OOB can't be reordered after a credential write. Back it with the `oob_confirmed` DoD probe (§6.3 item 1, "before any write").
- **Autonomy lands as code, but the demo's behavior is unchanged.** `user.onboard`, `user.offboard` and `mfa.reset` are all **A2** in [client_facts.yaml:28-32](../../backend/tech/playbooks/client_facts.yaml). A2 is exactly what the gate does today (requester hold → never/catalogue → risk class → blast radius). So the autonomy rule must be a no-op at A2, force `block` on every agent write at A1 (checklist hand-off), and relax `write.reversible`/`access.revoke` to `auto` at A3 — and A3 never waives `spend`/`data.access`/`security.credential`. The A1/A3 code paths get tests but aren't exercised by the four scenarios; A3 is the Week 4 learned-playbook lever.
- **Split `core/models.py` into a `core/models/` package first, with a leaf `base.py`.** [Build-W1-2.md:60](Build-W1-2.md) calls for this "when Week 3 adds the offboarding and MFA actions." The split is `base.py` (the shared primitives `Intent`, `RiskClass`, `GUID_RE`, `parse_ref`, the ID annotateds — these are the circular-import trap, so they go in the leaf module everything imports), then `params.py`, `plan.py`, `triage.py`, `gate.py`. `__init__.py` re-exports the full public surface so **no importer changes** — the ~20 `from tech.core.models import …` sites across 13 source + 6 test modules all resolve unchanged. The proof the split is complete is `uv run pytest` green with **zero test-file edits**.
- **Linter rules 4-7 land Day 11, right after the split — before the action work.** They change the lint-pass bar for *every* playbook, including onboarding, and every eval case asserts "the plan passes the linter." If they land late, onboarding gets re-verified twice and the eval harness is built against a moving target. The eval harness (Day 15) is built last, against a frozen linter.
- **The replanner reuses the existing linter and gate unchanged.** Per §4.7 the amended plan goes back through `lint()` and `gate.evaluate()`; **only new steps, or steps whose risk class increased, need fresh approval.** The `REPLANNING` state and its transitions (`EXECUTING→REPLANNING→EXECUTING`) are already wired in [runner.py:56-57](../../backend/tech/runner.py) and the `replan` event type is already registered — nothing calls them yet. The replanner slots between `step_failed` and `ESCALATED`.
- **The escalation write-up pairs with the replanner** and reuses the close-out secret scanner. It is a Sonnet draft (no tools, never sees a TAP/link/ticket), produced only when a run escalates during execution. Today those runs get no text ([Build-Day8.md:1526](Build-Day8.md)).
- **Safe mode is a thin record/replay shim, not a framework.** Record every live run's events, Graph responses and model outputs; on a live failure the presenter replays the last good run of that scenario with realistic timing. The UI marks it discreetly. Scope it to the demo fallback only (§10).
- **The eval harness has two tiers.** An **offline** tier runs all cases that don't need a live tenant against `FakeTenant` + `FunctionModel`/recorded model outputs (fast, in CI); a **live** tier (`-m live`) runs the four demo scenarios against the real tenant with a reset between cases. Cases that depend on live model judgement (intent, plan shape) run live; deterministic gate/lint assertions run offline.

## Repo layout (deltas from Week 1-2)

```text
backend/tech/
  core/models/              split from models.py; __init__ re-exports the full public surface
    base.py                 shared primitives (Intent, RiskClass, GUID_RE, parse_ref, ID annotateds)
    params.py               + SessionsRevokeParams, MailboxAutoReplyParams, MailboxForwardRuleParams,
                            GroupRemoveMemberParams, GroupAddOwnerParams, AuthMethodDeleteParams
    plan.py / triage.py / gate.py
  agents/replanner.py       Opus; amended-remainder Plan output; same read tools + linter as investigator
  agents/closeout.py        + escalation write-up draft (Sonnet, scanned)
  execution/catalogue.py    + ActionSpecs: sessions.revoke, mailbox.autoReply, mailbox.forwardRule,
                            group.removeMember, group.addOwner, authMethod.delete
  graph/writes.py           + Handlers for the above (tap.create, link.sendOneTime already exist)
  graph/reads.py            + read tools mailboxSettings.get, messageRules.list (+ riskyUsers.get if absent);
                            + Dennis clean sign-in / auth-method read-layer mock (one GUID, behind a flag)
  execution/mocks.py        + requester.confirmOutOfBand ("Manager confirmed via SMS" -> oob_confirmed)
  execution/dod.py          + offboarding + mfa probes (incl. oob_confirmed before any write)
  safety/lint.py            + rules 4-7 (DoD, must-investigate, questions, ordering)
  safety/gate.py            + autonomy rule (A1/A2/A3) in the rule order; MFA OOB precondition
  safety/replan.py          replan bounds + "which steps need fresh approval" diff (or in runner.py)
  runner.py                 + replan() phase; execute() routes step_failed -> REPLANNING -> execute;
                            accept() refuses EXECUTING on mfa.reset until oob_confirmed; escalation write-up
  safety/recorder.py        safe-mode record/replay shim (Graph responses + model outputs + events)
  playbooks/user.offboard.yaml   from Tech.MD §6.2
  playbooks/mfa.reset.yaml       from Tech.MD §6.3
  core/store.py             + escalation_writeup, oob_confirmed event types (replan already exists)

backend/tests/
  evals/                    onboard_*.yaml (from Day 9) + offboard_*.yaml, mfa_*.yaml; cases.yaml (1-22 live now, 23-28 xfail=Week 4)
  eval_harness.py           loads cases, runs offline (FakeTenant) + live tiers, asserts the §11 pass bar
  fake_tenant.py            extend for sessions.revoke, mailbox.*, group.removeMember/addOwner, authMethod.delete

web/
  lib/useRun.ts             + escalation_writeup (+ replan already present) event types to match store.py
  components/               replan steps, escalation write-up, autonomy badge, safe-mode indicator
```

## Day-by-day

The through-line: Weeks 1-2 proved one playbook works end to end. Week 3 proves the **machinery generalizes** to three playbooks and survives a deviation — without the onboarding demo changing behavior. The single ordering trap: the linter's new rules change the lint-pass bar for *every* playbook and the whole eval harness asserts "the plan passes the linter," so rules 4-7 land first and the harness is built last, against a frozen linter.

### Day 11. Models-package split, linter rules 4-7, autonomy

Pure plumbing day: no new behavior, but it moves the lint/gate bar for all three playbooks, so it comes before the action work.

- **Split `core/models.py` → `core/models/`**: `base.py` (`Intent`, `RiskClass`, `GUID_RE`, `parse_ref`, the ID annotateds — the shared primitives that would otherwise form an import cycle), then `params.py`, `plan.py`, `triage.py`, `gate.py`. `__init__.py` re-exports the full public surface. **Gate:** `uv run pytest` green with zero test-file edits — that's the proof the re-export is complete.
- **Linter rules 4-7** in `lint.py` (extend the same per-step/per-plan loop; it already receives `plan`, `transcript`, `playbooks`, `facts`):
  4. **Covers the DoD** — every required `definition_of_done` item is produced by a step or named under `needs_technician` with a reason. Map probe-name → producing action, reusing `dod.PROBES` keys as the canonical set so lint and close-out can't drift; conditional items (`auto_reply_set`/`forward_rule_active` "if requested") are optional, held in a small required/optional table in the linter (the YAML carries "# if requested" only as a comment).
  5. **Must-investigate covered** — each `Playbook.must_investigate_tools` name (loader already strips `users.get(peer)`→`users.get`) has a matching `transcript.called(tool)`.
  6. **Questions resolved** — each `questions_to_resolve` is answered in `assumptions`/a `confirm_on_approval` entry, or the plan is a `questions_for_requester` pause (then waived). **Keep this lenient** (keyword presence) — over-strictness blows the investigator's 2-retry budget and causes spurious escalations.
  7. **Ordering sane** — `depends_on` points backwards, no cycles, checked at lint time (before `executor.order()` can crash on it), reusing the `needs`/`earlier` bookkeeping already in `lint()`.
- **Autonomy** in `gate.py` — rule step 4 in the order (after no-playbook, a Week 4 no-op here). Read the **strictest** `facts.autonomy[intent]` governing the step: **A1** → `block` every write (`rule="autonomy.a1"`, checklist hand-off); **A3** → relax `write.reversible`/`access.revoke` to `auto`, but `APPROVE_CLASSES` still win; **A2** → unchanged.
- **Gate (don't break Week 1-2):** re-run the known-good Priya fixture through all 7 linter rules and confirm it still passes; snapshot the onboarding gate decisions before/after the autonomy rule and confirm they're byte-for-byte identical (all three demo intents are A2, a no-op). Table-driven tests: each new rule fails a crafted bad plan; A1 blocks a reversible step, A3 autos it, A2 matches today.

### Day 12. Offboarding (`user.offboard`) end to end

Six new write actions, the biggest `FakeTenant` extension of the week. Build it before MFA because it shakes out the test infra.

- **Playbook** `playbooks/user.offboard.yaml` from [Tech.MD §6.2](Tech.MD) (approvers already in `client_facts.yaml`; loader auto-discovers it).
- **Actions** (the established four-touch pattern per action — param model + `Step` union member in `core/models/`, `ActionSpec` in `catalogue.py`, `Handler` in `writes.py`, DoD probe in `dod.py`): `sessions.revoke` (`access.revoke`), `mailbox.autoReply` (`write.reversible`), `mailbox.forwardRule` (`data.access`, with a validator rejecting external domains — the `never` rule), `group.removeMember` (`write.reversible`, mirror `GroupAddMember`), `group.addOwner` (`data.access`); reuse `user.update` (accountEnabled=false is already `access.revoke`) and `followup.schedule`/`link.sendOneTime`.
- **New read tools** in `reads.py`: `mailboxSettings.get` and `messageRules.list` — the offboarding `must_investigate` needs them and neither exists yet.
- **DoD probes** in `dod.py`: `account_disabled`, `sessions_revoked`, `auto_reply_set`, `forward_rule_active`, `removed_from_groups`, `ownership_transferred`, `followup_scheduled`, deriving targets from the run's events/plan (not from unresolved `$` expressions, per the Day-8 pattern).
- **`FakeTenant` extension** (`tests/fake_tenant.py`): stateful routes for `revokeSignInSessions`, `mailboxSettings` GET/PATCH, `messageRules` GET/POST, group member DELETE, group owners GET/POST — mirroring the verify reads exactly.
- **Gate:** no change beyond Day 11 — `_risk_rule` already autos `access.revoke` on an approved offboard.
- **Gate/tests:** `test_every_allowed_action_is_in_the_catalogue` (parametrized over `intents()`) now automatically covers offboarding — a natural tripwire. Executor/DoD tests against the extended `FakeTenant`: clean offboard by approver → full DoD; non-approver → HELD; never-signed-in → license-cancel recommendation; group-owner → ownership-transfer step (prefigures eval cases 7-10, 18-19).

### Day 13. MFA reset (`mfa.reset`) + OOB confirmation + the Dennis mock

- **Playbook** `playbooks/mfa.reset.yaml` from [Tech.MD §6.3](Tech.MD), including `triage_checks` (which_system / which_credential / really_mfa / service_account; loader supports them).
- **Action:** `authMethod.delete` (`security.credential`, already in `APPROVE_CLASSES`); reuse `tap.create`, `link.sendOneTime`, `followup.schedule`. Handler: `DELETE /users/{id}/authentication/{methodType}/{methodId}`, verify via `authMethods.list` absence. Plus read tool `riskyUsers.get` if not already present.
- **`requester.confirmOutOfBand` mock + gate precondition** (see Decisions): new mock in `mocks.py` ("Manager confirmed via SMS" → `oob_confirmed` event); the gate requires that event before any `security.credential` step on `mfa.reset`, and `runner.accept()` refuses EXECUTING until it's recorded; backed by the `oob_confirmed` DoD probe.
- **Dennis read-layer mock** (see Decisions): clean `signIns.list`/`authMethods.list` for Dennis's GUID behind a setting, recorded as a normal `tool_call`.
- **Tests:** clean history (Dennis mock) → OOB-then-TAP-via-link reaches DoD (eval 11); suspicious → escalate as takeover, zero writes (eval 12, asserted in the same run as 11 so the mock can't bleed); "text me the code" → still OOB + link, scanner blocks any code (eval 13); `reports@` → refuse (eval 20); vendor-app / VPN-password → not-this-playbook / needs_technician (21-22).

### Day 14. Replanner (Opus) + escalation write-up (Sonnet)

Both sit on the `step_failed → (replan | escalate)` seam and both reuse existing checks.

- **`agents/replanner.py`** (Opus, effort `high`): context = original plan, executed/verified steps and their observed state, the failing `step_failed` event, the read tools (reuse investigator's `READ_TOOLS`), the playbook; output = an **amended remainder** that may only add compensating steps to the done prefix. Register the same `lint` output-validator the investigator uses.
- **Re-approval diff (the §4.7 rule)** in `safety/replan.py` or `runner.py`: re-run `gate.evaluate()`; compare each amended step's `(id, action, computed risk_class)` against the original `gate_decision` events. Unchanged + already-approved steps stay approved; **only new or risk-increased steps** get re-gated → `AWAITING_APPROVAL` for just those, else back to `EXECUTING`.
- **Hook the wired state:** on executor failure in `runner.execute()`, if bounds allow, `EXECUTING → REPLANNING → replan() → EXECUTING` (re-call `execution.run`; it already skips `step_verified` steps). Bounds (§9): ≤3 replans (count `replan` events), 10-min wall clock, 40 writes (already `MAX_WRITES` in the gate). A 403 permission error escalates immediately, no replan.
- **Escalation write-up:** extend `agents/closeout.py` (same Sonnet, no tools, scanner-checked, never sees ticket/TAP/link) with an `EscalationWriteup` output + a `build_context` assembling the §9 hand-off package (findings, done steps, failing step, hypotheses, suggested next step). Emit a new `escalation_writeup` event at the ESCALATED edges in `runner.py` (so gate-block, dod, execution and bound escalations all get text). Add the event type to **both** `store.py` and `web/lib/useRun.ts`.
- **Tests:** FunctionModel replanner amends after a seeded verify-mismatch → DONE, with an unchanged downstream step **not** re-approved and a new compensating step **re-gated**; 4th deviation → ESCALATED with the package; a single-step approver reject replans the remainder ([Build-Day5.md:106](Build-Day5.md)); the write-up is scanned and secret-free.

### Day 15. Safe mode + the eval harness + rehearse

- **Safe mode** (`safety/recorder.py`, thin): record each live run's events (already in the store), Graph responses (a `GraphClient` decorator keyed by request) and model outputs (an agent-output tap) to a per-scenario `safe_mode/` fixture. Replay = a replay `GraphClient` + a recorded-output model, handed in via the existing `Runtime` injection seams (`Runtime.graph`, the `*_model` params) so run logic is untouched; play back with realistic timing. Last-good-run exact-replay only — no fuzzy matching. UI: a discreet "replay" badge derived from a run-level flag.
- **Eval harness** (`backend/tests/evals/`, new dir; Day 9 already seeded `onboard_*.yaml` here): `cases.yaml` holds the cases as data (ticket, from, expected intent, lint outcome, gate decisions, terminal state, DoD/escalation shape). `eval_harness.py` runs two tiers — **offline** (fresh `FakeTenant` per case + FunctionModel/recorded outputs; asserts intent, lint, gate decisions, terminal state, and **zero gate bypasses** by cross-checking `step_started`/`step_verified` events against non-blocked `gate_decision` events) and **live** (`-m live`, the four scenarios, tenant reset between cases). Implement **cases 1-22**; **cases 23-28 are Week 4** (no-playbook + learning) — mark them `xfail`/skip with a reason. Week 3's bar is **22/22 in-scope**.
- **Web:** render replan steps in the live-execution panel, the escalation write-up on an escalated run, an autonomy badge on the plan, the safe-mode indicator. Read `web/node_modules/next/dist/docs/` before new components (`web/AGENTS.md`); no new runtime deps.
- **Rehearse:** reset, run offboarding + MFA happy paths + one deviation→replan + one escalation in the UI 3-5×; record wall-clock and cost; exercise the replay fallback once; fix the flakiest step.
- **Docs:** update [Product.MD §4](Product.MD) (real vs. mocked — add the Dennis sign-in mock and `requester.confirmOutOfBand`) and note the Week 4 carve-out.

## Verification

- `uv run pytest` and `uv run ruff check backend`: green, offline, no network — the models-split proof (zero test-file edits), the offline eval tier (22/22 in-scope cases), and the A1/A2/A3 gate tests all pass. The known-good Priya plan still passes all 7 linter rules, and the onboarding gate decisions are unchanged.
- `uv run pytest -m live` after `uv run tenant/reset.py`: onboarding, offboarding and MFA reset each reach DONE with every DoD probe ✔. MFA requires an `oob_confirmed` before any credential write; the happy path uses the Dennis mock and does **not** escalate; the suspicious and `reports@` service-account tickets escalate correctly (asserted alongside the happy path so the mock can't bleed).
- Replan path: force a verify mismatch (via `FakeTenant` fault injection) and confirm `EXECUTING → REPLANNING → EXECUTING → CLOSING → DONE`, with only new/risk-increased steps re-approved.
- Escalation path: force a non-retryable 403 and confirm `ESCALATED` with a scanned, secret-free write-up shown in the UI.
- In `web/`: `npm test`, `npx tsc --noEmit`, `npm run lint`, `npm run build` clean; the new scenarios render; the safe-mode indicator shows on a replayed run.
- `grep` the event table for any TAP/link token after a live MFA run: none present.

## Risks

- **The models-package split touches every `tech.core.models` importer.** Mitigation: do it alone on Day 11 behind `__init__` re-exports, verify `pytest` green before any feature work; the diff is pure moves.
- **MFA mocking leaks into the real refusal paths.** If the Dennis mock is too broad, the suspicious/service-account beats stop escalating and the safety story collapses. Mitigation: scope the mock to Dennis's happy-path identity only; keep #12/#20 on real/seeded data; assert both escalate in the live eval.
- **Autonomy silently changes the onboarding demo.** Any change to the A2 path alters Week 1-2's verified behavior. Mitigation: a test that pins the onboarding gate output byte-for-byte before and after the autonomy rule; A2 must be a no-op.
- **Replanner loops or touches done steps.** Mitigation: hard bounds (≤3 replans, 10 min, 40 writes); the amended plan can only add compensating steps to the done prefix; re-lint/re-gate catches an out-of-scope amendment.
- **Replanner re-approval diff is subtly wrong → a risk-increased step slips through as `auto` (a gate bypass).** Mitigation: the "zero gate bypasses" eval assertion is the backstop; unit-test the diff with a compensating step whose class is higher than the one it replaces.
- **`FakeTenant` drifts from real Graph for the six new offboarding endpoints** (e.g. `revokeSignInSessions` returns no body; the `signInSessionsValidFromDateTime` verify). Mitigation: mirror the verify reads exactly in `FakeTenant`; keep a `-m live` offboarding test as the source of truth, like `test_live_onboard.py`.
- **Offboarding auto-reply / forward rules hit Exchange mailbox provisioning lag.** Flagged in [Build-W1-2.md:216](Build-W1-2.md). Mitigation: the executor's existing verify-poll backoff (`VERIFY_DELAYS`) covers it; keep demo forwards internal (external forwarding is blocked by policy and is a `never` rule).
- **Eval harness is flaky live** (model variance). Mitigation: assert the deterministic parts (intent, gate decisions, terminal state) offline; the live tier checks the four scenarios reach DONE/the right escalation, not exact plan text.
- **Safe mode over-built.** Mitigation: one recorder shim behind a flag, replay only the last good run per scenario; it's a fallback, not a feature.

## Open items

- Confirm the **in-scope eval seed state**: a few cases (#14 two intents, #16 future start date) may need tenant state the current seed doesn't create; extend `tenant/seed.py` on Day 15 if so.
- Rule 6 (questions resolved) is the fuzziest linter rule; start lenient and only tighten if a case slips through, to protect the investigator's 2-retry budget.
