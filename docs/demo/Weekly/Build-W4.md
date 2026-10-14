# Week 4 Implementation Plan: Eval Harness, No-Playbook Runs, Learning Playbooks, and the Full Demo

## Context

Week 3 takes the demo to three live scenarios (onboarding, offboarding, MFA reset) and adds the replanner, the escalation write-up and safe mode ([Build-W3.md](Build-W3.md)). **The eval harness planned for Week 3 Day 15 did not land there; it opens this week.** Everything else in Week 4 is measured against it.

Week 4 builds the fourth demo scenario, the one that answers "what happens with the rest of my queue?" ([Product.MD §3.4](../Product.MD)). It also widens "learning" from what Tech.MD describes:

1. **No-playbook runs** ([Tech.MD §4.9](../Tech.MD)): a ticket type with no playbook is still investigated, then either planned using only the no-playbook allow-list (every write needs approval) or escalated with a diagnosis.
2. **Learning a playbook from four sources.** Each becomes a draft that goes through the same code checks and human approval:
  - **an agent run** the technician approved (Tech.MD §4.10, the demo beat);
  - **a technician's own run in our console**: the technician picks catalogue actions in the workspace, and our executor runs and verifies each one, so the steps are recorded exactly like an agent's;
  - **a playbook the technician writes**, in plain English or YAML;
  - **audit logs** of a fix the technician made in the Entra admin center, read from `directoryAudits` (Tech.MD §4.10 called this product-only; we build a scoped version).
3. **One playbook store.** Shipped and learned playbooks are the same thing: versioned YAML rows in the store. The repo's YAML files only seed the shipped v1.

**Already in place** (from earlier weeks):

- `file.restore_version` is in the `Intent` literal ([base.py:30](../../../backend/tech/core/models/base.py)).
- The `files.search` and `files.versions` read tools exist ([reads.py:229-275](../../../backend/tech/graph/reads.py)).
- The seed has Sophie, Grace, the Northwind Operations team site and the versioned spreadsheets ([tenant/seed.py](../../../tenant/seed.py)).
- Reset stages the file versions (`--files base` or `--files overwrite`).
- A3 already relaxes `write.reversible` to `auto` ([gate.py:80](../../../backend/tech/safety/gate.py)).
- `playbook_drafted`/`playbook_approved` are registered event types ([store.py:44](../../../backend/tech/core/store.py)).
- `tests/fake_tenant.py` and Day 9's `backend/tests/evals/onboard_*.yaml` seed fixtures exist.

**Scope (in Week 4):**

1. **Eval harness** (moved from W3 Day 15): `cases.yaml` plus `eval_harness.py`, offline and live tiers, cases 1-22 passing and 23-28 `xfail` until their features land.
2. **No-playbook path**: open intents, a generic investigator with tighter bounds, an `Escalation` outcome, linter rules 3-6 in no-playbook mode, and gate rule 3.
3. `file.restoreVersion`: four-touch, with its DoD probe and FakeTenant routes.
4. **Playbook store** (one store for every playbook), the drafter, code checks, versioning, autonomy overrides, and demotion.
5. **Learning from people**: technician console runs, written playbooks, and audit-log observation.
6. **Web**: the no-playbook banner, the escalation diagnosis, the "Do it myself" panel, Save as playbook, the review screen side by side with its source, the library, a new-playbook editor, and the learned badge.
7. **Evals 23-28**, plus cases for the new learning sources. The bar is **28/28 plus the new cases**.
8. **The full 10-minute demo**: the audit trail, the time-saved card, reset/preflight, and rehearsal.

**Length:** 7 build days (Days 16-22), because the eval harness and the two extra learning sources were added. The cut line is at the end of the day-by-day.

**Not in scope until Week 5:**

- learning from the M365 unified audit log (SharePoint/Exchange changes) and from RMM script history;
- playbook layering across MSPs and clients ([expansion-to-multi-customers.md §2.4](../../overall/expansion-to-multi-customers.md));
- a PSA integration;
- pydantic-graph.

**Exit:**

- The eval harness runs offline in CI and live on demand.
- Sophie's file-restore runs live with no playbook (one approval, verified restore, reply).
- "Save as playbook", one edit, then A3; Grace's ticket closes with no human involved.
- A technician's console run, a written playbook, and an Entra admin-center fix observed through audit logs each produce a draft that passes the code checks and can be approved.
- The outside-contact ticket escalates with the missing action named.
- 28/28 plus the learning cases pass offline, and the four scenarios pass live.
- The full 10-minute demo has been rehearsed, with replay as a fallback.



## Decisions carried in

- **The eval harness comes first, so that Week 4 is built against it** (the same argument as Week 3's "linter first"). Two tiers, as Build-W3 designed:
  - **Offline:** a fresh `FakeTenant` per case, FunctionModel or recorded outputs. It asserts the intent, the lint result, the gate decisions and the terminal state, and **zero gate bypasses**: every `step_started` must follow a non-blocked `gate_decision`, and an `approve` step needs an `approval`, or a technician actor for console runs.
  - **Live:** `-m live`, with a tenant reset between cases.
  - Cases are data (`cases.yaml`). The case runner calls the same `runner` functions the API does, so there is no special path.
- **One playbook store.** Every playbook is a `PlaybookVersion` row with these columns:
  - `intent`, `version`, `yaml` (text);
  - `status`: `draft`, `approved` or `retired`;
  - `source`: `shipped`, `agent_run`, `technician_run`, `written` or `audit_log`;
  - `source_run_id`, `approved_by`, `created_at`.
  - On startup, `PlaybookLibrary.sync_shipped()` imports each `playbooks/*.yaml` as an approved `shipped` version if it is absent or its content hash changed. Editing a shipped playbook in the UI creates v2 (`source=written`).
  - Per-client autonomy moves to a `ClientAutonomy` table, seeded from `client_facts.autonomy`.
  - `client_facts.yaml` itself stays a file. Learning never writes client facts.
  - The `PlaybookLibrary` on `Runtime` replaces the loader's module functions (`intents()`, `load_playbooks()`, `prompt_text()`). The loader keeps the YAML parsing and validation, used for seeding and by tests through a library built from the shipped files.
  - Reset deletes non-shipped versions and autonomy overrides, which resolves [tenant/reset.py:173](../../../tenant/reset.py).
  - Why one store: one load path, an audit/version history for every playbook, and per-client autonomy and multi-tenancy (Week 5) work the same way for shipped and learned playbooks.
- **Intake intents become open slugs.** `Triage.intents: list[IntentSlug]`, a constrained `str`: dot-separated lowercase words such as `file.restore_version`, or `unknown`.
  - The intake prompt lists the library's intents and asks intake to reuse one when it fits.
  - A validator rejects near-duplicates of a known intent.
  - Code decides "has a playbook" with `library.has(intent)`.
  - `Intent` stays as an alias so that importers don't change.
- **No-playbook mode is a run property.** If no triaged intent has a playbook, the run is `no_playbook=True`.
  - **Investigator:** a generic guideline, at most 15 reads, a 60 s timeout. It outputs a `Plan` with the existing `escalate` flag, plus new `Escalation` fields: `diagnosis`, `evidence`, `checked`, `suggested_next_step`, `missing_action`.
  - **Linter:** no-playbook mode replaces the forced escalation at [lint.py:91](../../../backend/tech/safety/lint.py):
    - rule 3 checks scope against `NO_PLAYBOOK_ALLOWED`;
    - rule 4 requires a non-empty `expected` on every step, plus a reply;
    - rules 5 and 6 are skipped.
  - **Gate:** rule 3 sits before autonomy. A step not on the allow-list is `block` (`no_playbook.not_allowed`); otherwise it is `approve` (`no_playbook.approve`). A3 can never auto it.
  - `NO_PLAYBOOK_ALLOWED` goes in [safety/policy.py](../../../backend/tech/safety/policy.py), pinned by a test: `user.update` (non-security fields), `group.addMember`/`group.removeMember`, `mailbox.autoReply`, `file.restoreVersion`, `link.sendOneTime`.
- **Every learning source produces the same thing.** Each source is reduced to a `DraftSource`:
  - the triage;
  - **observed steps**, each with its action, params, verify result and actor;
  - the reads that informed them;
  - findings, with notes from the technician.
  - One drafter (Opus, effort `high`, no tools) turns a `DraftSource` into a `Playbook`. The sources differ only in how the observed steps are gathered:
    - **agent run:** the run's verified steps;
    - **technician console run:** the same executor events, with the actor set to `technician:{name}`;
    - **written:** no steps. The technician's prose (or YAML) is the input, with `allowed_actions` named explicitly;
    - **audit log:** `directoryAudits` entries mapped to catalogue actions (below).
- **The drafter never sees raw ticket text.** It gets the triage, not the email. That is how eval #28 ("remember for next time: always forward…") cannot reach a playbook. Technician notes and written prose are trusted input, because they come from an authenticated technician, not a ticket.
- **Code checks are hard failures** (`safety/playbook_check.py`):
  - every action is in `ACTIONS`;
  - every `must_investigate` tool is in `READS`;
  - every `definition_of_done` name is in `dod.PROBES`;
  - `never` includes the default rules for the action classes used;
  - the YAML round-trips;
  - **for sources with observed steps**, `allowed_actions` is a subset of the actions observed, with no new risk class.
  - The checks run on the draft (1 drafter retry) and again on the human-edited YAML at approval.
- **The autonomy ceiling depends on evidence.**
  - A playbook with no observed run behind it (`written`, or an edited `shipped`) can be approved at A1 or A2 only.
  - It becomes A3-eligible after 1 successful approved run on that version.
  - `audit_log` drafts start at A2 at most, because the inference from audit logs is indirect.
  - An A3 run that fails DoD, has a verify mismatch or replans is **auto-demoted to A2**, with an event. This is a deliberate change from Tech.MD's "A1"; A2 stops unapproved writes and keeps the playbook usable. One click demotes to A1.
- **Technician console runs ("Do it myself").** On a no-playbook, escalated or A1 run, the workspace offers a panel where the technician:
  - picks a catalogue action;
  - fills params from a picker of IDs **observed in this run's transcript** (grounded), or types a GUID that one confirming read resolves;
  - executes.
  - The step goes through `gate.evaluate` with a `technician_actor` flag. `never` rules and catalogue blocks still apply; `approve` is satisfied by the technician's own action. It then goes through the normal executor (idempotency, snapshot, act, verify). The events are the same, with `actor` added, so the audit trail, the DoD and "Save as playbook" work unchanged.
- **Audit-log learning is scoped to directory changes.**
  - On an escalated run, the technician clicks "I fixed it in the admin center", and we read `GET /auditLogs/directoryAudits` filtered to the window from escalation to now and to target IDs observed in the run.
  - We poll for up to 15 minutes for log lag and show "waiting for audit logs".
  - `audit_map.py` maps `activityDisplayName` → a catalogue action (e.g. "Add member to group" → `group.addMember`, "Update user" → `user.update`, "Revoke sign-in sessions" → `sessions.revoke`).
  - Unmapped activities are listed as "can't learn: not in the catalogue".
  - SharePoint/Exchange changes are in the unified audit log, not here; that is Week 5. So the audit-log demo case is a directory change, not the file restore.
  - **The first two hours of Day 20 are a spike** to measure real lag and filter support on the tenant before building the UI.
- **An all-**`auto` **plan skips** `AWAITING_APPROVAL`**.** Check that the runner goes from `PLAN_CHECKED` to `EXECUTING` when no step is `approve` (no demo plan has exercised this path). Add it if it's missing, and pin it with a test.



## Repo layout (deltas from Week 3)

```text
backend/tech/
  core/models/base.py       IntentSlug (open, validated); Intent kept as alias
  core/models/plan.py       + Escalation fields on Plan
  core/models/params.py     + FileRestoreVersionParams (driveId, itemId, versionId)
  core/models/learning.py   DraftSource, ObservedStep
  core/store.py             + PlaybookVersion, ClientAutonomy tables; + actor on step events;
                            + playbook_demoted, audit_observed event types
  playbooks/library.py      PlaybookLibrary over the store; sync_shipped() from playbooks/*.yaml
  playbooks/loader.py       parsing/validation only (used by sync_shipped and tests)
  agents/intake.py          prompt lists library intents; near-duplicate validator
  agents/investigator.py    no-playbook guideline + 15-read / 60 s bounds
  agents/drafter.py         Opus -> Playbook from a DraftSource; output validator = playbook_check
  learning/sources.py       from_run(), from_written(), from_audit_log() -> DraftSource
  learning/audit_map.py     directoryAudits activity -> catalogue action
  graph/reads.py            + directoryAudits read (not offered to the investigator)
  safety/policy.py          + NO_PLAYBOOK_ALLOWED
  safety/lint.py            no-playbook mode for rules 3-6
  safety/gate.py            + rule 3 (no-playbook); technician_actor path
  safety/playbook_check.py  code checks + autonomy ceiling
  execution/catalogue.py    + file.restoreVersion (write.reversible)
  graph/writes.py           + restoreVersion handler and verify
  execution/dod.py          + version_restored probe
  runner.py                 no_playbook flag; all-auto skip; technician_step(); auto-demote
  api.py                    + POST /runs/{id}/manual-step, /runs/{id}/playbook-draft,
                            /runs/{id}/audit-draft; GET /playbooks, /playbooks/{intent};
                            POST /playbooks (written), /playbooks/{intent}/approve, /playbooks/{intent}/autonomy
backend/tests/
  evals/cases.yaml          cases 1-28 + learning cases (L1-L4) as data
  eval_harness.py           offline + live tiers, zero-bypass check
  fake_tenant.py            + drive item versions, restoreVersion, directoryAudits
tenant/reset.py             + clear non-shipped playbook versions, autonomy overrides, runs
scripts/preflight.py        + library clean, files staged and searchable, AuditLog.Read.All
web/
  components/               NoPlaybookBanner, EscalationDiagnosis, ManualStepPanel, SavePlaybook,
                            PlaybookReview, AutonomyPicker, LearnedBadge, AuditTrail, TimeSaved
  app/playbooks/            library, /playbooks/new (written), /playbooks/[intent] (review/edit/approve)
```



## Day-by-day

The through-line: Weeks 1-3 proved the machinery works when a playbook says what good looks like. Week 4 proves it **stays safe without one** (the gate tightens, it doesn't loosen). It also shows that **every playbook, whoever it came from, enters through one door**: a code-checked draft plus a human approval, with autonomy earned by evidence. Ordering: harness first, then the store and no-playbook rules (they move the bar for every case), then the features, with eval cases un-xfailed as each lands.

### Day 16. The eval harness (moved from W3 Day 15)

- `backend/tests/evals/cases.yaml` holds cases 1-28 ([Tech.MD §11](../Tech.MD)). Each case has a ticket, from, any setup, the expected intent, the lint outcome, per-step gate decisions, the terminal state, and the DoD or escalation shape. Fold in Day 9's `onboard_*.yaml` variations.
- `eval_harness.py`:
  - **Offline tier:** a fresh `FakeTenant` and store per case, with a FunctionModel or recorded model output. It asserts all of the above plus the zero-bypass cross-check.
  - **Live tier:** `-m live`, the scenarios against the tenant, `reset.py` between cases. It asserts the deterministic parts and DONE or the correct escalation, not plan text.
- Cases 1-22 must pass. 23-28 are `xfail(reason="Week 4 Day N")`.
- **Seed gaps:** check whether #3 (disabled same-name user), #9 (never-signed-in), #14 and #16 need tenant state the seed lacks, and extend `tenant/seed.py` if so (W3 open item).
- **Gate:** 22/22 offline in `uv run pytest`. The live tier is run once and the results are recorded.



### Day 17. One playbook store, open intents, the no-playbook path

- **Store first** (alone, then green):
  - `PlaybookVersion`/`ClientAutonomy` tables;
  - `PlaybookLibrary` with `sync_shipped()`, `has()`, `intents()`, `get()`, `prompt_text()`, `autonomy()`;
  - intake, investigator, lint, gate and runner read through `Runtime.library`.
  - **Gate:** the eval harness passes 22/22 unchanged, and the onboarding gate decisions are byte-for-byte unchanged.
- **Open intents:** `IntentSlug` and the near-duplicate validator.
- **No-playbook mode:**
  - the runner flag;
  - the generic investigator guideline and bounds;
  - the `Escalation` fields;
  - linter no-playbook mode;
  - gate rule 3 and `NO_PLAYBOOK_ALLOWED`;
  - `escalate=True` → `ESCALATED` (`kind="no_playbook"`), with the W3 escalation write-up rendering the diagnosis.
- **Tests:**
  - `tap.create` with no playbook → block (un-xfail **#26**);
  - outside-contact → escalation naming the missing action (un-xfail **#25**);
  - A3 can't auto a no-playbook step;
  - a step missing `expected` fails lint;
  - `unknown` → no-playbook mode.



### Day 18. `file.restoreVersion` + Sophie live

- **Four-touch:**
  - `FileRestoreVersionParams` (IDs grounded via `files.search`/`files.versions`) and the `Step` member;
  - the `ActionSpec` (`write.reversible`; compensation restores the prior current version);
  - the handler `POST /drives/{d}/items/{i}/versions/{v}/restoreVersion`;
  - verify: the newest version matches the chosen one (size + modified);
  - the `version_restored` probe.
- **FakeTenant:** a stateful versions list; a restore appends a copy as current.
- **No-playbook close-out:**
  - the DoD is the step verifies plus `message_sent`;
  - the reply says "restoring creates a new version, nothing is lost";
  - the PSA note and time entry are written;
  - the snapshot exposes `can_save_playbook`.
- **Ambiguity (#24):** the generic guideline steers same-day versions with no time given to `questions_for_requester`, with no write.
- **Live:**
  1. `reset.py --files base`, then `--files overwrite`.
  2. Run Sophie's ticket via the CLI and approve.
  3. Confirm the current version in SharePoint.
  4. Add `test_live_file_restore.py`.
  - Un-xfail **#23, #24**.
- **Check first thing:** the SharePoint search-index lag on `files.search` after reset. If it's too slow, add a fallback that lists the team-site drive by name.



### Day 19. Drafter, code checks, approval, autonomy

- `DraftSource`/`ObservedStep` models. `learning/sources.py`:
  - `from_run(run)` (agent or technician actors);
  - `from_written(intent, text, allowed_actions, by)`, where the text is prose or YAML.
- `agents/drafter.py`: Opus, `high`, no tools, the `DraftSource` → `Playbook`, with `playbook_check` as its output validator (1 retry). It emits `playbook_drafted` with the source kind and the check results.
- `safety/playbook_check.py`: the checks plus the **autonomy ceiling** (`written`/edited-shipped → A2 max until 1 successful run; `audit_log` → A2 max).
- `PlaybookLibrary`:
  - `approve(intent, yaml, autonomy, by, source)`: re-check, new version, `playbook_approved`, and write the source run (if any) to `evals/learned/` as a regression case;
  - `set_autonomy()`;
  - `demote_on_failure(run)`, wired to the runner's escalate/replan edges.
- **API:** the draft, written, approve and autonomy routes.
- **Tests:**
  - a draft adding `tap.create` to a restore run → rejected;
  - a DoD name with no probe → rejected;
  - an approval-time edit widening `allowed_actions` → rejected;
  - a written playbook at A3 → rejected; after one successful run it is accepted;
  - approve → the next run is not no-playbook;
  - A3 → the restore is `auto` and skips approval (un-xfail **#27**);
  - a DoD failure at A3 → demoted to A2;
  - the drafter input contains no ticket text, and the library is unchanged by "remember for next time" (un-xfail **#28**).



### Day 20. Learning from people: console runs and audit logs

- **Spike (first 2 hours):**
  - In the Entra admin center, add a test user to a group and update a user field.
  - Measure when each change appears in `directoryAudits`.
  - Confirm that the `activityDateTime ge` and `targetResources/any(...)` filters work app-only with `AuditLog.Read.All`.
  - Write the results into this doc. If lag is over about 10 minutes, the demo shows a pre-recorded audit-log draft (safe mode) and the feature stays real but off-stage.
- **Technician console run:**
  - `runner.technician_step(run_id, step, by)`: allowed on no-playbook, escalated or A1 runs;
  - params are validated as a `Step`;
  - grounding is checked against the transcript (or one confirming read);
  - the gate runs with `technician_actor` (never/catalogue still block);
  - the executor runs it;
  - the events carry `actor`.
  - A run finished this way can close (DoD on the verified steps plus a technician note) and is `can_save_playbook`.
- **Audit-log learning:**
  - the `directoryAudits` read;
  - `audit_map.py`;
  - `from_audit_log(run, since, until)`: filter to the run's observed IDs, map, and list the unmapped activities;
  - the `/runs/{id}/audit-draft` route, polling with a timeout.
- **Tests:**
  - FakeTenant `directoryAudits` with mapped and unmapped entries → a draft containing only mapped actions, with unmapped ones listed;
  - a technician step with a `never` action → blocked;
  - a technician step with an ungrounded ID → rejected;
  - the zero-bypass check accepts technician actors and rejects agent `approve` steps without an approval;
  - learning cases **L1** (console run → draft → approve → next run uses it) and **L2** (audit-log draft at A2 max).



### Day 21. Web: the learning loop and Grace's hands-free run

- **Workspace:**
  - the "No playbook: every change needs approval" banner;
  - the escalation diagnosis panel;
  - the **"Do it myself"** panel (action picker → param form with an observed-ID picker → run; each step shows verified like the agent's, tagged with the technician's name);
  - **"Save as playbook"** (agent or console run);
  - **"I fixed it in the admin center"** (audit draft, with a waiting state).
- `/playbooks/[intent]` **review:**
  - the editable YAML side by side with its source (the run's steps and verify results, the audit entries, or the written prose);
  - check results inline;
  - an autonomy picker that respects the ceiling;
  - Approve;
  - the version history.
- `/playbooks/new`**:** intent name, plain-English steps, allowed actions (a catalogue checklist), then Draft and the same review screen. This is learning case **L3**.
- `/playbooks` **library:** shipped and learned together, with source, version and autonomy, the "Learned from 1 technician-approved run" link, and one-click demote.
- Read `web/node_modules/next/dist/docs/` before the new routes (`web/AGENTS.md`). No new runtime deps.
- **Live loop:**
  1. Sophie → Save as playbook → edit one line → A3 → Grace closes with **zero approvals**.
  2. Demote → Grace again → needs approval.
  3. Repeat once with a console run on a no-playbook ticket (case **L4**).



### Day 22. Audit trail, time-saved card, rehearse, docs

- **Audit trail** (Product.MD §5.3): a run tab rendering events as a table (read/write, actor, pre-state, change, approver, verify). It uses existing events.
- **Time-saved card** (Product.MD §5.5): the scenario's April-sample numbers ([invoice-learnings.md](../../overall/invoice-learnings.md)) next to this run's duration and approval count.
- **Reset/preflight:**
  - reset clears non-shipped versions, autonomy overrides and runs;
  - preflight checks the library is clean, the files are staged and searchable, and `AuditLog.Read.All` is granted.
- **Safe mode:** record scenario 3.4 (both halves), plus the audit-log draft if the Day 20 spike said so.
- **Full eval:** 28/28 plus L1-L4 offline; the four scenarios live.
- **Rehearse** the full 10-minute script ([Product.MD §6](../Product.MD)) 3-5 times from reset:
  - time each segment;
  - record cost;
  - exercise replay once;
  - fix the flakiest step.
  - Decide whether the console run or the written playbook gets a 20-second mention in the "unknown ticket" segment.
- **Docs:**
  - Product.MD §2 (four learning sources), §4 (real vs. mocked) and §5 (playbook screens);
  - Tech.MD §4.10 (sources, one store, autonomy ceiling, demote to A2), §8 (technician actor) and the "Week 4" section marked done;
  - Build-W3.md (harness moved).

**Cut line if behind (in order):**

1. the audit trail and time-saved cards (show the event log instead);
2. audit-log learning moves to Week 5, keeping the Day 20 spike results;
3. the `/playbooks/new` UI (written playbooks stay API-only).

The eval harness, no-playbook path, file restore, the drafter, and agent-run and console-run learning are on the critical path.

## Verification

- `uv run pytest` and `uv run ruff check backend` are green offline:
  - the eval harness at 28/28 plus L1-L4 with zero gate bypasses;
  - the no-playbook gate/lint tables;
  - `playbook_check` rejections and the autonomy ceiling;
  - library versioning, `sync_shipped` and demotion;
  - technician-step gating;
  - audit mapping.
  - The three existing intents' gate decisions are unchanged.
- After `uv run tenant/reset.py`, `uv run pytest -m live`: onboarding, offboarding, MFA reset and file restore reach DONE with every DoD probe ✔. A learned A3 run reaches DONE with zero `approval` events.
- Manual, in the UI from reset:
  1. Sophie: banner, one approval, verified restore (check the SharePoint version history).
  2. Save, edit, A3; Grace runs hands-free with the badge.
  3. Demote → approval needed.
  4. A console run on a no-playbook ticket → save → approve.
  5. A written playbook can't be set to A3.
  6. A group add done in the Entra admin center → audit draft lists it.
  7. The outside-contact ticket escalates with the diagnosis.
- `grep` the drafter's input events for the ticket body: not present.
- In `web/`: `npm test`, `npx tsc --noEmit`, `npm run lint` and `npm run build` are clean.
- The full 10-minute demo runs end to end, within time, 3 times in a row.



## Risks

- **Seven days, not five.** The harness and two extra learning sources add about 2.5 days. Mitigation: the ordered cut line. Audit-log learning is the first feature cut, and the spike decides early.
- **The store migration breaks the loader's callers.** Mitigation: land it alone on Day 17 behind `PlaybookLibrary`, with the eval harness (already in place from Day 16) plus the gate snapshot as the proof.
- **The drafter writes a broad or vague playbook.** Mitigation: the code checks bound it to the observed actions, the checks run again on edits, and a human approves every version.
- **Written playbooks skip the "run used it" safety rule** (there's no run). Mitigation: the autonomy ceiling. They can't be A3 until a real approved run succeeds, so every write still gets an approval until then.
- **Technician console runs become a gate bypass.** Mitigation: the technician actor only satisfies `approve`. `never` and catalogue blocks still apply, IDs must be grounded, and the zero-bypass eval check covers technician actors explicitly.
- **Audit-log lag or noise.** Mitigation: the Day 20 spike, a filter on the run's observed IDs and time window, unmapped activities shown rather than guessed, A2 at most, and a safe-mode recording for the demo.
- **Open intents fragment** (`file.restore` vs `file.restore_version`). Mitigation: library intents in the prompt, the near-duplicate validator, and 3 Grace phrasing variants that must map to the learned intent.
- **An A3 hands-free restore picks the wrong version.** Mitigation: the ambiguity rule (#24), auto-demote on any failure, and the restore is non-destructive.
- **SharePoint search-index lag after reset.** Mitigation: measure on Day 18, the drive-listing fallback, and staging files the evening before.



## Open items

- Auto-demote to A2 (proposed) vs A1 (Tech.MD). Confirm before Day 19.
- Who can approve a playbook: any technician, or a named role? Proposed for the demo: the presenter, recorded as `approved_by`. For the product: a per-MSP "playbook approver" list.
- Does the file-restore intent need a requester approver list? Today Sophie and Grace pass because there's no list, the same as `mfa.reset`. Confirm that this is right for a data restore.
- Whether to export approved versions as YAML for git review. Proposed: a "download YAML" link on the review screen; the store is the source of truth.

