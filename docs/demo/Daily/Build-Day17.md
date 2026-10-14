# Week 4 Day 17: one playbook store, open intents, the no-playbook path

## Context

[Build-W4.md](../Weekly/Build-W4.md) Day 17. Today playbooks are files read by module functions in
`playbooks/loader.py` (`intents()`, `load_playbooks()`, `prompt_text()`), and `Intent` is a closed `Literal`
(`core/models/base.py:30`). A ticket with no playbook is forced to escalate (`lint.py:91`). Day 17 moves every
playbook into the store, opens up intents, and lets a no-playbook run either plan within a tight allow-list
(every write approved) or escalate with a diagnosis. Un-xfails eval cases **#25** and **#26**.

Each part ends with green tests and a commit, then a stop for review.


| Part | What                                                                         | Behaviour change?                   |
| ---- | ---------------------------------------------------------------------------- | ----------------------------------- |
| 1    | Playbook store + `PlaybookLibrary`, callers switched                         | None (the proof is 22/22 unchanged) |
| 2    | Open intents: `IntentSlug` + near-duplicate validator                        | Intake only                         |
| 3    | No-playbook mode: runner flag, investigator, lint, gate rule 3, `Escalation` | Yes; #25, #26 pass                  |


---



## Part 1: the store, alone

- `core/store.py`: `PlaybookVersion` (intent, version, yaml, status, source, source_run_id, approved_by,
created_at, content hash) and `ClientAutonomy` (intent → level), seeded from `client_facts.autonomy`.
- `playbooks/library.py`: `PlaybookLibrary(store, domain)` with `sync_shipped()` (import `playbooks/*.yaml`
as approved `shipped` if absent or hash changed), `has()`, `intents()`, `get()`, `for_intents()`,
`prompt_text()`, `autonomy()`. Parsing/validation stays in `loader.py` (reuse `Playbook`, `_text`).
- `Runtime` gains `library`; `Runtime.from_settings` builds it and calls `sync_shipped()`.
- Switch the product callers to `rt.library`: `agents/intake.py` (intents list), `agents/investigator.py:341-345`,
`closeout.py:92`, and `gate.autonomy_level` (reads `library.autonomy()` instead of `facts.autonomy`).
Lint/gate/dod already take a `playbooks` dict, so they don't change.
- Tests keep the loader functions; `conftest.py` and `eval_harness.py` build a library from the shipped files
on each case's fresh store.
- **Gate:** eval harness 22/22 unchanged; a snapshot test pins the onboarding/offboarding/MFA gate decisions
byte-for-byte; `sync_shipped` is idempotent and versions a changed file as v2.



## Part 2: open intents

- `base.py`: `IntentSlug` = constrained `str` (`^[a-z]+(\.[a-z_]+)+$` or `unknown`); `Intent` kept as alias.
- `Triage.intents: list[IntentSlug]`; the intake prompt lists `library.intents()` and asks to reuse one.
- Output validator rejects a near-duplicate of a known intent (e.g. `file.restore` vs `file.restore_version`)
with a retry message naming the known one.
- **Tests:** a new slug is accepted; a near-duplicate is rejected; `unknown` still works.



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

