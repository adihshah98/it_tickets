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

**As built:**

- **One `components/Learning.tsx`** holds `NoPlaybookBanner`, `EscalationDiagnosis` and `LearnedBadge`, the
  way `Closeout.tsx` holds several.
- **The banner** goes under the header while `no_playbook` and the run isn't DONE.
- **The badge** goes in the header for each non-shipped `playbooks_used` entry. It reads "Learned playbook
  · {intent} vN · from run #N", linking to `/playbooks/{intent}` (Part 5) and to the source run. It adds "·
  no approvals" when the run is DONE with no `approval`.
- **The diagnosis** goes above `Outcome` on an ESCALATED run whose outcome has a `diagnosis`. `Outcome`
  filters `DIAGNOSIS_KEYS` out of its key/value dump.
- **Changed from decision 2: the `missing_action` label.** It reads "The change it couldn't propose:",
  not "Not in the catalogue:". On run 22 the investigator put `group.addMember` there: the playbook's
  gotcha stopped it proposing the add, but the action *is* in the catalogue. That matches the field's own
  description.
- **`lib/types.ts`:** `Snapshot` gains `technician_steps`, `audit`, `playbook_draft`, `can_save_playbook`,
  `no_playbook` and `playbooks_used`, with `TechnicianStep`, `AuditObserved` (a union on `status`),
  `ObservedStep`, `UnmappedAudit`, `PlaybookDraft` and `PlaybookSource`.

**Checked against `data/tech.db`** (the API and `next dev` locally; reads only, no tenant calls):

- **Run 23** has the badge "Learned playbook · site.grant_access v1 · from run #21 · no approvals".
- **Run 22**, escalated by the learned playbook's gotcha, has the badge without "no approvals", and the
  diagnosis with its evidence, checks, next step and missing change.
- **Run 24**, with no playbook and rejected, has the banner. It has no diagnosis, because a rejection
  carries none.

**Result:** `npm test` (14), `typecheck`, `lint` and `build` are green. No backend change.

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

**As built:**

- **`lib/console.ts`:**
  - `fields(action)` reads `anyOf: [X, null]` as an optional X;
  - `driveId` and `itemId` become **one file picker** (value `drive|item`), and `versionId` a version
    picker filled once the file is picked, with the newest marked "current";
  - `groupName` and `fileName` are left off the form (`AUTO_FILLED`): the backend names the group, and
    `toParams` sets `fileName` from the file picked;
  - a boolean is a Yes / No / "Leave as is" select, so `user.update` doesn't send `accountEnabled` unless
    you choose it;
  - `toParams` drops empty optionals and lists the empty required fields, which disables Run.
- **Found while building: the file picker had only SharePoint IDs.** `observed` gave a file as `{driveId,
  itemId, versions}`. Now `runner.observed_objects` adds `name` and `site` from the run's `files.search`
  results, and `versionDetails` (id, time, size) from `files.versions`. The picker shows "Q3 Parking
  Rates.xlsx · Northwind Operations" and "2.0 · 13/10 21:58 · 12.8 KB". `versions` keeps its shape. This
  is a small backend change, with a test.
- **`ManualStepPanel.tsx`:**
  - "Working as" (`TECHNICIANS = ["aditya"]`);
  - the action picker shows console actions with their risk class and "Verified by: …";
  - an ID field is a select of observed users, groups or SKUs, plus "Type an ID…", which gives a text box
    (the backend runs the confirming read);
  - Run waits for verify, then shows "t1: … verified" or "not done: {reason}". A 409 shows as "Refused:
    …";
  - the console steps (✔ / ⊘ blocked / ✖ failed) carry "by {name}", the rule and collapsed params;
  - Close (with a PSA note) appears once a step has verified. It calls `reopen()` to follow the close-out
    on the stream.
  - `observed` is re-fetched after each step, because a typed ID's confirming read adds to it.
- **Where it shows:** under the diagnosis while ESCALATED. Once the run has closed, it moves to the
  execution's place, so a console run reads step → definition of done → reply. The first placement put
  the steps after their own close-out, which read backwards on run 21.
- **`useRun` / `REFRESH_ON`:**
  - `audit_observed` and `playbook_demoted` are added to the stream's event types;
  - `audit_observed`, `playbook_drafted`, `playbook_approved` and `playbook_demoted` now refresh the
    snapshot;
  - `tool_call` was considered and left out: it would refresh on every investigation read.
- **Fixtures:** `tests/fixtures/catalogue.json` (`GET /catalogue`) and `observed-run20.json` (run 20's
  `observed`), so the tests use the backend's real schemas. Like `onboard.sse`, they hold the demo
  tenant's IDs and UPNs.

**Checked against `data/tech.db`, with no Run or Close clicked** (those write to the tenant, which is Part
7):

- **Run 24:** picked `user.update` → User = "Jordan Lee (jlee@…)" from the reads, Job title "Lot
  Supervisor", and the optional fields marked. Run was enabled.
- **Run 21:** the console step reads "✔ group.addMember t1 verified · by aditya · technician.self", before
  the definition of done and the reply.

**Result:**

- Web: `npm test` (25: +11 in `console.test.ts`), `typecheck`, `lint` and `build` are green.
- Backend: `uv run pytest` 511 passed (+1). `ruff` is clean.

## Part 4: Save as playbook, and the admin center

- `SavePlaybook.tsx`: the intent and notes, then draft, then navigate (decision 10).
- `AuditDraft.tsx`:
  - "Waiting for audit logs (usually 1-3 minutes)", with a timer from `audit.since`;
  - then learned / already undone / can't learn;
  - then "Review the draft →", or a retry after a timeout.
- `lib/api.ts`: `draftPlaybook(...)` and `auditDraft(...)`.
- **Tests (vitest):** the `audit` payload → the waiting / found / timed-out view, on Day 20's recorded
  shapes.

**As built:**

- **`lib/learning.ts` (pure):**
  - `defaultIntent`: the first of the last draft's intent, the plan's, then triage's, that isn't
    `unknown`. Run 21's last draft *was* `unknown`, so the first version left the box empty;
  - `INTENT_RE`, copied from the backend's `INTENT_RE` less `unknown`, which disables the button until the
    slug is valid;
  - `reviewHref`;
  - `auditView(audit, draft)` → `idle | waiting | found | timed_out`. A Save-as-playbook draft isn't
    offered as the audit one.
- **`SavePlaybook.tsx`:** on a DONE run with `can_save_playbook`.
  - It takes the intent and notes (one per line), then shows "Drafting… (about a minute)" and goes to
    `/playbooks/{intent}?run={id}`.
  - With an earlier draft on the run, it shows "Review the last draft →" next to "Draft again".
- **`AuditDraft.tsx`:** on an ESCALATED run, or any run with an `audit`.
  - **Idle:** a button.
  - **Waiting:** "Waiting for the audit logs (usually 1-3 minutes, up to 10)…" with a timer.
  - **Found:** Learned (each step's params) / Already undone / Can't learn (activity · time · reason), then
    "Drafting…" until the draft lands, then "Review the draft → capped at A2".
  - **Timed out:** "Look again".
  - It's started as `TECHNICIANS[0]` and calls `reopen()` to follow the polling on the stream.
- **Backend: the snapshot's `audit` gains `at`.** That's when that status was recorded. `since` is when the
  run escalated, so a timer from `since` would have counted the hours before you clicked. Tested in
  `test_audit.py`.
- **Fixed in passing:** Part 3's edit had left `viewLink =(token` in `lib/api.ts`.

**Checked against `data/tech.db`, with no draft or audit started:**

- **Run 24:** Learned `user.update` (userId, jobTitle "Lot Supervisor"), then "Review the draft
  (user.update_title) → capped at A2 (from audit logs)".
- **Run 21:** Save as playbook, with "Draft again" and "Review the last draft". The review link 404s until
  Part 5.

**Result:**

- Web: `npm test` (33: +8 in `learning.test.ts`), `typecheck`, `lint` and `build` are green.
- Backend: `uv run pytest` 511 passed. `ruff` is clean.

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

**As built:**

- **The route** follows `runs/[id]`: `app/playbooks/[intent]/page.tsx` renders `PlaybookPage` (a client
  component reading `useParams`/`useSearchParams`) inside `<Suspense>`, as the Next 16 docs say for
  runtime params with `cacheComponents`.
  - **With `?run=N`,** it loads that run's snapshot and reviews its `playbook_draft`.
  - **Without it,** it shows the approved playbook (`PlaybookVersions`), or "No playbook for … yet".
- **`PlaybookReview.tsx`:**
  - the intent box and the YAML textarea (no wrapping), with the source beside it (`SourcePanel`);
  - **renaming rewrites the YAML's `intent:` line** (`renameIntent`), because `parse_playbook` rejects a
    playbook that declares a different intent. Without this, every rename of run 21's `unknown` draft
    would fail its check;
  - `/check` runs 500 ms after each edit, together with `GET /playbooks/{intent}`. So "it becomes v2 of
    site.grant_access (now v1, A3)" follows the rename, not the URL's intent;
  - the autonomy picker allows up to the check's ceiling. A level picked earlier falls back to A2 if an
    edit lowers the ceiling;
  - Approve is disabled until the check passes. A 422 shows each problem, through `ApiError.problems`,
    which now keeps a list `detail` as a list. Approving goes to the playbook's page.
- **`Playbooks.tsx`:**
  - `AutonomyPicker`, with each level's meaning, and the "A3 needs a successful approved run" note under
    an A2 ceiling;
  - `SourcePanel`: the ticket and the run's verified steps by agent or technician, with findings; or the
    audit changes; or the written prose (Part 6);
  - `PlaybookVersions`: autonomy (`/autonomy`), the current YAML with its source run and approver, and
    each version's YAML in a `<details>`, newest first.
- **`lib/playbooks.ts` (pure):** `allowedLevels`, `defaultLevel`, `renameIntent`, `sourceView`,
  `LEVEL_TEXT` and `SOURCE_TEXT`.
- **Fixtures:** `snapshot-run21.json` and `snapshot-run24.json`, `GET /runs/{id}` from `data/tech.db`.

**Checked against `data/tech.db`.** The check route reads only. Approve and the autonomy buttons were not
clicked, since they'd change your library.

- **`/playbooks/unknown?run=21`:**
  - the draft is beside "Ticket · run #21", "✔ group.addMember by aditya" and the findings;
  - "✖ name the job" while it's `unknown`;
  - renamed to `site.grant_access`, the YAML says so, it shows "✔ Passes every check" and "it becomes v2 of
    site.grant_access (now v1, A3)", and A3 is offered (a technician run).
- **`/playbooks/user.update_title?run=24`:** it shows "Draft learned from the audit logs" and the audit
  change, it passes, it becomes v2 (now v1, A2), and A3 is held back with the note.
- **`/playbooks/site.grant_access`:** A3 is selected, with "learned from a technician's run (run #21) ·
  approved by aditya", the YAML and the versions.

**Result:**

- Web: `npm test` (38: +5 in `playbooks.test.ts`), `typecheck`, `lint` and `build` are green.
- Backend: unchanged, 511 passed.

## Part 6: `/playbooks/new` and the library

- `app/playbooks/new/page.tsx`:
  - the intent, plain-English steps, and an allowed-actions checklist (all of `/catalogue`);
  - then Draft (`POST /playbooks`);
  - then `PlaybookReview` in place, with source `written` and the prose as the source.
- `app/playbooks/page.tsx` and `components/PlaybookLibrary.tsx` (decision 14), plus the nav link.
- **Tests:** display. The written path's rules are covered by L3 in Part 1.

**As built:**

- **`/playbooks/new` → `NewPlaybook.tsx`:**
  - the intent (checked against `INTENT_RE`), the steps in plain English, and the allowed actions (every
    catalogue action, with its risk class);
  - Draft is enabled once all three are filled, then shows "Drafting… (about a minute)";
  - the draft opens `PlaybookReview` in place, with source `written`, no run, and "As written" plus the
    allowed actions as its source. A written draft isn't recorded anywhere, so the page keeps it;
  - "← Back to what you wrote" keeps the form;
  - `new` is a static segment, so it wins over `[intent]`.
- **`/playbooks` → `PlaybookLibrary.tsx`:** a table from `GET /playbooks` showing:
  - the intent, linking to its page;
  - the version;
  - the source (shipped plain; learned or written in violet), "from run #N" linking to the run, and the
    approver and date;
  - the autonomy, with "(A3 once a run on it succeeds)" under an A2 ceiling;
  - **Demote to A2** on A3 rows, one click, then a reload.

  Plus a **New playbook** button.
- **Nav:** "Playbooks" on the right of the top bar.
- **`lib/api.ts`:** `writtenPlaybook(...)`.

**Checked against `data/tech.db`, with no Draft or Demote clicked:**

- **`/playbooks`** lists:
  - `file.restore_version` v1, learned from an agent run (run #20), A3, Demote;
  - `mfa.reset`, `user.offboard` and `user.onboard`, shipped, A2;
  - `site.grant_access` v1, from a technician's run (run #21), A3, Demote;
  - `user.update_title` v1, from the audit logs (run #24), A2.
- **`/playbooks/new`:** the catalogue checklist renders. Draft enables after an intent, steps and
  `group.addMember`.

**Result:** web `npm test` (38), `typecheck`, `lint` and `build` are green. The backend is unchanged (511
passed).

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

## Part 7 (done): live, 2026-10-14

**The loop worked in the UI** after two fixes to `files.search`, found on the first ticket (below).

| Step | Run | Result |
| --- | --- | --- |
| 1. Sophie, no playbook | 27 | Banner. Plan: restore 2.0, `approve/no_playbook.approve`, with a confirm question (2.0 or 1.0). One approval (Taylor). Verified: 2.0 restored as 4.0, 12,765 bytes. `version_restored` ✔ and `message_sent` ✔. Investigated in about 15 s, with `files.search` at 1.5 s. |
| 2. Save → edit → A3 | 27 | Drafted in one Opus call (3,410 in / 1,149 out), passing every check first time, and opened on the review screen with run 27 as its source. Edited (below), still passing, then **approved at A3** as `file.restore_version` v1 (`agent_run`, run #27). |
| 3. Grace, hands-free | 28 | **DONE in 32 s with no approval.** `auto/autonomy.a3.write.reversible`, Staffing Plan 2.0 restored as 4.0, both probes ✔. Header: "Learned playbook file.restore_version v1 · from run #27 · no approvals". |
| 4. Demote → Grace again | 29 | Demote to A2 from the library, then files re-staged (`tenant/reset.py`). Grace's ticket waited on **`approve/autonomy.unproven`**. Approved by Taylor → DONE, both probes ✔. |
| 5. L4: console run | 30 | Jordan's ticket was planned (`group.addMember`, needs approval), since search no longer times out. Taylor rejected it → ESCALATED (rejected). Do it myself: `group.addMember` Northwind Operations ← Jordan, both picked from the reads, "✔ t1 verified · by aditya · technician.self". Close with a note → DONE. **The reply names the group** (decision 5). Saved: intent prefilled `group.add_member`, renamed `site.grant_access`. Draft (3,604 / 1,420) passed → **A3** (`technician_run`, run #30). |
| 6. Written playbook | — | `/playbooks/new`: `user.update_title`, plain-English steps, `user.update`. Drafted and passed; **A3 greyed out** ("needs a successful approved run on this playbook first"). Approved at A2, ceiling A2. |
| 7. Admin center | — | **Skipped.** Your answer didn't cover it, and it needs you in Entra. Day 20 proved it live, and Part 4 checked its UI on run 24. |
| 8. Revert | — | `tenant/reset.py` removed Jordan from Northwind Operations and re-staged the files. `tech library reset` deleted the three learned or written versions. Read back: Jordan is in Field Operations, Northwind Parking and Parking Supervisors, with title "Lead Lot Attendant". |

Build-W4's manual checks 1-5 pass in the UI. Check 6 (the admin-center draft) was skipped, as above.

**The "edit one line" took three lines.** The draft's `never` line ("Restore a version when the history
doesn't make clear which save was the unwanted one, unless someone has confirmed the target version") would
make every staged restore a question, because every version is by "SharePoint App", seconds apart.
Gotchas 15 and 16 said the same thing ("Don't guess. Get confirmation…" and "If the times and sizes don't
settle it, ask"), so editing only the `never` line would have left the playbook contradicting itself. The
three edits:

- **`never`:** "Restore anything older than the version just below the newest when the history doesn't make
  clear which save was the unwanted one, unless someone has confirmed the target version."
- **Gotcha 15:** "If the requester asks for the version from before their save, restore the one just below
  the newest; confirm anything further back first."
- **Gotcha 16:** "Take the one just below the newest, unless the requester points further back or the sizes
  say otherwise; then ask."

On stage, the edit is either rehearsed as these three lines or narrated as "loosen the confirm rule". Day
22's rehearsal decides.

**Fixes made live (committed separately):**

- **`c99c74b` `files.search` is now one Microsoft Search query** (`POST /search/query`, driveItem, region
  NAM), not a `root/search` per team site. Graph throttles those (429, `Retry-After: 30`, waited out quietly
  by `GraphClient`), so one `files.search` took 37 s and Sophie's no-playbook runs 25 and 26 escalated on
  the 60 s bound. It now takes 1-3 s.
  - Hits are kept to the team-site libraries and to files.
  - The top-folder fallback stays.
  - FakeTenant answers `/search/query`, and `writes()` treats it as a read.
  - You chose this over listing top folders, waiting out the throttle, or raising the bound.
- **`f11ffb9` stale hits dropped.** Straight after a reset re-uploads the files, the search index still
  returns the *deleted* item ID (`files.versions` → 404) and doesn't know the new one yet. Each hit is now
  confirmed with one item read (not throttled); a 404 is dropped, and the top-folder fallback finds the
  fresh upload.
- **`8b94d1e` reverts `2a494e3`.** That commit capped drive searches at two at a time, on the wrong
  diagnosis that concurrency was the cause (see the notes below). You chose to revert it.

**Follow-ups this found (not done):**

- **Grace isn't in the inbox's `SENDERS`,** so her tickets went through `POST /tickets` (the inbox's own
  call) and were watched in the UI. Add her (and any other demo requester) for Day 22's script.
- **Console steps name groups, not people:** "The user has been added to the Northwind Operations team
  group". Only group actions have a name param. A `userName` filled the same way would finish decision 5.
- **The drafter over-collects `must_investigate`:** six reads on run 30's draft, including `users.list()`,
  as on Day 20. This is still open.
- **Triage puts a file restore under "Out of scope, won't act on"** (runs 25-27, as on Day 18) while the
  investigator restores it anyway. That's confusing on screen.
- **The inbox's Start, the review screen's A3 and Approve, and the panel buttons sometimes ignored a click
  made by element reference right after a `form_input`;** clicking by position worked. This is about the
  test browser rather than the app, but it's worth a look if anyone reports dead buttons.
- **`tenant/reset.py` still prints "Not yet reset: app-side playbooks and runs (no app yet)".** Day 22's
  reset should call `tech library reset`.

### Notes from the pause at step 1

- **Reset:**
  - `tech library reset --yes` deleted `file.restore_version` v1, `site.grant_access` v1 and
    `user.update_title` v1, and autonomy went back to client facts;
  - `tenant/reset.py` re-uploaded the three files at 19:27 UTC, searchable at once with fresh item IDs.
- **Sophie, worded "…saved over Q3 Parking Rates.xlsx earlier today…":**
  - **Run 25** escalated on the **60 s no-playbook timeout**. `files.search` took 37 s, and the
    investigation had already found the file and its three versions.
  - **Run 26** escalated the same way: `files.search` took 38 s.
  - On both, the workspace showed the no-playbook banner, and triage put the restore under "out of
    scope" (as on Day 18). Nothing was written to the tenant.
- **The cause: Graph throttles the drive searches.** `GET /drives/{id}/root/search` answers 429 with
  `Retry-After: 30`, and `GraphClient` waits and retries quietly. One `files.search` is six drive searches
  (one per team site), so a throttled call costs about 36 s whatever the concurrency.
- **A wrong first diagnosis:** that concurrent searches stall. Commit `2a494e3` (at most two at once) was
  based on timings that just happened to fall in and out of the throttle window. My own diagnostic
  scripts sent dozens of searches in a few minutes, which made it worse. The cap does no harm, but it
  isn't the fix, and its commit message is wrong.

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
