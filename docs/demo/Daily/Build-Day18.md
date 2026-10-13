# Week 4 Day 18: `file.restoreVersion` and Sophie live

## Context

[Build-W4.md](../Weekly/Build-W4.md) Day 18. Day 17 made the no-playbook path real, but it has never executed a
write: `file.restoreVersion` is on `NO_PLAYBOOK_ALLOWED` but not in the catalogue, FakeTenant has no drives,
and close-out does nothing for a run with no playbook (`dod_playbook({})` is `None`, so no probes run and
nothing checks that a reply went out). Day 18 adds the action end to end, gives no-playbook runs a
close-out, and runs Sophie's ticket live. Un-xfails eval cases **#23** and **#24**.

Each part ends with green tests and a commit, then a stop for review.

| Part | What | Behaviour change? | Status |
| ---- | ---- | ----------------- | ------ |
| 0 | Live spike: search lag and what a restore does to the file (read-mostly) | None | **Done** |
| 1 | `file.restoreVersion` four-touch + FakeTenant drives + file-ID grounding; #24 | New action; shipped intents unchanged | **Done** |
| 2 | No-playbook close-out: DoD, reply note, `can_save_playbook`; #23 | Close-out for no-playbook runs only | **Done** |
| 3 | Live: reset, Sophie via the CLI, `test_live_file_restore.py` | Three fixes found live (below) | **Done** |

**Needs your approval:** Part 0's one test restore on the tenant, and all of Part 3 (tenant reset, live model
runs). Both are flagged where they come up.

---

## What I found reading the code

These need a decision before Part 1. Each has a recommendation.

1. **File IDs aren't GUIDs, so the linter can't ground them.** Drive IDs look like `b!x7Z…`, item IDs like
   `01ABCD…`, and SharePoint version IDs are `1.0`, `2.0`, `3.0`. Grounding (`lint.py:124`) only checks
   strings that match `GUID_RE`, and `ObjectId` would reject these values outright. The harness hides this
   because it seeds the file IDs with `gid()`.
   **Recommendation:** add `DriveId`, `DriveItemId` and `VersionId` string types with loose patterns. Have
   `Transcript` record the files it sees: (drive, item) pairs from `files.search`, and the version list per
   item from `files.versions`. Add a lint check for `file.restoreVersion`: the (driveId, itemId) pair was
   returned by a search, the versionId was listed for *that* item, and it isn't the newest (current)
   version. Switch the harness seed to real-looking IDs so the eval cases exercise this check.

2. **"Verify: the newest version matches the chosen one (size + modified)" can't work as written.** A restore
   creates a *new* current version stamped with the time of the restore, so its modified time never matches
   the old one.
   **Recommendation:** verify passes if a version appears that wasn't in the snapshot's version list, and its
   size equals the chosen version's size. It reports `{"currentMatches": versionId | None, "newVersionId",
   "size"}`, so case #23's `expected: {currentMatches: …}` compares cleanly. **Settled by Part 0:** a restore
   is byte-identical to the chosen version and shows up in about 2 s, so size works. The
   `version_restored` probe also compares content hashes.

3. **`message_sent` always requires a one-time link** (`dod.py:208`). A file restore sends no link, so a
   no-playbook run would always fail this probe.
   **Recommendation:** require the link only when the DoD item says `via: one_time_link`. All three shipped
   playbooks already say that, so their behaviour doesn't change, and a test pins it.

4. **No-playbook DoD.** Build-W4 says it is "the step verifies plus `message_sent`". The executor already
   refuses to reach CLOSING unless every step verified, so the DoD only needs what verify can't see.
   **Recommendation:** add `closeout.no_playbook_dod(plan)`, built in code: one probe per action that has one
   (`file.restoreVersion` → `version_restored`, re-read after close-out), then
   `message_sent: {to: $requester}`. Allowed actions with no probe rely on their step verify alone. As
   today, the reply is held while any probe fails.

5. **The reply line "restoring creates a new version, nothing is lost".** With no playbook, the close-out
   prompt gets no gotchas (`agents/closeout.py:89`).
   **Recommendation:** add a small `REPLY_NOTES` map, keyed by action, to the close-out context
   (`file.restoreVersion` → that sentence). Offline tests can check that the note is in the context, not the
   model's wording; the Part 3 live run checks the actual reply.

6. **`can_save_playbook`.** For today, it is true when the run is DONE, its `plan` event has
   `no_playbook=True`, and at least one step verified (not skipped). Day 19/20 widen it to escalated, A1
   and technician runs. It is a snapshot field only; there is no web change today.

7. **Live ticket wording vs. staging.** Running `--files base` then `--files overwrite` on the same day makes
   all three versions *today's*, so Sophie's "the version from yesterday afternoon" doesn't exist. The
   ambiguity rule should then send her a question rather than restore anything, which is correct behaviour
   but isn't the demo.
   **Recommendation:** today's live test uses "…can you get back the version from before I saved over it
   this morning?", which is unambiguous with same-day versions (it means the version just before the
   newest). The exact demo wording is checked at Day 22's rehearsal, with `--files base` run the evening
   before. Optional: also run Grace's #24 ticket live, to see whether Opus asks the question (offline,
   #24 only checks the shape of the result).

8. **Case #27 uses `Staffing Plan.xlsx`, which the tenant seed doesn't have.** Day 21's hands-free Grace run
   needs it live.
   **Recommendation:** add it to `seed.FILES` today (two versions, plus an overwrite) and to FakeTenant, since
   reset's file staging is already being touched. This costs one more upload per reset.

9. **Search-index lag.** `files.search` uses `/root/search`, which can miss a file uploaded minutes ago.
   **Recommendation:** build the fallback whatever Part 0 shows. If search returns nothing for a drive, list
   the drive root's children and match on name. It is cheap and removes a demo-morning risk.

10. **Idempotency and rollback.**
    - `existing()` returns the earlier output only on a resumed step, when a version newer than the step's
      start already matches the chosen version's size. Otherwise it returns `None`: a second restore is
      harmless (just another version), so there is no Deviation.
    - The snapshot records the current version's ID and the list of version IDs.
    - `compensate` is "restoreVersion to the snapshot's current version".

---

## Part 0 (done): live spike

Run on 2026-10-13 against the tenant as staged by the last `reset.py` (all three versions uploaded at
17:21 UTC, about 2h15m earlier). The scripts were throwaway and were not committed.

**IDs as Graph returns them:**

- drive: `b!H-i7YY5pVEqXQkPXRsnUJLuxRunmPO1KtzAS0DsA2RAXToSyhvzRTppnO_EhlcHb`
- item: `01A7DRABNZ5SZVNFRH35DZITOYEXVFNOJT` (34 uppercase alphanumerics)
- versions: `1.0`, `2.0`, `3.0`, newest first. The newest is the current version.

This confirms decision 1: none of these are GUIDs.

**Search:**

- `root/search` finds both files by full name, and "Parking" finds Q3, in 1-4 s.
- "Staffing Plan" finds nothing, as expected (not seeded yet).
- `files.search` scans 6 group sites (Viva Engage, All Company, Northwind Parking, Accounting, Northwind
  Operations, Field Operations), and all six have a drive.
- **Not yet measured:** lag right after a fresh upload. These files were 2 hours old. Part 3 measures it
  straight after the reset. The fallback (decision 9) is built either way.

**Versions:**

- SharePoint rewrites the uploaded xlsx: a 5,010-byte upload is stored as 12,712 bytes. The stored versions
  still all have different sizes (12,712 / 12,755 / 12,606 for Q3, and 12,666 / 12,708 / 12,578 for
  Lot Permits).
- `lastModifiedBy` is "SharePoint App" on every version, so "who saved it" doesn't tell Sophie's save apart
  from anyone else's. Only the times and the ticket's wording do.
- `GET …/versions/{current}/content` returns **400**. The current version's bytes only come from
  `GET …/items/{i}/content`. Older versions download fine.

**One restore** (`Lot Permits Tracker.xlsx` → `2.0`):

- `POST …/versions/2.0/restoreVersion` → **204**, empty body, 0.9 s.
- 2 s later the versions list shows a new **`4.0`**: size 12,708 (equal to `2.0`), modified at the time of
  the restore. The item's `lastModifiedBy` is "Microsoft Graph".
- The current content's SHA-256 is **byte-identical** to `2.0`'s (`b72c821b840e…`). There is no rewrite
  on restore and no lag.

**What this settles:**

- **Decision 2: verify on size works.**
  - Verify passes when a version not in the snapshot appears and its size equals the chosen version's.
  - The `version_restored` probe adds a content comparison: SHA-256 of `items/{i}/content` vs.
    `versions/{v}/content`, both under 13 KB.
  - The "modified" half of Build-W4's verify is dropped, because a restore always stamps a new time.
- **Decision 1:** `VersionId` is `^\d+\.\d+$` (SharePoint). OneDrive personal's version IDs are out of
  scope.
- **The handler must not download the current version through `versions/{id}/content`.** That call
  returns 400.
- **The tenant is now off its staged state.** Lot Permits Tracker has 4 versions, so `--files overwrite` on
  its own would skip it. Part 3 starts with `--files base`, which deletes and re-uploads the file.

## Part 1 (done): the action, FakeTenant, grounding

**Product code:**

- `core/models/base.py`: `DriveId`, `DriveItemId` and `VersionId` (loose string patterns).
- `core/models/params.py`: `FileRestoreVersionParams(driveId, itemId, versionId, fileName?)`, with
  snake_case aliases as the other params have. `fileName` is for the approval card only.
- `core/models/plan.py`: `FileRestoreVersionStep`, added to `Step` and `STEP_TYPES`.
- `execution/catalogue.py`: an `ActionSpec` for `file.restoreVersion`: `write.reversible`, `POST
  /drives/{d}/items/{i}/versions/{v}/restoreVersion`, verify and compensate as in decisions 2 and 10.
- `graph/writes.py`: a `FileRestoreVersion` handler (existing/snapshot/act/verify), plus the
  `file_versions` and `find_version` helpers that the handler and Part 2's probe share.
- `graph/reads.py`:
  - `Transcript` records file hits and version lists (decision 1);
  - the `files.search` fallback to root children (decision 9).
- `safety/lint.py`: the file grounding check (decision 1).
- `tenant/seed.py`: `Staffing Plan.xlsx` in `FILES` (decision 8). Reset needs no change, because it loops
  over `FILES`.

**Test code:**

- `tests/fake_tenant.py`:
  - `add_drive(group_id)` and `add_file(drive, name, versions=[(time, size)])`;
  - routes for `GET /groups?$filter=…Unified`, `GET /groups/{id}/drive` (404 for groups with no site),
    `root/search(q=…)`, `root/children`, `items/{i}/versions`, and `POST …/restoreVersion`, which appends
    a copy of the chosen version as the new current version;
  - a `search_lag` counter, like `stale_member_reads`, to exercise the fallback.
- `tests/eval_harness.py` `seed()`: the Northwind Operations drive, with Q3 Parking Rates (v1 and v2
  yesterday afternoon, v3 this morning) and Staffing Plan, using realistic IDs.

**Tests:**

- `test_catalogue.py`: the spec exists, it is `write.reversible`, and the params round-trip.
- `test_executor.py`, against FakeTenant:
  - a restore verifies, and `currentMatches` equals the chosen version;
  - the restore appends a version and doesn't replace one;
  - a resumed step is idempotent;
  - verify fails if no new version appears.
- `test_lint.py`:
  - an unobserved item ID → rejected;
  - a version listed for a different item → rejected;
  - the current version → rejected;
  - grounded IDs → pass.
- `test_reads.py`: search hits; the fallback finds the file when search is empty; groups with no site are
  skipped.
- `test_gate.py`: `file.restoreVersion` with no playbook → `approve` (`no_playbook.approve`), including at A3.

**As built:**

- **Handler:**
  - `snapshot` raises a Deviation if the chosen version isn't in the history or is the current one, so
    nothing is written;
  - `act` records `previousVersionId` (what compensation restores) and `versionsBefore`;
  - `verify` reports `{currentMatches, currentVersionId, size}`;
  - `existing` returns a result only on a resumed step that already restored. It verifies against the
    `step_started` snapshot.
- **The `files.search` fallback** lists the library's top folder and keeps files (not folders) whose name
  contains every word of the query. It runs per library, only when that library's search returns nothing.
- **Transcript:** `Transcript.files` maps (drive_id, item_id) to the version IDs listed for that file, or
  `None` if only search has seen it. IDs are case-sensitive: they're base64, unlike GUIDs.
- **Harness seed:**
  - Q3 Parking Rates is `1.0`/`2.0` yesterday afternoon and `3.0` at 09:05 today;
  - Staffing Plan is `1.0` yesterday and `2.0` at 10:00 today (#27);
  - the `${q3_v1}`… and `${staffing_v1}`… placeholders now resolve to version IDs;
  - a case can replace a file's history with `setup.file_versions`.
- **`test_catalogue`'s "no dead actions" check** now counts `NO_PLAYBOOK_ALLOWED`:
  `file.restoreVersion` isn't in any playbook until one is learned.

**Two differences from the plan:**

- **#24 un-xfailed here, not in Part 2.** It only needed FakeTenant's versions. With
  `setup.file_versions` (09:05, 11:40, 14:10 today) and `expect: {state: ESCALATED, escalation: {kind:
  questions}}`, it passes. Offline it checks the shape (a question, zero writes, waiting on the requester),
  not whether Opus asks. That's Part 3's optional live run.
- **#23 passed as soon as the action existed (XPASS),** because a no-playbook run has no DoD and went
  straight to DONE. Its `expect` now has `dod: {ok: true, probes: [version_restored, message_sent]}`, so
  it stays xfail until Part 2. With `--runxfail` it fails only on "no probes ran". Lint (grounded file
  IDs), the gate (`no_playbook.approve`) and execution (the restore verified) all pass.

**Result:**

- `uv run pytest`: 386 passed, 3 xfailed (#23, #27, #28).
- `uv run ruff check backend` is clean.
- `tenant/seed.py`'s ruff warnings are the 17 it had before (line length); none are new.
- No live run: Part 3 does that.

## Part 2 (done): no-playbook close-out, #23

**Product code:**

- `execution/dod.py`:
  - the `version_restored` probe: for each restore step, the newest version's size equals the chosen
    version's, read with `settled()`;
  - `message_sent` requires a link only for `via: one_time_link` (decision 3).
- `closeout.py`: `no_playbook_dod(plan)`, used when `dod_playbook` returns `None` and the plan has steps
  (decision 4). The PSA note and time entry are written as today.
- `agents/closeout.py`: `REPLY_NOTES` in the context (decision 5).
- `api.py`: `can_save_playbook` in the snapshot (decision 6).
- `cli.py`: prints `can save as playbook` under `== DONE` when the flag is true.

**Test code:**

- `test_dod.py`:
  - `version_restored` passes after a restore and fails without one;
  - `message_sent` without `via` passes with only a reply;
  - the three shipped playbooks still require the link (pinned).
- `test_closeout.py`:
  - a no-playbook run gets `version_restored` + `message_sent` done checks;
  - the reply is held if `version_restored` fails;
  - the PSA note and time entry are written;
  - the context carries the restore note.
- `test_api.py`: `can_save_playbook` is true for #23's run, and false for a playbook run and for an
  escalated one.
- `evals/cases.yaml`:
  - remove the xfail from **#23** (its `dod` expectation is already in, from Part 1);
  - add 23 to `WEEK_4_LANDED`.
- `lint.DOD_PRODUCERS`: `version_restored` ← `file.restoreVersion`. `message_sent` needs a
  `link.sendOneTime` step only when its args say `via: one_time_link`. This matters for a learned
  restore playbook (Day 19).

**Gate:** `uv run pytest` green: 386 tests plus Part 2's new ones, with #27 and #28 the only xfails. The
three shipped intents' gate decisions are unchanged. `ruff` is clean.

**As built:**

- **`version_restored`** downloads the chosen version (`versions/{v}/content`) and the current file
  (`items/{i}/content`, since the current version's own URL is a 400) and compares their SHA-256. It
  re-reads with `settled()` before failing. The detail reads "Q3 Parking Rates.xlsx is version 2.0 again
  (now 4.0)".
- **`GraphClient.content(path)`** is new: a bytes GET that follows Graph's redirect to the download URL.
  httpx drops the Authorization header when it changes host. It doesn't retry, because only probes use it,
  and a probe turns a Graph error into a failed check.
- **No-playbook DoD:** `closeout.no_playbook_dod(plan)` is a `Playbook` built in code. Its DoD is one probe
  per action in `dod.NO_PLAYBOOK_PROBES` (today `file.restoreVersion` → `version_restored`), then
  `message_sent: {to: $requester}`. It is used when no triaged intent has a playbook and the plan has
  steps.
- **`message_sent`** checks the link only for `via: one_time_link`. Onboarding and MFA say that, and
  offboarding has no `message_sent`. The detail is now "reply to …" when no link is required. The
  linter's rule 4 follows the same rule.
- **`REPLY_NOTES`** (`agents/closeout.py`) adds a `notes` list to the close-out context for each action
  done, and the instructions say to put each note in plain words. The restore note is "creates a new
  version; nothing is lost; the overwritten version is still in the history".
- **`runner.can_save_playbook(store, run_id)`** is used by the API snapshot (`can_save_playbook`) and by
  the CLI. After an approval, the CLI prints "no playbook for this ticket type: this run can be saved as
  one".
- **Tests:**
  - conftest has a `restore_gated` fixture: Sophie's no-playbook restore, through the real gate, awaiting
    approval;
  - FakeTenant serves version content and gives the current version's `versions/{v}/content` a 400, as
    Graph does.

**Result:**

- `uv run pytest`: 398 passed, 2 xfailed. #27 and #28 still need the learned-playbook store (`--runxfail`
  shows the Day 19 `NotImplementedError`s).
- `ruff check backend` is clean.
- The shipped intents' gate decisions and DoDs are unchanged. The "reply and one-time link" detail is the
  same for onboarding and MFA, and a test pins that both still require the link.
- **Not tested offline:** the CLI line, because `continue_run` needs real settings. It gets checked in
  Part 3.

## Part 3: live (needs approval: tenant writes and model spend)

**What happened (2026-10-13):**

- **Reset done.** `--files base` (20:58) then `--files overwrite` (20:59). All three files have
  `1.0`/`2.0`/`3.0`, including `Staffing Plan.xlsx`, which was uploaded for the first time. Stored sizes:
  Q3 12,717 / 12,760 / 12,608. Reset also warned that Dennis has no Authenticator; that is the MFA demo's
  concern, not today's.
- **Search lag is not a problem.** Polled `root/search` from about 45 s after the overwrite: Q3 and
  Lot Permits were found on the first poll, and Staffing Plan (a brand-new file created at 20:59:07)
  6 s later, about 80 s after creation. The search hit's `lastModifiedDateTime` showed `2.0`'s time
  rather than the overwrite's, so search metadata trails. The model reads times from `files.versions`
  anyway.
- **Sophie's run 19 crashed in the investigation.**
  - SharePoint throttled `root/search` (429) with `Retry-After: 30,120`, two values. Probably my lag
    polling plus the run's 6-site fan-out.
  - `GraphClient.request` does `float(resp.headers["Retry-After"])` and raised `ValueError`, which killed
    the CLI process.
  - Run 19 is left in `INVESTIGATING`. Nothing was written to the tenant.
  - Throttling had cleared a minute later (search → 200).
- **Intake triaged the ticket as `unknown`,** not a new slug such as `file.restore_version`. Both
  are no-playbook, so today is unaffected. But "Save as playbook" (Day 19) needs a real intent to save
  under, and #23 records `file.restore_version`.

**Decisions taken:**

- **`Retry-After`:** use its first number, and fall back to backoff if it isn't a number
  (`client.retry_after`, tests for `"30,120"`, `"30, 120"`, `"soon"` and `""`).
- **Intake's `unknown`:** left for Day 19. "Save as playbook" proposes the slug (the plan's intent was
  `file.restore_version`) and the technician confirms it.

**Run 20 (CLI, after the fix): DONE.**

- Intake said `unknown`. The investigator named the plan's intent `file.restore_version` itself.
- Reads: users.search → files.search (1 hit; the index had caught up) → files.versions → users.memberOf.
  Lint passed on the first attempt, in 30 s.
- Plan: restore `2.0`, one step, gated `approve` (`no_playbook.approve`). It put its doubt in
  `confirm_on_approval`: all versions are by "SharePoint App", and 20:59 UTC isn't "this morning".
  Its `expected` keys (`latestVersionSize`, `versionCount`) aren't ones verify reports, so they were
  unchecked. The handler's own check decides.
- After approval: the restore verified in 1.1 s; `version_restored` ✔ ("Q3 Parking Rates.xlsx is version
  2.0 again (now 4.0)"); the reply was sent with the "nothing is lost" note; `message_sent` ✔; a 1-minute
  time entry; the CLI printed the save-as-playbook line.
- **SharePoint:** `4.0` is 12,760 bytes, SHA-256 `d3033bedf913…`, identical to `2.0`. `3.0` (the overwrite)
  is still in the history.
- **Defect: the PSA note was blocked `tap_like`.** Sonnet copied the drive ID (`b!H-i7…`) from the context
  into it. Fixed by keeping file IDs out of the close-out context (`CONTEXT_EXCLUDED`: tap, driveId,
  itemId), plus a test that the context scans clean.

**`test_live_file_restore.py`:** two attempts after re-staging, neither reached approval. Nothing was
written.

1. "…saved over it this morning…" → `ESCALATED` (questions). All versions were staged at 21:12 UTC, which
   isn't morning in the US, so Opus asked Sophie which version she meant. The test wording was changed
   to drop the time of day.
2. No time of day → `ESCALATED` (timeout at 60 s). The findings say "all saved by SharePoint App
   within 40 s today, can't tell which save was Sophie's".

The cause is the staged data, not the code: each version is a few seconds apart and authored by the app,
which a careful investigator rightly finds suspicious. It's also near the 60 s no-playbook bound (run 20
took 30 s). (Attempt 2 was a re-run I made only to read the failure; I should have captured the first
run's output. That cost one extra Opus run.)

**Decision: prefer confirm-on-approval.** The no-playbook guideline's ambiguity rule now says: when the
evidence points to one likely answer, plan it and put the doubt in `confirm_on_approval`, because a person
approves every no-playbook change anyway. Ask the requester, and plan nothing, only when no answer is more
likely than another. That is what run 20 did. A test pins the two branches. The timeout stays at 60 s.

**Attempt 3, after the change: passed** (37 s end to end).

- Restore planned and gated `approve`; one approval; DONE.
- `version_restored` ✔ and `message_sent` ✔.
- **Both** the reply and the PSA note were sent, so the file-ID fix holds live.
- SharePoint has a new version identical to the chosen one.

**Three fixes from the live runs, each with tests:**

1. `GraphClient` uses the first number of a multi-value `Retry-After` (crashed run 19).
2. The close-out context leaves out `driveId`/`itemId` (the PSA note was blocked as TAP-like).
3. The no-playbook guideline sends a doubt that has a likely answer to the approver, not back to the
   requester (one question and one timeout in three runs).

**Left over:**

- Run 19 is still `INVESTIGATING` in `data/tech.db` (the crash). The CLI has no crash guard; the API does.
  It's harmless, and Day 22's reset clears runs.
- **Grace's #24 hasn't been run live since the guideline change.** "The earlier version from today" with
  several same-day versions should still be a tie, and so a question. Worth a live run (with a re-stage)
  before Day 21's Grace phrasings.
- The investigator writes `expected` keys that verify doesn't report (`latestVersionSize`). They're
  unchecked, not wrong. If it matters, the params description can name `currentMatches`.
- **For the demo (Day 22):**
  - run `--files base` the evening before, so "yesterday afternoon" and "this morning" match the
    history;
  - every version will still be authored by "SharePoint App". Run 20 showed that ends up on the
    approval card, which is fine to show.

1. `uv run tenant/reset.py --files base`, then `--files overwrite`. This deletes and re-uploads the
   team-site files (now three of them).
2. Check `files.search` finds all three files. If not, confirm the fallback finds them.
3. Run Sophie's ticket through the CLI (decision 7's wording) and approve. Expect one approval, the restore
   verified, `version_restored` ✔, `message_sent` ✔, and the reply mentioning that nothing is lost.
4. Confirm in SharePoint's version history that the current version has `2.0`'s content.
5. `backend/tests/test_live_file_restore.py` (`-m live`): steps 3-4 as a test that asserts the deterministic
   parts (intent, the gate's `approve`, DONE, the probes), not the plan text.
6. Optional: Grace's #24 ticket live, which should end with a question and zero writes.

Cost: one Opus investigation and one Sonnet close-out per run, about the same as an MFA run. Results are
recorded in this doc.

## Verification

**Result:** met.

- `uv run pytest`: 404 passed, 2 xfailed (#27, #28 → Day 19).
- `ruff check backend` is clean.
- Live: run 20 via the CLI reached DONE, with SharePoint showing `4.0` identical to `2.0`.
  `test_live_file_restore.py` passed once (attempt 3).

- `uv run pytest` and `uv run ruff check backend` are green. #23 and #24 pass; #27 and #28 still xfail for
  their stated reasons.
- The onboarding, offboarding and MFA gate decisions and DoD probes are unchanged.
- Live: Sophie's restore reaches DONE with one approval, and SharePoint shows the restored version.
  `test_live_file_restore.py` passes once.
