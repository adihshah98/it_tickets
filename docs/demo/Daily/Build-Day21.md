# Week 4 Day 21: the learning loop on screen, and Grace's hands-free run

## Context

[Build-W4.md](../Weekly/Build-W4.md) Day 21. Days 19-20 built the learning loop in the backend, but you can
only reach it through the API and CLI. Today puts it in the web app and proves it live:

1. **Workspace** (`/runs/[id]`):
   - the no-playbook banner;
   - the escalation diagnosis;
   - **"Do it myself"**;
   - **"Save as playbook"**;
   - **"I fixed it in the admin center"**.
2. **`/playbooks/[intent]` review:**
   - the editable YAML side by side with its source;
   - check results inline;
   - an autonomy picker that respects the ceiling;
   - Approve;
   - the version history.
3. **`/playbooks/new`:** a written playbook goes through the same review. This is learning case **L3**.
4. **`/playbooks` library:** shipped and learned together, with the "learned from" link and one-click
   demote.
5. **The live loop:**
   1. Sophie → Save as playbook → edit one line → A3 → Grace closes with **zero approvals**.
   2. Demote → Grace again → needs approval.
   3. A console run on a no-playbook ticket (**L4**).

Each part ends with green tests and a commit, then a stop for review.

| Part | What | Behaviour change? |
| ---- | ---- | ----------------- |
| 1 | Backend: snapshot fields, `GET /catalogue`, `POST /playbooks/{intent}/check`, names on console steps, `tech library reset`, L3/L4 tests | New fields and routes; console steps gain names |
| 2 | Workspace display: banner, diagnosis, learned badge | Display only |
| 3 | Workspace: "Do it myself" and Close | New UI over existing routes |
| 4 | Workspace: Save as playbook, and "I fixed it in the admin center" | New UI over existing routes |
| 5 | `/playbooks/[intent]` review | New route |
| 6 | `/playbooks/new` and the `/playbooks` library | New routes |
| 7 | Live: the loop in the browser | **Needs your approval** |

**Needs your approval:**

- **Part 7:** tenant writes, including file restores and a group add, all reverted. Each ticket costs a
  Sonnet triage, an Opus investigation and a Sonnet close-out, and each draft is one Opus call: about 6
  tickets and 3 drafts. Before it starts, it also clears the learned playbooks in `data/tech.db`
  (decision 15).

---

## What I found reading the code

Each needs a decision before Part 1. Each has a recommendation.

### Workspace

1. **The snapshot doesn't say whether a run had no playbook.** The flag is on the `plan` event
   (`no_playbook`), but the snapshot drops it. A run that escalated before it had a plan (run 21 hit the
   60 s timeout) has no `plan` event at all.
   **Recommendation:**
   - Add `no_playbook: bool` to the snapshot, taken from the `plan` event when there is one.
   - With no plan, fall back to `library.for_intents(triage.intents)` being empty *now*. The catch: once
     a playbook is saved for that intent, an old planless run stops showing the banner. That only affects
     a run that's already escalated.
   - The exact alternative is a key on the `INVESTIGATING` state event. It changes every run's event
     payloads, which Day 20 avoided.

2. **The diagnosis is already in the snapshot, shown as raw key/values.** A no-playbook escalation puts
   `diagnosis`, `evidence`, `checked`, `suggested_next_step` and `missing_action` on the ESCALATED state
   event (`investigator.py:414`), so they arrive in `outcome`. `Outcome` dumps whatever it doesn't know
   through `KeyValues`.
   **Recommendation:** an `EscalationDiagnosis` component above `Outcome`, shown when `outcome.diagnosis`
   is set. It reads:
   - **Diagnosis**, then **Evidence** and **Checked and ruled out**;
   - **Suggested next step**;
   - **Not in the catalogue: …**, when `missing_action` is set.

   `Outcome` stops dumping those five keys.

3. **There's no catalogue route.** The console's action picker and `/playbooks/new`'s allowed-actions
   checklist both need one.
   **Recommendation:** `GET /catalogue` returns every action with `name`, `risk_class`, `verify`,
   `compensate`, `params` (the Pydantic JSON schema, by alias) and `console: bool`.
   - `console` is false for what the gate always blocks for a technician (`gate.TECHNICIAN_BLOCKED`:
     `tap.create`, `authMethod.delete`, `license.purchase`, `mailbox.forwardRule`, `group.addOwner`), and
     for the services `link.sendOneTime` and `followup.schedule`.
   - The picker shows only console actions, and the checklist shows all of them.
   - The gate still decides; this only keeps the picker honest.

4. **The param form.** The console actions have 2-6 flat params.
   **Recommendation:** a form built from the JSON schema, in a pure `lib/console.ts` that vitest can test:
   - a string becomes a text box, a boolean a checkbox, an enum a select, and a string list a
     comma-separated box;
   - **ID fields** (`userId`, `groupId`, `driveId`, `itemId`, `versionId`) become a select of
     `GET /runs/{id}/observed`, filtered by kind, plus "type an ID…". A typed GUID gets Day 20's
     confirming read;
   - empty optional fields aren't sent.

   No form library (no new runtime dependencies).

5. **Names on console steps** (a Day 20 follow-up). Run 21's reply said "A user was added to the
   requested group".
   **Recommendation:** in `technician_step`, after grounding, fill any empty name param the action has
   (`groupName` today) from `transcript.objects`. The step list, reply and PSA note then name the group.
   This is a small backend product change.

6. **Live updates.** ESCALATED is terminal, so the event stream sends `end` and closes.
   - **Manual step:** awaited inline. The panel shows "running…", then `refresh()`.
   - **Close and audit draft:** these run as background tasks, so call `reopen()` after the 202. The stream
     stays open while the run is busy.
   - **`useRun`:** add `audit_observed` to `EVENT_TYPES`, and `audit_observed`, `playbook_drafted`,
     `playbook_approved` and `playbook_demoted` to `REFRESH_ON`.
   - **Console steps** (`t1`, `t2`, …) render from the snapshot's `technician_steps`, so `reduce()` is
     unchanged.

7. **Who the technician is.** There's no login, and `APPROVERS` are client staff.
   **Recommendation:** add `TECHNICIANS` to `samples.ts`, with one demo name (your call; e.g. `aditya`). It
   is used for "Working as" in the console, for approving a playbook, and for demote. The API adds the
   tenant domain.

8. **The learned badge** (Build-W4's "Grace runs hands-free with the badge"). The `plan` event records
   `playbook_versions`, but the snapshot doesn't show which playbook a run used, or whether that playbook
   was learned.
   **Recommendation:**
   - The snapshot gains `playbooks_used: [{intent, version, source, source_run_id}]`, looked up from the
     library's version rows.
   - The workspace header shows a badge for each non-shipped one: "Learned playbook · from run #20",
     linking to the playbook. When a learned run reaches DONE with no `approval` event, it adds "· no
     approvals".

9. **When each Workspace panel shows:**

   | Panel | When |
   | --- | --- |
   | Banner | `no_playbook` and the run isn't DONE |
   | Diagnosis | `outcome.diagnosis` |
   | Do it myself | ESCALATED |
   | Console steps | any `technician_steps` |
   | Close | ESCALATED with a verified console step |
   | Save as playbook | `can_save_playbook` |
   | Admin center | ESCALATED |

   Buttons are disabled while one of these is in flight. The API's 409s still appear as refusals.

### Drafts and the review screen

10. **How a draft reaches the review screen.** A draft is recorded on its run as `playbook_drafted`, and
    the snapshot returns it as `playbook_draft`, for both Save-as-playbook and audit drafts. A written
    draft (`POST /playbooks`) has no run and isn't recorded.
    **Recommendation:**
    - **Save as playbook:** posts the draft (about a minute; "Drafting…"), then navigates to
      `/playbooks/{intent}?run={id}`. The review page loads the run's snapshot and takes its
      `playbook_draft`.
    - **Audit drafts:** the workspace shows "Review the draft →" once `playbook_draft` appears.
    - **Written drafts:** `/playbooks/new` renders the same `PlaybookReview` component in place.
    - **The intent box:** on the Save form, prefilled from the plan. It is empty and required when the
      plan's intent is `unknown`, the trap run 21 hit. The review screen can still rename it before
      approving.

11. **There's no way to check an edited draft without approving it.** The checks run only inside
    `library.approve()`, and the ceiling only at approval.
    **Recommendation:**
    - Pull approve's checks into `library.check(intent, yaml, source, source_run_id) -> (problems,
      ceiling)`, and have `approve()` call it.
    - Expose it as `POST /playbooks/{intent}/check {yaml, source, source_run_id}` → `{problems, ceiling}`.
      It is code only, with no model call.
    - The review screen calls it on load and 500 ms after each edit, showing "✔ passes every check" or the
      list of problems. Approve still re-checks on the server.

12. **The source, side by side.** The `DraftSource` the drafter saw isn't stored, but its parts are on the
    run.
    **Recommendation:** rebuild it from the run's snapshot, by source kind:
    - **Agent or technician run:** the ticket text, then each verified step (the agent's `steps` and the
      `technician_steps`) with its params, actor and status, then the findings.
    - **Audit log:** the snapshot's `audit`: learned, already undone, and can't learn.
    - **Written:** the technician's own prose, held in the page.

    Two columns from `md:` up, stacked below that.

13. **Autonomy and versions.**
    - **The picker:** A1/A2/A3 buttons, defaulting to A2, with A3 disabled when the check's `ceiling` is A2
      ("needs a successful approved run on this playbook first").
    - **Without `?run`,** `/playbooks/[intent]` shows the current version read-only, its autonomy (changed
      through `POST /playbooks/{intent}/autonomy`, within the ceiling), and the version history.
    - **For old versions:** `GET /playbooks/{intent}` gains `yaml` on each version, so each one can be
      expanded.
    - **No editing an approved version this week.** You'd do that by saving a new draft. It's a cut that
      keeps the "edited shipped" ceiling rules out of the UI.

14. **The library** (`/playbooks`): one row per intent, from `GET /playbooks`, showing:
    - the intent, linking to its page;
    - the version and source (shipped / agent run / technician run / written / audit log);
    - the autonomy, with the ceiling when it's lower;
    - who approved it, and "Learned from run #N" for the run sources, linking to the run;
    - **Demote to A2** on any A3 row, one click, with no confirm.

    A **New playbook** button, and a "Playbooks" nav link in `layout.tsx`.

### The live loop and the learning cases

15. **`data/tech.db` already holds learned playbooks.**
    - `file.restore_version` v1 (from run 20, at A3), `site.grant_access` v1 and `user.update_title` v1.
    - So a fresh Sophie ticket would run on the learned v1. It would show no banner and need no approval,
      which isn't the loop.
    - Build-W4 puts "reset clears non-shipped versions, autonomy overrides and runs" in Day 22.

    **Recommendation:**
    - Pull the playbook half forward as `tech library reset [--yes]`, which deletes non-shipped versions,
      re-approves the newest shipped one where a learned version had retired it, and clears
      `ClientAutonomy`. Runs stay.
    - Day 22's reset calls it.
    - The `learned/` records stay on disk.

16. **Grace's ticket must be unambiguous** (Day 19's watch-outs). The drafted restore playbook leans
    towards asking the requester.
    **Recommendation:** stage with `tenant/reset.py --files all`, and use eval case #27's wording: "I need
    yesterday's version of Staffing Plan.xlsx back please, I overwrote it at 10am today." If her run ends in a question,
    edit the "one line" in step 1 to loosen that rule. That's the edit the loop needs anyway.
    - **Between the two Grace runs:** re-stage her file only (`--files overwrite` adds just this morning's
      overwrite) so there's something to restore again.

17. **The learning cases L3 and L4 offline** (`test_learning.py`, as Day 20 did L1/L2).
    - **L3:** a written playbook is drafted, refused at A3 and approved at A2. A ticket on it gets
      `approve/autonomy.unproven`; once that's approved and DONE, A3 is allowed.
    - **L4** is L1's flow driven through the **HTTP routes the UI calls**:
      1. `manual-step`, `close` and `playbook-draft`;
      2. `check`, then `approve` at A3;
      3. a second ticket with no approval.

      It overlaps L1 on purpose: it proves the screens' path, not the runner's.

---

## Part 1: backend

**Product code:**

- `api.py`:
  - `snapshot()` gains `no_playbook` and `playbooks_used` (decisions 1 and 8);
  - `GET /catalogue` (decision 3);
  - `POST /playbooks/{intent}/check` (decision 11);
  - `yaml` on each version in `GET /playbooks/{intent}`;
  - the docstring's route list.
- `playbooks/library.py`: `check()` pulled out of `approve()`; `reset_learned()`.
- `runner.py`: names on console steps (decision 5).
- `cli.py`: `tech library reset` (decision 15).

**Test code:** `test_learning.py` gains L3 and L4 (decision 17).

**Tests:**

- the snapshot's `no_playbook` for a no-playbook plan, a playbook run and a planless timeout;
  `playbooks_used` on a learned-playbook run;
- `/catalogue`: `group.addMember` is a console action with its schema; `tap.create` and
  `link.sendOneTime` are not;
- `/check`: a good draft gives `[]` and its ceiling; a widened one gives its problem; written gives A2;
- a console group add by GUID gets `groupName` from the transcript;
- `reset_learned`: the shipped v1 is current again, and autonomy is back to client facts;
- **L3** and **L4**.

**As built:**

- **`library.check(intent, yaml, *, source, source_run_id) -> (problems, ceiling)`:**
  - it holds the source, slug, near-duplicate, evidence and `playbook_check` checks;
  - the ceiling is `None` while there are problems;
  - `approve()` calls it, then adds its own autonomy, approver and A3-over-ceiling problems;
  - the problems come out in a different order (autonomy and `by` are now last). No test depended on the
    order.
- **`snapshot(store, run_id, web_origin, domain, library)`** takes the library now.
  - `no_playbook` is `None` before triage.
  - In `playbooks_used`, a version deleted since (a reset) keeps its intent and version, with `source: None`.
- **`GET /catalogue`**: `console` is decided by the action's *fixed* risk class, so `user.update` is in. Its
  `accountEnabled: false` form is `access.revoke`, which the technician gate allows.
  `api.NOT_IN_CONSOLE` holds the two services.
- **`POST /playbooks/{intent}/check`** takes `{yaml, source, source_run_id}` and returns
  `{problems, ceiling}`. `GET /playbooks/{intent}` versions carry `yaml`.
- **Console step names:** after grounding, an empty `groupName` is filled from the transcript, so a typed
  GUID is named by its confirming read. A name the technician typed is kept. Only group actions have a
  name param today (`fileName` on a restore is already required by the file checks' reads).
- **`library.reset_learned()`** deletes every non-shipped version and every `ClientAutonomy` row. Where an
  intent's newest remaining version is retired, it approves it again. It returns the deleted rows.
- **`tech library reset [--yes]`** lists what it would delete, then asks.
- **L3 and L4** run through `httpx` against `api.create_app(rt)` on the harness's `offline()` tenant.
  - **L3:** a written playbook → `check` (A2) → A3 is a 422 and A2 is a 201 → Luis waits on
    `autonomy.unproven` → approved, DONE → the ceiling is A3 → `/autonomy` A3.
  - **L4:**
    1. Jordan escalates; the snapshot has `no_playbook` and the diagnosis.
    2. `/catalogue` → `manual-step` (named "Northwind Operations") → `close` → DONE.
    3. `playbook-draft` → `check` (A3) → approve at A3.
    4. Luis is DONE with no approval, and `playbooks_used` says `technician_run` from run 1.
    5. Demote to A2 → the next Luis ticket waits for approval.

    Zero bypasses throughout.

**Tests:** `test_api.py` (+5), `test_technician.py` (+1), `test_library.py` (+1), `test_cli.py` (+1),
`test_learning.py` (+2).

**Result:**

- `uv run pytest`: 510 passed. `ruff` is clean.
- Eval cases 1-28 are unchanged. No live run, and no model was called.

## Part 2: workspace display

- `NoPlaybookBanner.tsx`, `EscalationDiagnosis.tsx` and `LearnedBadge.tsx`; `Outcome` skips the diagnosis
  keys.
- `lib/types.ts`: the new snapshot fields, plus `technician_steps`, `audit`, `playbook_draft` and
  `can_save_playbook` (already served, but not typed).
- **Tests:** display only. `npm run typecheck`, `lint` and `build` stay green.

## Part 3: "Do it myself" and Close

- `lib/console.ts` (pure): schema → fields, ID-field kinds, observed → options, form → params.
- `lib/api.ts`: `catalogue()`, `observed(id)`, `manualStep(...)` and `closeRun(...)`.
- `ManualStepPanel.tsx`:
  - the picker, form and Run;
  - the console steps (✔ verified, ✖ blocked with the rule, ✖ failed with the error), each tagged with the
    technician;
  - Close, with a note.
- `useRun.ts` event types (decision 6); `TECHNICIANS` (decision 7).
- **Tests (vitest):**
  - `group.addMember`'s schema → two ID selects plus an optional name;
  - a string list round-trips;
  - empty optionals are dropped;
  - ID kinds filter `observed`.

## Part 4: Save as playbook, and the admin center

- `SavePlaybook.tsx`: the intent and notes, then draft, then navigate (decision 10).
- `AuditDraft.tsx`:
  - "Waiting for audit logs (usually 1-3 minutes)", with a timer from `audit.since`;
  - then learned / already undone / can't learn;
  - then "Review the draft →", or a retry after a timeout.
- `lib/api.ts`: `draftPlaybook(...)` and `auditDraft(...)`.
- **Tests (vitest):** the `audit` payload → the waiting / found / timed-out view, on Day 20's recorded
  shapes.

## Part 5: `/playbooks/[intent]` review

- Read `node_modules/next/dist/docs/` first.
  - `cacheComponents` is on and params are runtime data, so the page follows `runs/[id]/page.tsx`: a
    server page that only renders a client component inside `<Suspense>`.
  - The component reads `useParams()` and `useSearchParams()`.
- `app/playbooks/[intent]/page.tsx` and `components/PlaybookReview.tsx`:
  - the YAML in a monospace textarea, with its source beside it (decision 12);
  - the check results, which are debounced (decision 11);
  - the intent box, the autonomy picker (decision 13) and "Approving as";
  - Approve → `POST /playbooks/{intent}/approve` → the playbook page, without `?run`.
- `components/PlaybookVersions.tsx`: the current version, its autonomy and the version history.
- `lib/api.ts`: `getPlaybook`, `checkPlaybook`, `approvePlaybook` and `setAutonomy`.
- `lib/playbooks.ts` (pure): the source view model from a snapshot, and the picker's allowed levels.
- **Tests (vitest):**
  - a technician-run snapshot → its source steps carry the actor;
  - an audit snapshot → the learned and unmapped lists;
  - A3 is disabled under an A2 ceiling.

## Part 6: `/playbooks/new` and the library

- `app/playbooks/new/page.tsx`:
  - the intent, plain-English steps, and an allowed-actions checklist (all of `/catalogue`);
  - then Draft (`POST /playbooks`);
  - then `PlaybookReview` in place, with source `written` and the prose as the source.
- `app/playbooks/page.tsx` and `components/PlaybookLibrary.tsx` (decision 14), plus the nav link.
- **Tests:** display. The written path's rules are covered by L3 in Part 1.

## Part 7: live, in the browser (needs your approval)

From `tech library reset` and `tenant/reset.py --files all`:

1. **Sophie** (eval case #23's wording: "I accidentally saved over Q3 Parking Rates.xlsx this morning…"):
   - Expect the no-playbook banner, one approval, a verified restore and DONE.
   - Check the SharePoint version history.
2. **Save as playbook** (one Opus call) → review:
   - the source is beside the YAML, and it passes;
   - edit one line, and it still passes;
   - A3 → Approve.
3. **Grace** (decision 16): expect the learned badge, `auto/autonomy.a3…`, **zero approvals** and DONE.
4. **Demote** from the library → re-stage Grace's file → Grace again: expect it to need approval. Then
   approve it.
5. **L4:**
   - Jordan's Northwind Operations ticket → escalates;
   - Do it myself (`group.addMember`) → Close: expect the reply to name the group;
   - Save → approve.
6. **`/playbooks/new`:** a written playbook can't be set to A3 (Build-W4 manual check 5), so approve it at
   A2.
7. **The admin center, a UI pass only:** an escalated run, a change you make in Entra, then the waiting
   state → found → review. (Day 20 already proved it live.)
8. **Revert:**
   - Jordan's membership and title;
   - re-stage the files;
   - `tech library reset`, so Day 22 starts clean.

Timings, approvals and cost for each run go here.

## Verification

- `uv run pytest` and `uv run ruff check backend` are green, including L1-L4 with zero bypasses.
- In `web/`: `npm test`, `npm run typecheck`, `npm run lint` and `npm run build` are green.
- The agent's gate decisions and existing event payloads are unchanged (eval cases 1-28). The onboarding
  stream fixture replays the same.
- Live (Part 7): Build-W4's manual checks 1-6 pass in the UI.

## Cut line (Build-W4, in order)

1. The `/playbooks/new` UI. Written playbooks stay API-only, and L3 stays offline.
2. "I fixed it in the admin center" in the UI. The CLI and route stay.

Do it myself, Close, Save as playbook, the review screen, the library's demote and the live Grace loop are
on the critical path.
