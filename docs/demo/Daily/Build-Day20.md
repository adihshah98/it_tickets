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
| 1 | Technician steps: transcript from events, confirming read, gate's technician path, executor `actor`, bypass check | New path; agent runs unchanged |
| 2 | Technician close-out, wider `can_save_playbook`, learning case **L1** | Escalated runs can close via a technician |
| 3 | Audit-log learning: the read, `audit_map`, `from_audit_log`, the polling route, **L2** | New path; first to cut if behind |
| 4 | Live: one console step on the tenant, and an audit draft from a real admin-center change | None |

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

## Part 4: live (needs approval: tenant writes and model spend)

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
