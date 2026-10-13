# Week 4 Day 17: one playbook store, open intents, the no-playbook path

## Context

[Build-W4.md](../Weekly/Build-W4.md) Day 17. Today playbooks are files read by module functions in
`playbooks/loader.py` (`intents()`, `load_playbooks()`, `prompt_text()`), and `Intent` is a closed `Literal`
(`core/models/base.py:30`). A ticket with no playbook is forced to escalate (`lint.py:91`). Day 17 moves every
playbook into the store, opens up intents, and lets a no-playbook run either plan within a tight allow-list
(every write approved) or escalate with a diagnosis. Un-xfails eval cases **#25** and **#26**.

Each part ends with green tests and a commit, then a stop for review.

| Part | What | Behaviour change? | Status |
| ---- | ---- | ----------------- | ------ |
| 1 | Playbook store + `PlaybookLibrary`, callers switched | None (the proof is 22/22 unchanged) | **Done** |
| 2 | Open intents: `IntentSlug` + near-duplicate validator | Intake only | **Done** |
| 3 | No-playbook mode: runner flag, investigator, lint, gate rule 3, `Escalation` | Yes; #25, #26 pass | **Done** |

---

## Part 1 (done): the store, alone

**Files:**

- `core/store.py`: two tables.
  - `PlaybookVersion`: intent, version, yaml, content_hash, status, source, source_run_id, approved_by,
    created_at; unique on (intent, version).
  - `ClientAutonomy`: per-intent overrides (intent, level, set_by, updated_at).
- `playbooks/library.py`: `PlaybookLibrary(store, domain, facts)`.
  - `sync_shipped(directory)` imports each `playbooks/*.yaml`: v1 if the intent is new, the next version if
    the file's hash differs from its last *shipped* version. The previous approved version is retired. A
    broken file fails the sync (startup), not a run.
  - Reads: `current()`, `versions()`, `intents()`, `has()`, `get()`, `for_intents()`, `prompt_text()`.
  - `autonomy()` is `client_facts.autonomy` with the `ClientAutonomy` rows on top.
- `playbooks/loader.py`: `parse_playbook(text, intent)` and `client_facts_text()` split out for the library.
  The file-reading functions stay, for seeding and tests.
- `runner.py`: `Runtime.library` is built in `__post_init__` and synced, so every Runtime (API, CLI, tests,
  each eval case's fresh store) has one.
- Callers switched to the library: `run_intake` and `investigate` (new `library=` argument), `close_out`
  (`library=` replaces the now-unused `domain=`), and the gate.
- `safety/gate.py`: `evaluate`/`check` take a required `autonomy` mapping; the runner passes
  `rt.library.autonomy()`.
- Tests: `conftest.shipped_library()`; the direct gate calls pass `autonomy=facts.autonomy`;
  `tests/test_library.py` is new.

**Two differences from the plan above:**

- **Autonomy overrides are merged, not seeded.** `ClientAutonomy` holds only the levels set in the product,
  and `autonomy()` lays them over `client_facts.autonomy`. Seeding copies would go stale when the file
  changes, and Day 22's reset just deletes the rows.
- **The gate's `autonomy` argument is required**, with no fallback to `facts.autonomy`, so no caller can
  skip the overrides by accident.

**Tests** (`test_library.py`):

- the sync is idempotent;
- a changed file becomes v2 and v1 is retired;
- a broken file fails the sync;
- `for_intents`/`prompt_text` are equal to the loader's output for all three intents plus `unknown`, so the
  investigator, lint and gate get byte-identical input;
- the onboarding gate decisions are equal with the library and with the files;
- overrides sit on top of the client facts;
- a Runtime syncs its store.

Offboarding and MFA decisions are pinned per step by the eval cases.

**Result:** `uv run pytest`: 330 passed, 6 xfailed (#23-28). The eval harness is unchanged: 27 passed
(cases 1-22, 5b and the variants), 6 xfailed. `ruff` is clean. No live run is needed: no write path changed.

## Part 2 (done): open intents

**Files:**

- `core/models/base.py`: `IntentSlug`, a `str` matching `INTENT_RE`. It is two or more dot-separated
  lowercase words (underscores inside a word are allowed), or `unknown`. `Intent` stays as an alias.
  `Triage.intents` and `Plan.intent` use it.
- `agents/intake.py`:
  - the prompt lists `library.intents()` as the known intents. It asks intake to reuse one for the same
    job, however it is worded, and to name a new slug only for a job none of them covers;
  - `near_duplicate(slug, known)`: same first word, and the rest is equal without underscores, one is a
    prefix of the other, or the similarity is ≥ 0.85 (difflib). onboard/offboard is 0.8, so the two
    known intents stay apart;
  - `build_intake(model, known)` registers an output validator that sends near-duplicates back with
    `ModelRetry`, naming the known intent. Intake has 2 retries.

**Tests:**

- `test_models.py`: valid slugs are accepted (including `unknown`); malformed ones are rejected (no dot,
  capitals, `..`, hyphens, a trailing `_`, whitespace, empty).
- `test_agents.py`:
  - a table of near-duplicates: `file.restore`, `user.onboarding`, `user.off_board` and typos are caught;
    `user.rename`, `mfa.register` and `group.add_member` are not;
  - intake answering `user.onboarding` is sent back once and settles on `user.onboard`;
  - a new slug with no playbook (`file.restore_version`) is kept, with no retry.

**Result:** `uv run pytest`: 355 passed, 6 xfailed. The eval harness is unchanged (27 passed, 6 xfailed).
`ruff` is clean.

**Not yet checked live:** the new intake prompt hasn't been run against Haiku. Day 21's three Grace
phrasings are the live check for fragmentation. If intake can't settle after its retries, the run fails
at intake, the same as any intake model error today.

## Part 3 (done): no-playbook mode

**What "no playbook" means:** no triaged intent has an approved version in the library, so
`library.for_intents(triage.intents)` is empty. Lint and gate already receive that dict, so the flag isn't
passed separately anywhere. The investigator records it on the `plan` event (`no_playbook`).

**Files:**

- `safety/policy.py`: `NO_PLAYBOOK_ALLOWED` (the six actions) and `NO_PLAYBOOK_RISKS`
  (`write.reversible`, `none`). The risk check is what makes `user.update` "non-security fields only": a
  `user.update` that blocks sign-in is `access.revoke`, so it is out. `file.restoreVersion` is listed now;
  until Day 18 adds it to the catalogue, the catalogue check rejects it.
- `core/models/plan.py`: `diagnosis`, `evidence`, `checked`, `suggested_next_step`, `missing_action`, flat
  on `Plan` (the shape case #25 records), all optional.
- `safety/lint.py`, no-playbook mode (replaces the forced escalation):
  - rule 3: the action must be on the allow-list (the message says to escalate and name it in
    `missing_action`), and the step's catalogue risk must be in `NO_PLAYBOOK_RISKS`;
  - rule 4: every step needs a non-empty `expected`. "Plus a reply" (Tech.MD §4.3) is close-out's
    message, not a plan step, so lint doesn't ask for one. Day 18 makes `message_sent` part of the DoD;
  - rules 5-6 are skipped;
  - an escalation passes as before. The diagnosis fields are asked for, not required: case #22 (VPN
    reset) is a correct no-playbook escalation with only `needs_technician` and a reason.
- `safety/gate.py`, rule 3, after the catalogue/group blocks and in place of autonomy and the risk-class
  rules:
  - off the list, or above the allowed risk → `block` `no_playbook.not_allowed`;
  - otherwise → `approve` `no_playbook.approve`;
  - no autonomy level (A3 included) applies. The blast-radius and writes-per-run guards still do.
- `agents/investigator.py`:
  - `NO_PLAYBOOK_GUIDELINE` is added to the instructions: goal, objects, hypothesis; then plan using only
    the allow-list with `expected` on every step, or escalate with the five fields; credential, license,
    data-access, revoking and deleting changes are always escalated; anything ambiguous goes to
    `questions_for_requester` with no changes;
  - bounds are `min(max_reads, 15)` and `min(timeout_s, 60)`;
  - the escalation is `ESCALATED` with `kind="no_playbook"`, and the non-empty diagnosis fields on the
    state event.
- `cli.py` prints the diagnosis, the suggested next step and the missing action under `== ESCALATED`.
- Web: no change today. The workspace's escalation write-up (`Outcome` in `Closeout.tsx`) already renders
  extra fields on the state event as key/values. The dedicated diagnosis panel is Day 21's.

**Tests:**

- `test_gate.py`:
  - the allow-list is pinned;
  - allowed steps → `approve`;
  - `tap.create` → `block`;
  - `user.update` disabling an account → `block` (access.revoke);
  - A3 still → `approve`;
  - the IT Admins group rule still blocks.
- `test_lint.py` (replaces "no playbook means no steps"):
  - an allowed step with `expected` passes;
  - a step without `expected` fails;
  - `tap.create` is sent back, mentioning `missing_action`;
  - disabling an account is sent back;
  - a playbook's must-investigate reads aren't required.
- `test_agents.py`:
  - an `unknown` ticket gets the guideline and the 15-read budget, and no playbook YAML;
  - the plan event says `no_playbook`;
  - the escalation is `no_playbook` with the fields;
  - a playbook run gets neither the guideline nor `no_playbook=True`.
- Eval cases **#25** and **#26** are un-xfailed. `test_cases_cover_tech_md_section_11` now tracks the
  landed Week 4 cases (`WEEK_4_LANDED`).

**Result:**

- `uv run pytest`: 369 passed, 4 xfailed (#23, #24, #27, #28).
- `--runxfail` shows each still fails for its stated reason:
  - #23: `file.restoreVersion` isn't a step type yet;
  - #24: FakeTenant has no drive versions;
  - #27 and #28: the harness can't seed learned playbooks or check the library until Day 19.
- `ruff` is clean.
- No live run: the no-playbook prompt hasn't been run against Opus yet. Day 18's Sophie run is its
  first live use.

## Verification

- `uv run pytest` and `uv run ruff check backend` green: cases 1-22, 5b, #25 and #26 pass, and #23/24/27/28
  still xfail for their stated reasons (`--runxfail` check). The gate decisions for the three shipped
  intents are unchanged. **Met** (369 passed, 4 xfailed).
- No live run today (no new writes reach the tenant until Day 18's restore).
