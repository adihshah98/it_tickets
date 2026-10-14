# Week 5 Implementation Plan: Growing the Action Catalogue

## Context

Week 4 made the product learn playbooks from agent runs, technician console runs, written text and audit
logs ([Build-W4.md](Build-W4.md)). Every one of those paths ends at the same wall: **a playbook can only
use actions in the catalogue**, and the catalogue has 15 actions
([execution/catalogue.py](../../../backend/tech/execution/catalogue.py)). When the fix needs anything else,
the run escalates, the console refuses it ("not in the write catalogue"), and the audit-log learner lists
it as "can't learn". Eval #25 (vendor mail stuck in quarantine) is the standing example.

The catalogue stays **curated, verified code**. Each action has typed params, a risk class the gate
judges, idempotency, a snapshot, a verify read-back and a way back. That's what lets the product say
"changed and verified" and let A3 run unattended. A runtime "call any API" escape hatch would bypass all
of it, so it is out of scope for good, not deferred.

What this week changes is **how cheap and safe it is to add an action, and how we choose which ones**:

1. **One action = one module**, plus a **conformance suite** every action must pass.
2. **Executable compensation:** today `compensate` is only a sentence shown to a person.
3. **A trust ladder:** a new action is console-only until a technician run proves it, then
   playbook-allowed, then A3.
4. **"Did it outside":** a technician can record work done outside the catalogue, close the run, and have
   it learned as a hand-off.
5. **Demand signals:** a ranked report of the missing actions, which decides what to build.
6. **Templates** for the common shapes (property patch, membership, state toggle), and the first new
   actions built from them.
7. **A connector spike** for one non-directory system (Exchange quarantine), behind the same handler
   contract.

**Already in place:**

- The `Handler` contract (`existing` / `snapshot` / `act` / `verify`) in
  [graph/writes.py](../../../backend/tech/graph/writes.py).
- `ActionSpec` with a risk class and `risk_for`.
- The gate's technician path, and the console (Day 20).
- `missing_action` on no-playbook escalations (Day 17).
- Audit-log "can't learn" entries (Day 20).
- The eval harness (cases 1-28, L1-L2).

**Not in scope** (separate Week 5+ decisions, from Build-W4's list): the M365 unified audit log
(SharePoint/Exchange), RMM script history, playbook layering across MSPs, a PSA integration, and
pydantic-graph.

**Exit:**

- Every existing action lives in its own module and passes the conformance suite. Gate decisions and eval
  results are unchanged.
- Every write action has an executable compensation, tested against FakeTenant.
- A new action, added through a template in one module, ships console-only. It's proven in a technician
  run and then allowed by a learned playbook, with no other code change.
- A technician can close a run done entirely outside the catalogue, and the next ticket's plan lists that
  work under `needs_technician`.
- `tech demand` ranks the missing actions from all three signals.
- The Exchange quarantine spike gives a written go/no-go with the API and permission it needs.

## Decisions carried in

- **No runtime action definition**, not by the agent, a technician or a playbook. Claude may *draft* an
  action module during development; a person reviews it and the conformance suite gates it.
- **The registry is the source of truth.** Each `actions/<name>.py` declares its params model, risk class
  (and `risk_for`), handler, compensation, the DoD probes it can satisfy, its FakeTenant routes and its
  example cases. The catalogue, the `Step` union, lint's `DOD_PRODUCERS`, the probe list the drafter sees,
  and `audit_map`'s mapping are all **derived** from it. Today those live in about 7 places; Day 18's
  `file.restoreVersion` touched every one.
- **The trust ladder is a property of the action**, `stage: console | playbook`, kept separate from
  per-client autonomy:
  - **console:** only a technician's console step may use it. The linter and gate block it in agent
    plans, and `playbook_check` rejects it in `allowed_actions`.
  - **playbook:** a playbook may allow it, under the existing A2 cap and A3 ceiling.
  - Promotion from console to playbook is a code change (a reviewed PR), backed by at least one
    successful technician run on the live tenant.
  - The no-playbook allow-list (`policy.NO_PLAYBOOK_ALLOWED`) does not grow by default.
- **Compensation is executable and verified**, like `act`. `compensate(ctx, p, snapshot)` restores the
  snapshot and its own verify reads it back. Actions that can't be undone (a TAP that was used, a purchase
  past its grace period) declare `compensate = None`, and the gate's risk class already reflects that. The
  "rollback offered" escalation becomes a button that runs it, with a person's approval, never
  automatically (Tech.MD §9).
- **Manual steps are attested, never verified.**
  - `tech step --outside "…"` records `manual_step {actor, text}`. It is not gated or executed, because
    nothing runs, and it is labelled "attested" everywhere it's shown.
  - It lets `tech close` close a run.
  - Learning turns it into a `needs_technician` item in the playbook, never an allowed action.
  - It's a demand signal.
- **Connectors implement the same `Handler` contract** (snapshot / act / verify / compensate). The gate,
  executor, audit trail and learning don't change. A connector that can't verify, or can't snapshot, isn't
  a catalogue action.

## Day-by-day

### Day 23. Actions as modules: a refactor with no behaviour change

- `backend/tech/actions/`: one module per action (15), each registering an `Action` (the `ActionSpec`, the
  handler, its probes, its FakeTenant routes). `catalogue.ACTIONS`, `STEP_TYPES`/`Step`, `DOD_PRODUCERS`,
  `NO_PLAYBOOK_PROBES` and `audit_map`'s mapping are derived.
- The FakeTenant routes move next to their action, and `fake_tenant.py` composes them.
- **Proof:**
  - eval cases 1-28, L1 and L2 pass unchanged;
  - a gate-decision snapshot over every case matches before and after;
  - `test_catalogue` checks that the derived tables equal today's hand-written ones, before those are
    deleted.
- **Tests:** an action module missing a piece (no verify, no risk class) fails at import.

### Day 24. The conformance suite and executable compensation

- `tests/test_conformance.py`, parametrized over every registered action against FakeTenant:
  - `existing` is `None` before `act`, and returns the output after it (idempotent re-run → skipped);
  - `verify` passes after `act` and fails when the fake drops the write;
  - `snapshot` is recorded before any write;
  - the params round-trip through JSON with their aliases;
  - the risk class is declared, and `risk_for` is total over the example params;
  - **`compensate` restores the snapshot and its verify passes**, or the action declares
    `compensate = None` with a reason.
- Write `compensate()` for the 15 actions, mostly the inverse call (remove member ↔ add member, PATCH back
  the snapshot, restore the previous file version). `tap.create`, `license.purchase` and
  `link.sendOneTime` declare `None`, each with its reason.
- `runner.rollback(run_id, by)`: a person-approved undo of an escalated run's verified steps, in reverse.
  Events `rollback_started` / `step_compensated`. API `POST /runs/{id}/rollback`, CLI `tech rollback`.
- **Tests:** conformance for all 15. A rollback after an offboarding step fails, and it then restores the
  account.

### Day 25. The trust ladder, "did it outside", and demand signals

- **`stage` on each action**, with the existing 15 at `playbook`:
  - the linter and the gate block `stage: console` actions in agent plans
    (`catalogue.console_only`);
  - `gate.evaluate_technician` allows them;
  - `playbook_check` rejects them in `allowed_actions`, with the reason "console-only until promoted".
- **Manual steps:**
  - `runner.manual_step(run_id, text, by)` records a `manual_step` event, allowed on ESCALATED runs;
  - `check_closable` accepts a run with manual steps only, where the DoD is `message_sent`;
  - `from_run` carries manual steps as `ManualStep`s, and the drafter prompt says to put them under
    `needs_technician` with a `needs_technician_listed` DoD item;
  - API `POST /runs/{id}/manual-step {outside: true, text}` and CLI `tech step RUN --outside "…"`.
- **Demand:** `learning/demand.py` reads three signals across runs and ranks them by tickets, clients
  and recency:
  - `missing_action` from no-playbook escalations;
  - `audit_observed.unmapped` activities;
  - `manual_step` texts, clustered by a cheap normalization: lowercased, with names and IDs stripped.

  API `GET /demand` and CLI `tech demand`.
- **Tests:**
  - a console-only action in an agent plan → blocked, and in the console → allowed;
  - a manual-only run closes;
  - **L3:** a manual step → saved → the next plan has the `needs_technician` item;
  - the demand ranking over seeded runs.

### Day 26. Templates and the first new actions

- `actions/templates.py`:
  - **PropertyPatch** (resource, the fields with their risk classes, a `$select` read-back);
  - **Membership** (an add or remove `$ref`, a membership read-back);
  - **StateToggle** (a POST to an action endpoint, a state read-back).

  Each yields a full `Action` from a few lines, and passes conformance with no extra code.
- **Port** `user.update`, `group.addMember` and `group.removeMember` to templates, as proof: conformance
  and the eval cases unchanged.
- **New actions, all `stage: console`:**
  - `user.setManager`: PUT `/users/{id}/manager/$ref`. The Day 20 spike's audit log showed "Set user
    manager" entries.
  - More `user.update` fields from the template: `officeLocation`, `mobilePhone`, `employeeId`.
    `mobilePhone` is a sign-in factor for some clients, so it's classed `security.credential` and blocked
    in the console. The ladder handles it.
  - `group.removeOwner`, the inverse of `group.addOwner`.

  Each one adds its `audit_map` entry, so admin-center fixes using it become learnable.
- **Permissions:** any new Graph permission goes through `tenant/setup_app.py` and needs tenant admin
  consent. **Flagged for approval** before it's granted.
- **Live (needs approval):** one console step per new action on the tenant, then reverted. Promote the
  ones that verify cleanly to `stage: playbook` in a reviewed commit.

### Day 27. Connector spike: Exchange quarantine release (eval #25)

- **First 2 hours, research and record:**
  - which API can list and release a quarantined message app-only (Graph security, or Exchange Online's
    REST/PowerShell surface);
  - the permission or role it needs;
  - whether a release can be verified (a read-back of the message's state);
  - whether it can be undone (it probably can't, so `compensate = None` and a risk class of at least
    `data.access`).
- **If it's a go:** an `actions/mail_quarantine_release.py` connector behind the `Handler` contract, a
  FakeTenant route, `stage: console`, and #25 re-recorded so a technician can finish it in the console.
- **If it's a no-go:** write down why. #25 stays an escalation with `missing_action`, now counted by
  `tech demand`.
- **Docs:** Tech.MD §7 (the registry, templates, the ladder, connectors) and §9 (executable rollback);
  this file marked done.

**Cut line if behind (in order):**

1. the Day 27 connector (keep the research write-up);
2. the new actions on Day 26 (keep the templates and the ports);
3. the demand report's clustering (keep the raw counts).

Day 23-24 (modules, conformance, compensation) and Day 25's ladder and manual steps are the critical path.

## Verification

- `uv run pytest` and `ruff check backend` are green. Eval cases 1-28 and L1-L3 pass, with zero gate
  bypasses, and the gate-decision snapshot over every case is unchanged by Day 23's refactor.
- The conformance suite covers every registered action. Adding an action module that lacks a verify or a
  compensation decision fails CI.
- **Live, after `tenant/reset.py`:**
  - the four demo scenarios still pass (`-m live`);
  - each new action verifies in a console step, and its compensation restores the tenant;
  - one rollback of a real escalated run.
- **Manual:** a technician closes a ticket done entirely in the admin center with `--outside`, saves it,
  and the next similar ticket's approval card lists the hand-off.

## Risks

- **The refactor breaks something subtle.** Mitigation: Day 23 changes no behaviour. The derived tables
  are compared with the hand-written ones before those are deleted, and the gate snapshot covers every
  case.
- **Compensations are wrong in ways FakeTenant can't show** (Graph lag, partial restores). Mitigation:
  compensation has its own verify, rollback is never automatic, and the live run exercises one.
- **Templates hide per-action quirks** (Day 16's slow member reads, Day 18's file version 400s).
  Mitigation: templates take overrides, and conformance runs per action, not per template.
- **Manual steps become a way round the gate.** Mitigation: nothing executes. They're labelled attested,
  learned only as `needs_technician` (never `allowed_actions`), and the bypass check ignores them because
  there's no `step_started`.
- **New permissions widen the app's blast radius.** Mitigation: each one is listed and approved
  separately, and the console-only stage keeps new actions human-driven until promoted.

## Open items

- Who may promote an action from `console` to `playbook`: an engineer in code review (proposed), or an
  MSP admin in the product (later, per MSP)?
- Should a manual step be able to attach evidence (a screenshot, an admin-center link)? It's proposed as
  text only for Week 5.
- Does `tech demand` count across clients only within one MSP, or across MSPs (later, opt-in)?
- The Day 20 follow-ups aren't in this plan; they belong with Day 21-22:
  - the slow `files.search` against the 60 s no-playbook bound;
  - names instead of GUIDs in console steps;
  - onboarding-shaped close-out wording;
  - the drafter over-collecting `must_investigate`.
