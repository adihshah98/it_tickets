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
| 3 | No-playbook mode: runner flag, investigator, lint, gate rule 3, `Escalation` | Yes; #25, #26 pass | |

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

## Part 3: no-playbook mode

- **Runner:** `no_playbook = not any(library.has(i) for i in triage.intents)`, recorded on the triage/plan
  event and passed to investigator, lint and gate.
- **Investigator:** generic guideline in place of playbook YAML; bounds 15 reads / 60 s (existing
  `max_reads` budget). `Plan` gains `Escalation` fields: `diagnosis`, `evidence`, `checked`,
  `suggested_next_step`, `missing_action`.
- **Linter:** replace the forced escalation at `lint.py:91` with no-playbook mode: rule 3 checks scope
  against `NO_PLAYBOOK_ALLOWED`; rule 4 needs non-empty `expected` on every step plus a reply; skip 5-6.
- **Policy:** `NO_PLAYBOOK_ALLOWED` in `safety/policy.py` (`user.update` non-security fields,
  `group.addMember/removeMember`, `mailbox.autoReply`, `file.restoreVersion`, `link.sendOneTime`),
  pinned by a test.
- **Gate:** rule 3 before autonomy: off-list → `block` (`no_playbook.not_allowed`), on-list → `approve`
  (`no_playbook.approve`); A3 never autos it. Update the module docstring's rule order.
- **Escalation:** `escalate=True` → `ESCALATED` with `kind="no_playbook"` (investigator.py:377), carrying the
  fields; the W3 escalation write-up renders the diagnosis and missing action.
- **Tests:** #26 `tap.create` → block; #25 escalates naming the missing action; A3 can't auto a
  no-playbook step; a step without `expected` fails lint; `unknown` → no-playbook mode. Remove both xfails.

## Verification

- `uv run pytest` and `uv run ruff check backend` green: 24/24 in-scope cases + 5b, #23/24/27/28 still xfail
  for their stated reasons (`--runxfail` check), gate snapshot unchanged.
- No live run today (no new writes reach the tenant until Day 18's restore).
