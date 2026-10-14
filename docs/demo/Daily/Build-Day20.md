# Week 4 Day 20: learning from people, through console runs and audit logs

## Context

[Build-W4.md](../Weekly/Build-W4.md) Day 20. Day 19 learns playbooks from agent runs. Today adds the two
ways a **technician's** work becomes a playbook:

1. **Console run ("Do it myself").** On a run the agent couldn't finish, the technician picks catalogue
   actions. Our gate and executor run and verify them, with `actor: technician:{name}`. The run can then
   close, and "Save as playbook" works on it like an agent run.
2. **Audit-log learning ("I fixed it in the admin center").** The technician fixed it in Entra instead.
   We read `directoryAudits` for the run's objects and window, map each entry to a catalogue action, and
   draft from that, at A2 at most.

Each part ends with green tests and a commit, then a stop for review.

| Part | What | Behaviour change? |
| ---- | ---- | ----------------- |
| 0 | Spike: audit-log lag and filters (needs you in the Entra admin center) | None: **done**, lag ≤ 2.5 min |
| 1 | Technician steps: transcript from events, confirming read, gate's technician path, executor `actor`, bypass check | New path; agent runs unchanged: **done** |
| 2 | Technician close-out, wider `can_save_playbook`, learning case **L1** | Escalated runs can close via a technician: **done** |
| 3 | Audit-log learning: the read, `audit_map`, `from_audit_log`, the polling route, **L2** | New path: **done** |
| 4 | Live: one console step on the tenant, and an audit draft from a real admin-center change | Two probe fixes found live: **done** |

**Needs your approval:**

- **Part 0:** you make two changes in the Entra admin center, and I poll `directoryAudits` (read-only).
- **Part 4:** tenant writes, plus one Opus drafter call per draft.

---

## What I found reading the code

Each needs a decision before Part 1. Each has a recommendation.

1. **Which runs get the console.** Build-W4 says "no-playbook, escalated or A1 runs". But an A1 run
   always ends `ESCALATED` (kind `gate`, every write blocked). A no-playbook run is either `ESCALATED` or
   `AWAITING_APPROVAL`, and in the second case the agent's own plan is waiting. Mixing technician steps
   into that plan would make "what was approved" unclear.
   **Recommendation:** technician steps only on **`ESCALATED`** runs, whatever the kind (`no_playbook`,
   `gate`/A1, `questions`, `execution`, `rejected`, `dod`). A technician who prefers to do an awaiting
   run themselves rejects it first, which escalates it.

2. **`ESCALATED` is terminal, so the run can't close afterwards.** `TRANSITIONS` gives `ESCALATED` no exit.
   **Recommendation:**
   - Technician steps leave the run `ESCALATED`; the steps are events only.
   - A new `runner.technician_close(run_id, by, note)` is the one way out: `ESCALATED → CLOSING → DONE`
     (or back to `ESCALATED` if the DoD fails). It's allowed only when at least one technician step
     verified.
   - The transition is added to `TRANSITIONS` and only `technician_close` uses it. A new state such as
     `TECHNICIAN` was considered, but it adds a state the UI, stream and tests would all need to learn,
     for no gain.

3. **What the gate does for a technician.** Build-W4: "`never` and catalogue blocks still apply; `approve`
   is satisfied by the technician's own action". The open question is the no-playbook allow-list, A1 and
   the risk classes.
   **Recommendation:** add a `gate.evaluate_technician(step, …)`.
   - **Still blocks:** an unknown action, `never.privileged_group`, `client_facts.group_handling`,
     `catalogue.group_kind`, and the write bounds.
   - **Doesn't apply:** `no_playbook.not_allowed` and `autonomy.a1`. Escaping those is the reason for the
     console, and a technician could make the same change in the admin center anyway.
   - **Also blocked for technicians this week:** `security.credential`, `spend` and `data.access`. A TAP
     needs the OOB flow and the one-time link, a purchase needs the mock's seat flow, and mailbox access is
     a data grant. Those have flows the console doesn't have yet, so they're Week 5 (rule
     `technician.not_in_console`).
   - **Otherwise** the decision is `approve` with rule `technician.self`, satisfied by the technician acting.
   - Recorded as a normal `gate_decision` event with `actor`.

4. **Grounding a technician's IDs.** The linter grounds against the investigator's `Transcript`, which
   lives only in memory during investigation. The harness rebuilds one from `tool_call` events
   (`eval_harness.transcript_of`).
   **Recommendation:**
   - Move that into `Transcript.from_events(events)`. The console's ID picker and the grounding check both
     use it.
   - For an ID the technician *types*, add a confirming read, `directoryObjects.get(id)` →
     `GET /directoryObjects/{id}` (type, display name). It's recorded as a `tool_call` with `actor`, so it
     grounds the ID.
   - It's not offered to the investigator. There is no group read tool today, so this one generic read
     covers users and groups.
   - File IDs use the existing `files.search`/`files.versions` grounding.
   - `$ref:` params are refused: a console step stands alone.

5. **`actor` on step events.** **Recommendation:**
   - `Execution` gets `actor: str = "agent"`, added to `step_started`/`step_verified`/`step_failed` *only
     when it isn't the agent*, so every existing payload is unchanged.
   - `from_run` already reads `actor`, defaulting to `agent`.
   - Console step IDs are `t1`, `t2`, …, so they never clash with plan step IDs.

6. **Close-out for a technician run.** `close_out` picks the triaged intent's playbook DoD when there is
   one. For an A1 run that's e.g. the MFA playbook, whose probes expect plan fields (`$plan.methods_to_remove`)
   the technician's steps don't have.
   **Recommendation:**
   - `technician_close` builds an in-memory `Plan` from the technician's verified steps and always uses
     `closeout.no_playbook_dod(plan)`: one probe per action that has one, plus `message_sent`.
   - The technician's note goes into the close-out context, and from there into the PSA note.
   - No new `plan` event: the agent's plan stays the run's plan.
   - The snapshot gains `technician_steps`, built from events, for Day 21's panel.

7. **`can_save_playbook` widens:** DONE, at least one verified write, and (no playbook **or** any
   technician step). An A1 run a technician finished can then become a better playbook version.
   `from_run` already sets `technician_run` when a step's actor is a technician, so the ceiling treats it
   like an agent run.

8. **The zero-bypass check** (eval harness, `bypasses`). A technician `step_started` must follow a
   non-block `gate_decision` for that step. An `approve` decision is satisfied by the step's own
   `actor: technician:*` instead of an `approval` event. An agent step still needs the approval. Tests
   cover both.

9. **Audit-log mapping** (`learning/audit_map.py`): `activityDisplayName` → catalogue action, with params
   taken from `targetResources` and `modifiedProperties`.
   - A first cut, to be confirmed by the spike:
     - "Add member to group" → `group.addMember`;
     - "Remove member from group" → `group.removeMember`;
     - "Add owner to group" → `group.addOwner`;
     - "Update user" → `user.update`, with the changed fields from `modifiedProperties`;
     - "Disable account" → `user.update {accountEnabled: false}`;
     - "Invalidate all refresh tokens for user" → `sessions.revoke`.
   - Anything else is listed as "can't learn: not in the catalogue". Password resets and app consents,
     for example.
   - Entries are kept only if they fall inside the window (escalation → now), target an ID observed in
     the run, and were initiated by a user, not an app. That keeps our own writes and service noise out.

10. **An audit step's evidence.** We didn't execute it, so there is no verify.
    **Recommendation:** one confirming read per mapped step shows the change is still in place
    (`users.memberOf` for a group add, `users.get` for user fields). Its result is the step's `verified`.
    A change that's already been undone is listed, not learned. The draft is `audit_log` and the ceiling
    caps it at A2.

11. **Waiting for audit logs.** Build-W4: "poll for up to 15 minutes".
    **Recommendation:**
    - `POST /runs/{id}/audit-draft` starts a background task (the `TaskRunner`, `escalate=False`). It polls
      every 30 s until at least one mapped entry is found, then waits one more poll for stragglers.
    - It emits `audit_observed` (mapped and unmapped entries, a new event type) and then
      `playbook_drafted`. A timeout emits `audit_observed` with `timed_out: true`.
    - The snapshot shows `audit: waiting | found | timed_out`. The poll interval and limit are settings,
      so tests use 0 s.

12. **The learning cases L1 and L2.** They span several phases (a ticket, technician steps, close, save,
    then a second ticket), and `cases.yaml` describes one ticket.
    **Recommendation:** write them as tests in a new `tests/test_learning.py`, using the harness's seed,
    `run_case` and recorded models. Each test names its L-number. `cases.yaml` keeps one-ticket cases.
    - **L1:** a no-playbook ticket ("please give Jordan Lee access to the Northwind Operations site"; he's
      already in Parking Supervisors, as Part 0 found). It's recorded as
      escalating, with the investigator unsure the requester may grant it. The technician adds the member
      in the console, closes, saves at A3, and a second identical ticket runs with no approval.
    - **L2:** an escalated run, then FakeTenant `directoryAudits` with one mapped and one unmapped entry,
      gives a draft with only `group.addMember`, the unmapped one listed, and approval refused at A3.

---

## Part 0 (done): spike results

Run on 2026-10-14, 00:21-00:32 UTC. You made both changes in the Entra admin center at about 00:29 UTC,
and I polled `directoryAudits` every 30 s, read-only. The scripts were throwaway and are not committed.
**Jordan was already in Parking Supervisors**, so the group add used **Northwind Operations** instead.
It's a plain M365 team-site group with no `group_handling` rule. L1 and Part 4 use the same change.

**Lag: under 3 minutes, so audit-log learning stays on stage.**

| Change (by you) | `activityDateTime` | First seen | Lag |
| --- | --- | --- | --- |
| Update user (Jordan's job title) | 00:29:31 | 00:30:58 | ≤ 88 s |
| Add member to group (Jordan → Northwind Operations) | 00:29:00 | 00:31:29 | ≤ 149 s |

Other entries took 66-166 s. Decision 11's 30 s poll is fine, and its 15-minute limit is generous. Change
the limit to **10 minutes**, with "waiting for audit logs (usually 1-3 minutes)" on screen.

**The shapes, which settle decision 9:**

- **"Add member to group"** has category `GroupManagement` and `operationType` `Assign`.
  - `targetResources[0]` is the **user** (`type: User`, `id`, `userPrincipalName`). Its
    `modifiedProperties` are `Group.ObjectID` and `Group.DisplayName`, whose `newValue` is a JSON-quoted
    string (`"\"619403f7-…\""`).
  - `targetResources[1]` is the **group** (`type: Group`, `id`, `groupType: unknownFutureValue`, display
    name `null`).
  - The mapping is `group.addMember {groupId: the Group target's id, userId: the User target's id}`.
- **"Update user"** has category `UserManagement` and `operationType` `Update`.
  - `targetResources[0]` is the user. Its `modifiedProperties` have one entry per field, in PascalCase,
    with JSON-array values: `JobTitle: ["Lead Lot Attendant"] → ["Lot Supervisor"]`.
  - It also has two bookkeeping entries, which are skipped: `Included Updated Properties` (the list of
    names) and `TargetId.UserType`.
  - The mapping is `user.update {userId, <camelCase field>: newValue[0]}`. Only fields `UserUpdateParams`
    accepts are kept; anything else makes the entry unmapped.
- **`targetResources` display names are `null`** on both. Names come from the UPN, or from the confirming
  read (decision 10).

**`initiatedBy` and noise, which settle the filter.** In 11 minutes there were 11 entries for one
admin-center session. Only 2 were the changes:

- Your changes have `initiatedBy.user` (id, UPN, IP) and `app: null`. Our app's own writes (seen in the
  last 24 h) have `user: null` and `app.displayName: northwind-ai-tech`.
- **App-initiated noise:**
  - "Update user" on *your* account by "Azure MFA StrongAuthenticationService": your sign-in refreshing
    the Authenticator's last-used time. Its `modifiedProperties` carry a device token, so **raw audit
    entries must never go to the drafter**; only mapped steps do.
  - "Add service principal" by "Microsoft Azure AD Internal - Jit Provisioning".
- **User-initiated noise:** "User started security info registration", "Get passkey creation options",
  "Get signInIdentifiers" ×2 and "Validate user authentication". All of them are on your own account or
  on no object, and are mostly `operationType: Read`.
- **A trap:** `GroupsODataV4_Get` has `user: null` but `app.displayName` = your UPN and
  `app.servicePrincipalId` = your user ID. The "has `initiatedBy.user`" rule rightly drops it (it's a read
  anyway).
- **The filter, as decided:**
  - keep an entry only if `initiatedBy.user` is set, `operationType` isn't `Read`, its time is in the
    window, and a `targetResources` id was observed in the run;
  - map only the known activity names;
  - list the other kept entries as "can't learn".

  On this spike's 11 entries, that keeps exactly the two changes.

**Filters:**

- `activityDateTime ge {t}` works app-only.
- **`targetResources/any(t: t/id eq '{guid}')` works app-only** too: it returned exactly
  `['Update user', 'Add member to group']`. `from_audit_log` uses both, one query per observed user or
  group ID, merged by entry ID.

**Undo:** both changes are still on the tenant (Jordan is in Northwind Operations, titled "Lot
Supervisor"). Revert them before Part 4.

## Part 0: the planned spike

1. You, in the Entra admin center: add a test user (e.g. Jordan Lee) to a group (e.g. Parking
   Supervisors), then edit one field on a user (e.g. Jordan's job title). Note the time of each.
2. Me: poll `GET /auditLogs/directoryAudits?$filter=activityDateTime ge {t0}` every 30 s for up to 20
   minutes. Record:
   - when each entry appears;
   - its `activityDisplayName`, `targetResources` and `modifiedProperties`;
   - `initiatedBy`.
3. Check that `targetResources/any(t: t/id eq '{guid}')` works app-only.
4. Then you undo both changes (or I do it via Graph, with your OK).

The results go into this doc and settle decision 9's names and decision 11's timing. If lag is over about
10 minutes, the audit draft stays real but off-stage, and the demo uses a recorded one (Build-W4's rule).

## Part 1: technician steps

**Product code:**

- `graph/reads.py`:
  - `Transcript.from_events()` (decision 4);
  - `directoryObjects.get` in a separate `CONFIRM_READS` dict, not `READS`, so the investigator never
    gets it.
- `safety/gate.py`: `evaluate_technician(step, transcript, facts, run_writes)` (decision 3).
- `execution/executor.py`: `Execution.actor` (decision 5).
- `runner.py`: `technician_step(rt, run_id, action, params, by)`. It:
  1. checks the state is `ESCALATED`;
  2. validates the params as a `Step` and refuses `$ref`;
  3. grounds the IDs, running the confirming read for a typed GUID;
  4. runs the gate, then `run_step`.

  It returns `(ok, step_id, error)`.
- `api.py`: `POST /runs/{id}/manual-step {action, params, by}`, awaited inline, and `GET
  /runs/{id}/observed` (the ID picker's list: objects from the transcript, with name and kind).
- `cli.py`: `tech step RUN ACTION key=value… --by NAME`.

**Test code:**

- `eval_harness.bypasses` learns about the technician actor (decision 8); `transcript_of` becomes
  `Transcript.from_events`.

**Tests:**

- a `never` action (a privileged group add) → blocked, and nothing is written;
- `tap.create` → blocked `technician.not_in_console`;
- an ID never observed → rejected, but a typed GUID the confirming read finds → accepted;
- a non-ESCALATED run → refused;
- the step's events carry `actor`, and agent step payloads are unchanged;
- bypass check: a technician `approve` step without an approval passes, and an agent one still fails.

**As built:**

- **`runner.technician_step(rt, run_id, action, params, by)`** returns `(ok, step_id, error)`.
  - A gate block or a failed verify is an *answer* (`ok: false`, with the reason).
  - It refuses (`RunError`, a 409) **before recording anything** when:
    - the run isn't ESCALATED;
    - the action is unknown;
    - the params don't validate;
    - a param is a `$ref:`;
    - a typed ID can't be confirmed.
  - The actor is `technician:{email}`: a bare name gets the tenant's domain, through `runner.person()`,
    which `accept` uses too now.
  - Step IDs are `t1`, `t2`, …, counted from earlier technician gate decisions.
- **The confirming read is `directoryObjects.get`**, in `reads.CONFIRM_READS`, which `fetch` falls back
  to.
  - It returns `{id, type, displayName}`, plus `userPrincipalName` for a user and `kind`/`roleAssignable`
    for a group. So a typed privileged group is still caught by `group_problem`.
  - It's recorded as a `tool_call` with `actor`. A failed one is recorded too, then refused.
- **`gate.evaluate_technician`** follows decision 3.
  - `client_facts.group_handling` still blocks: RDS-Users and VPN-Users stay "a technician's job outside
    Graph", even in the console.
  - The write bound counts all of the run's `step_started` events.
- **`Execution.actor`:** `emit` adds `actor` to `step_*` events only when it isn't the agent. A test
  checks that an agent run's step events have no `actor` key.
- **Step events and gate decisions on a technician step** both carry `actor`. The bypass check requires
  that a technician's step ran on a decision made *for that technician*, so an agent `approve` can't be
  borrowed, and vice versa.
- **`lint._file_problems` → `lint.file_problems`,** shared with the console for a file restore's IDs.
- **`runner.observed_objects()` / `GET /runs/{id}/observed`:** the ID picker's list, which is named objects
  from the reads plus files (drive, item, version list).
- **`POST /runs/{id}/manual-step`** is awaited inline, and returns 409 if a background task is running on
  the run. `tech step RUN ACTION key=value… --by NAME` reads values as JSON when they parse (`false`, `60`),
  else as text. The CLI printer tags steps and gate decisions with `[technician:…]`.

**Tests:**

- `test_technician.py` (17):
  - a step is gated, run and verified as the technician's, with no bypass, and the run stays ESCALATED;
  - IDs go `t1` → `t2`;
  - a privileged group → blocked, nothing written;
  - `tap.create` → `technician.not_in_console`;
  - a typed ID is confirmed by one read, and an unknown ID is refused with the failed read on record;
  - unknown action / bad params / `$ref` → refused, nothing recorded;
  - a non-ESCALATED run → refused;
  - agent steps carry no `actor`;
  - gate units: a revoke is allowed, RDS-Users is blocked, the write bound applies;
  - `Transcript.from_events`;
  - the route (ok, a block, 409, `observed`, 404);
  - CLI param parsing.
- `eval_harness`: a bypass test for technician actors.

**Result:**

- `uv run pytest`: 476 passed. `ruff` is clean.
- Eval cases 1-28 are unchanged (agent gate decisions and event payloads).
- No live run.

## Part 2: technician close-out and L1

- `runner.technician_close(rt, run_id, by, note)` (decisions 2 and 6), plus the `ESCALATED → CLOSING`
  transition.
- `closeout.close_out(..., dod=None, note=None)`, so a caller can pass the DoD and the technician's note.
- `runner.can_save_playbook` widens (decision 7). The snapshot gains `technician_steps`.
- API `POST /runs/{id}/close {by, note}`; CLI `tech close RUN --by NAME --note …`.
- **Tests:**
  - closing with no technician step → refused;
  - close → DONE with the `user_in_groups` and `message_sent` probes;
  - a failing probe → back to `ESCALATED`;
  - `can_save_playbook` is true;
  - `from_run` kind is `technician_run`;
  - **L1** end to end (decision 12).

**As built:**

- **`runner.technician_close(rt, run_id, by, note)`:**
  - `check_closable` first: the run must be ESCALATED with at least one verified console step. The API
    calls it before the 202.
  - The run then goes `ESCALATED → CLOSING`, with `by` and `note` on that state event, and
    `close_out(definition=no_playbook_dod(plan), note=…)` runs over an in-memory plan of the technician's
    steps.
  - The outcome is `DONE`, or `ESCALATED` (kind `dod`) with the failed probes.
  - `TRANSITIONS[ESCALATED] = {CLOSING}` is the only exit from ESCALATED, and only this function takes it.
- **A group add's DoD is `message_sent` only.** `no_playbook_dod` adds outcome probes only for actions in
  `dod.NO_PLAYBOOK_PROBES` (today a file restore). `user_in_groups` needs `$plan.mirrored_groups`, which a
  console plan doesn't have, and the step's own verify already read the membership back. The plan's "close
  → `user_in_groups` + `message_sent`" was therefore wrong. Adding console-friendly probe arguments is
  left for when a DoD needs them.
- **`close_out(definition=, note=)`:** the note reaches Sonnet as `technician_note`, and the close-out
  instructions say it belongs in the PSA note, not the reply.
- **Found while building, fixed:**
  - `library.successful_run` required an `approval` event, which a technician run never has. Its
    playbook would have been stuck at A2. Now a technician-verified step counts as a person behind the
    change.
  - `from_run` assumed a `plan` event, but a console run whose investigation failed has none.
  - `from_run` now passes on only `READS` tools, so a technician's `directoryObjects.get` can't become a
    `must_investigate` item that `playbook_check` would reject.
  - It also carries the close note as a (trusted) technician note.
- **`can_save_playbook`:** DONE, at least one verified write, and (no playbook, or any technician step).
- **The snapshot's `technician_steps`** carries each console step's decision, rule, actor, params and
  status, with `blocked` for a gate block.
- **API and CLI:** `POST /runs/{id}/close {by, note}` returns 202 and runs the close-out as a background
  task, like an approval; it's 409 if the run isn't closable. The CLI is `tech close RUN --by NAME
  [--note …]`.
- **Eval harness:** `run_case` is split into `offline()` (a seeded FakeTenant and Runtime, as a context
  manager) and `start_ticket(off, case)`, so a test can run several tickets in one tenant. `run_case`
  behaves as before.

**Tests:**

- `test_technician.py` (+6):
  - close without a console step, or on a non-ESCALATED run → refused;
  - close → `CLOSING` → `DONE`, with `message_sent` ✔, the reply and PSA note sent, the time entry, and
    the note in Sonnet's context;
  - a failed DoD → back to `ESCALATED` (kind `dod`);
  - a closed console run → `can_save_playbook`, `successful_run`, and `from_run` with kind
    `technician_run`, the note, and no confirming reads;
  - the snapshot's `technician_steps` (verified, and blocked);
  - the close route (409, then 202 → DONE).
- **`test_learning.py` L1:**
  1. Jordan's ticket escalates with no playbook.
  2. `tech step` group.addMember → verified, then close → DONE.
  3. The draft is a `technician_run` with the note; it's approved at A3.
  4. Luis's ticket runs under `site.grant_access` v1 with `auto/autonomy.a3.write.reversible`, no
     approval, and DONE.

  Zero bypasses on both runs.

**Result:** `uv run pytest`: 483 passed. `ruff` is clean. No live run.

## Part 3: audit-log learning and L2

- `graph/reads.py`: `directory_audits(graph, since, target_ids)` in `CONFIRM_READS` (not the
  investigator's).
- `learning/audit_map.py`: `MAPPING`, `map_entry(entry) -> ObservedStep | Unmapped` (decision 9).
- `learning/sources.py`: `from_audit_log(store, graph, run_id, since, until)`. It filters by window,
  observed IDs and a user initiator, maps the entries, runs the confirming read per step (decision 10),
  and returns `(DraftSource, unmapped)`.
- `core/store.py`: the `audit_observed` event type.
- `api.py`: `POST /runs/{id}/audit-draft` (decision 11), plus `audit` in the snapshot.
- `core/config.py`: `audit_poll_s` (30) and `audit_wait_s` (900).
- `tests/fake_tenant.py`: `directoryAudits` with `$filter` on `activityDateTime ge` and the
  `targetResources/any` match, shaped as Part 0 records.
- **Tests:**
  - mapped plus unmapped entries → only the mapped actions are drafted, and the unmapped are listed;
  - an app-initiated entry and one outside the window are ignored;
  - an undone change → not learned;
  - a timeout → `audit_observed {timed_out}`;
  - **L2**.

**As built:**

- **`learning/audit_map.py`:**
  - `relevant(entry, since, until, observed)` applies Part 0's filter: a person (`initiatedBy.user`), not
    a `Read`, in the window, touching an observed object.
  - `map_entry(entry)` returns an `ObservedStep` (actor `technician:{initiator UPN}`, with a rationale
    naming the activity and time) or an `Unmapped(activity, at, by, reason, targets)`. The `Unmapped`
    carries names and IDs only, never raw values.
  - It maps "Add member to group", "Remove member from group", "Add owner to group", "Update user"
    (JobTitle, Department, DisplayName, UsageLocation and AccountEnabled; any other field makes the entry
    unmapped) and "Disable account".
  - **"Invalidate all refresh tokens" isn't mapped.** Nothing readable confirms it afterwards (decision
    10), so it's listed as "can't learn".
- **`reads.directory_audits(graph, since, target_id)`** applies both filters. It is **not** a registered
  read: raw entries can carry device tokens (Part 0), so they're never recorded as a `tool_call` or shown
  to a model.
- **`sources.from_audit_log(store, graph, run_id, since, until)` → `AuditFindings(source, mapped,
  unmapped, undone)`:**
  - "observed" means every object the run's reads saw except licence SKUs, queried one ID at a time and
    merged by entry ID;
  - each mapped step gets one confirming read through the investigator's own read functions
    (`users.memberOf`, `users.ownedObjects`, `users.get`). Its result is the step's `verified`, and a
    change that's gone is `undone`;
  - `from_run`'s triage, plan, reads and findings handling moved into a shared `_run_context`, so both
    sources reduce the run the same way.
- **`runner.audit_draft(rt, run_id, by)`:**
  - the window starts when the run escalated (its last ESCALATED state event);
  - it records `audit_observed` (`waiting` → `found` with mapped/unmapped/undone, or `timed_out`), then
    drafts if anything mapped;
  - it polls every `Runtime.audit_poll_s` (30) up to `audit_wait_s` (600, per Part 0), with one more poll
    after the first find for stragglers;
  - the run stays ESCALATED, because learning a playbook doesn't close the ticket.
- **The library:** `approve(..., source="audit_log", source_run_id=…)` checks the YAML against the actions
  in that run's last `audit_observed`, exactly as run sources are checked against their verified steps. The
  ceiling caps it at A2 until a run on it succeeds.
- **API and CLI:**
  - `POST /runs/{id}/audit-draft {by}` returns 202 and runs as a background task (`escalate=False`).
  - The snapshot gains `audit` (the last `audit_observed`) and `playbook_draft` (the last
    `playbook_drafted`), for Day 21's waiting state and review.
  - `tech audit-draft RUN --by NAME [--autonomy A1|A2]` prints what was learned, what was undone and what
    can't be learned, then saves on confirmation. It shares `review_and_save` with `save-playbook`.
- **FakeTenant:** `directoryAudits` with both filters, plus `audit_add_member`, `audit_update_user` and
  `audit_noise` builders shaped exactly like the spike's entries: the MFA service's token-bearing "Update
  user", a password reset, a passkey read, and the `GroupsODataV4_Get` trap.
- **Found while testing:**
  - FakeTenant compared times as strings, and `…17.9Z` sorts before `…17Z`. It now parses them.
  - Day 19's `test_an_audit_log_draft_is_capped_at_a2` approved an audit_log playbook with no audit
    evidence. That's now refused, and the test pins the refusal; L2 covers the A2 cap.

**Tests:**

- `test_audit.py` (13):
  - mapping a group add and a title change;
  - unmapped with reasons, with no token leaking;
  - relevance: person, app, the trap, read, window, unobserved, our own app;
  - a draft from the mapped change, with the password reset listed, no token in any event, and the run
    still ESCALATED;
  - an undone change → not learned;
  - nothing found → `timed_out` after 21 polls;
  - one straggler poll;
  - a change from before the escalation doesn't count;
  - a non-ESCALATED run → refused;
  - the route (409, 202 → snapshot);
  - the CLI output.
- **`test_learning.py` L2:**
  1. Jordan's ticket escalates.
  2. An admin-center add, with noise, is drafted as `audit_log` with only `group.addMember` (the reset is
     listed).
  3. A3 → refused, and a widened YAML → refused; A2 is approved.
  4. Luis's ticket → `approve/autonomy.unproven`, approved → DONE.
  5. Then A3 is allowed.

  Zero bypasses.

**Result:** `uv run pytest`: 497 passed. `ruff` is clean. No live run.

## Part 4 (done): live, 2026-10-14

**Console half (runs 21-23):**

- **Run 21**, "Please give Jordan Lee access to the Northwind Operations site.":
  - Intake said `unknown`, so no playbook applied.
  - **Escalated on the 60 s no-playbook timeout**, after the investigator had already found the answer
    ("add Jordan to the Northwind Operations M365 group"). `files.search` alone took about 39 s across the
    six team sites. Worth a look before the demo: it's the slowest read, and Day 18's Sophie run also
    leaned on it.
  - Then:
    - `tech step 21 group.addMember …`: `approve/technician.self`, verified in 3 s;
    - `tech close 21 --note …`: `message_sent` ✔, the note was in the PSA note, a 4-minute time entry,
      DONE.
  - **The reply quality is poor:** "Hi tgarcia … A user was added to the requested group". It names no
    one, because the console step's params are bare GUIDs (no `groupName`), and it repeats the
    onboarding prompt's "no sign-in link" boilerplate.
- **The draft** passed every check on the first try, but needed review. A person approving would catch
  all three of these:
  1. It was saved as `intent: unknown`: the run had no plan, and intake said `unknown`. Renamed to
     `site.grant_access`.
  2. `must_investigate` had 7 reads, including `files.search` (39 s), `users.list`, `users.get` and
     `users.licenseDetails`. It was trimmed to 3 (`users.search`, and `users.memberOf` for the subject and
     for the manager or peers).
  3. **`user_in_groups: {groups: "$plan.mirrored_groups"}`** came from the onboarding example. The probe
     only checked a newly created user, so it would have failed every run, and at A3 auto-demoted on the
     first one.
  - **Fixed (`89ecf5d`):** with no `user.create`, it checks each group add's own user.
  - Approved at A3 (`technician_run`), with the edits re-checked.
- **Run 22**, "Can Luis Ortega get into the Northwind Operations site too?":
  - Intake → `site.grant_access`, and the run used v1 (not no-playbook).
  - **Escalated by the playbook's own gotcha:** neither Luis's manager (Dennis, Facilities) nor a same-role
    peer is in the group, and "Field Operations" is a look-alike group. That's correct on this data: the
    caution came from the drafted playbook, and it held.
- **Run 23, Omar Haddad (whose manager Kevin is in the group): hands-free.**
  - Intake → `site.grant_access`.
  - `auto/autonomy.a3.write.reversible`, verified in 6.5 s.
  - **`user_in_groups: in all 1` ✔ (the fix, live)**, and `message_sent` ✔.
  - No approval, 1 minute, DONE.

**Audit half (run 24):**

- "Jordan Lee has been promoted, please change his job title to Lot Supervisor.": no playbook →
  `user.update` `approve/no_playbook.approve` → rejected by tgarcia → ESCALATED.
- You changed the title in the admin center at 16:10:52 UTC. `tech audit-draft 24` found it at 16:12:23
  (`waiting` → `found`, about 90 s). It learned exactly `user.update {userId: Jordan, jobTitle: "Lot
  Supervisor"}`, with no noise kept. The drafter named the intent `user.update_title`.
- **Same trap:** the draft's DoD used `user_property`, which also only checked a created user.
- **Fixed (`f482044`):**
  - with no `user.create`, it checks each `user.update` step's own fields;
  - `user_exists` and `user_has_sku`, which really are new-user-only, are now rejected by `playbook_check`
    unless the playbook allows `user.create`, so the drafter gets a retry with the reason;
  - `tap_active` already falls back to the subject, so it isn't affected.
- Approved unchanged at A2 (A3 refused: "needs a successful approved run"). It's in `unproven()`, so its
  next run needs one approval.

**Learned records:** `site.grant_access-v1-run21.yaml` and `user.update_title-v1-run24.yaml`, in
`backend/tests/evals/learned/`.

**Reverted afterwards:** Jordan and Omar removed from Northwind Operations, and Jordan's title back to
"Lead Lot Attendant". Read back clean 10 s later. The local `data/tech.db` still holds the two learned
playbooks and runs 21-24; Day 22's reset clears them.

**Follow-ups this found (not done):**

- **The no-playbook 60 s bound vs `files.search`'s 39 s fan-out.** Options: drop `files.search` for
  non-file tickets via the guideline, run its six site searches concurrently, or raise the bound.
- **Console-step names:** fill `groupName` (and a user's name) from the transcript when a technician step
  is built, so the approval card, the close-out reply and the PSA note name people and groups, not GUIDs.
- **The close-out prompt is onboarding-shaped** ("no sign-in link to send"). It needs per-action wording,
  like `REPLY_NOTES`.
- **The drafter over-collects `must_investigate`.** Its prompt should say the list is *mandatory on every
  run*, so it lists only reads every run needs.
- **The drafter copies onboarding's DoD args.** Now partly guarded (new-user probes). A probe list that
  says which probes suit which actions would help it choose.

## Part 4: the plan (as written before the live run)

1. Re-stage a no-playbook ticket that escalates (L1's wording) via the CLI.
2. `tech step` group.addMember Jordan → Northwind Operations. Then `tech close`, then
   `tech save-playbook --autonomy A3`. That's one Opus call.
3. A second run of the same ticket: no approval, DONE.
4. **Audit:** an escalated run. You add the member in the admin center, I run the audit draft, and it's
   approved at A2. Then reset the membership.

The results are recorded here.

## Verification

- `uv run pytest` and `ruff check backend` are green.
- The agent's gate decisions and every existing event payload are unchanged (eval cases 1-28).
- The bypass check covers technician actors.
- Live: a console step verified on the tenant, a technician-run playbook ran hands-free, and a real
  admin-center change was drafted from audit logs (or the recorded fallback, if the spike says so).

## Cut line (Build-W4)

Part 3 and Part 4's audit half are the first to move to Week 5, keeping Part 0's results. Parts 1-2 are
on the critical path for Day 21's "Do it myself" panel.
