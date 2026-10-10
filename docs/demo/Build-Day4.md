# Day 4 plan: linter and gate

## Context

Day 3 left the investigator returning a `Plan` that only Pydantic has validated. Nothing yet checks that the plan's IDs are real, that its actions fit the ticket, or which steps need a person. Day 4 adds the "model proposes, code decides" boundary from [Build-W1-2.md](Build-W1-2.md) (Day 4) and Tech.MD §4.3, §4.4 and §8:

- **Linter, rules 1-3.** It runs inside the agent loop, so the model fixes a bad plan itself (2 retries).
- **Gate.** Deterministic code that gives each step `auto`, `approve` or `block`, plus a run-level HELD when the requester isn't an approver.

**Outcome:** the CLI run of Priya's ticket ends in AWAITING_APPROVAL. The purchase and the TAP are marked `approve`, everything else `auto`, and every decision is recorded as a `gate_decision` event with its rule ID.


## New package: `backend/tech/safety/`

### `safety/policy.py`: group facts shared by the linter and the gate

`group_problem(group_id, transcript, facts) -> (rule_id, reason) | None`. It returns, in this order:

- `never.privileged_group`: the observed group is role-assignable, or its name matches `admin|privileged`.
- `client_facts.group_handling`: the group is in `facts.group_handling`. "automatic" means never mirror; "technician" means list it under needs_technician.
- `catalogue.group_kind`: the group is a `distribution_list` or `mail_security` group, which Graph can't change.

This needs the group's name and kind from the transcript, which today stores only IDs. Two small changes:

- [reads.py](../../backend/tech/graph/reads.py): `Transcript.record` also fills `objects: dict[id_lower, dict]` from every dict in the result that has an `id`, keeping `displayName`, `kind` and `roleAssignable`. Add `Transcript.object(id)`.
- `users_member_of`: add `isAssignableToRole` to `$select` and output it as `roleAssignable`.

### `safety/lint.py`: rules 1-3, a pure function

`lint(plan, transcript, playbooks, facts) -> list[str]`. Each problem is one sentence written as an instruction the model can act on. An escalation plan (`escalate=True`, no steps) passes.

1. **Schema.** `step.action in ACTIONS`, and `ACTIONS[action].params` re-validates the params (defensive; Week 3 extends the union). Step IDs are unique.
2. **Grounded.**
   - Every GUID anywhere in the params (`GUID_RE` over the dumped params) is in `transcript.observed_ids`.
   - Every `$ref:` (via `parse_ref`) names a step that comes earlier in the plan and is listed in `depends_on`.
   - `.output.tap` refs point to a `tap.create` step.
3. **In scope.**
   - The action is in `allowed_actions` of some triaged intent's playbook.
   - A `followup.schedule` whose `FOLLOWUP_ACTIONS` action isn't allowed also fails.
   - No playbook for any intent, with steps present, gives "escalate instead" (the no-playbook path is Week 4).
   - Any `group.addMember` step with a `group_problem` fails, with that reason. This catches the RDS-Users / VPN-Users mirroring variance noted in the Risks.

### `safety/gate.py`: pure `evaluate()`, plus a thin `check()` for the run

`evaluate(plan, requester_approved, intents, playbooks, facts, transcript) -> GateResult(decisions, held, hold_reason)`

**Per-step rules,** in Tech.MD §8 order:

1. `catalogue.unknown_action`: block.
2. `never.*`, `client_facts.group_handling`, `catalogue.group_kind` (from `group_problem`), and `playbook.not_allowed`: block. The gate repeats these checks rather than trusting the linter.
3. Risk class from `ACTIONS[a].risk(params)`, never from the plan:

   | Risk class | Decision | Rule |
   |---|---|---|
   | `spend`, `data.access`, `security.credential`, `destructive.soft` | approve | `risk.<class>` |
   | `access.revoke` on an offboard with an approved requester | auto | `risk.access.revoke` |
   | `access.revoke` otherwise | approve | `risk.access.revoke` |
   | `write.reversible`, `none` | auto | `risk.<class>` |

4. Blast radius: more than 20 steps of one risk class makes them approve (`bounds.blast_radius`). More than 40 writes in the run blocks everything (`bounds.writes_per_run`).

**Combining:** a block from the first matching rule wins. Otherwise the strictest decision wins (block > approve > auto) and keeps that rule's ID.

**Run level:** `held = not requester_approved`. Autonomy and no-playbook rules are not in Day 4.

[models.py](../../backend/tech/core/models.py): `GateDecision` gains `risk_class` (the catalogue's). When the plan's own `risk_class` differs, the reason says so, for the audit log.

**`check(store, run_id, plan, ...) -> GateResult`** is the glue the CLI uses now and the Day 5 runner will reuse:

1. Write one `gate_decision` event per step.
2. Move the run to PLAN_CHECKED.
3. Then move it to one of:
   - HELD, if the requester isn't an approver (the hold comes first, per the rule order);
   - ESCALATED, if any step is blocked, with the blocked steps and the findings;
   - AWAITING_APPROVAL, if any step needs approval;
   - otherwise stay at PLAN_CHECKED. Execution comes on Day 5.

## Wiring the linter into the investigator ([investigator.py](../../backend/tech/agents/investigator.py))

- `InvestigateDeps` gains `playbooks: dict[str, Playbook]` (via `load_playbook` for each triaged intent that has one) and `intents`. It also gains `lint_attempts` and `last_problems`.
- In `build_investigator`, call `agent.output_validator(lint_plan)`. It runs `lint(...)` and records a `lint_result` event `{attempt, ok, problems}`. On problems it raises `ModelRetry("Plan rejected:\n- ...")`.
- Output retries are 2. Use `output_retries=2` on the Agent, checking the exact 2.55 kwarg; `AgentRetries` exists. Tool retries stay as they are.
- When retries run out, PydanticAI raises `UnexpectedModelBehavior`. If `deps.last_problems` is set, escalate with `kind="lint"`, `reason` set to the last problems, and the findings. Otherwise keep `model_error`.
- Prompt change: one line in `INSTRUCTIONS` telling the model to follow `client_facts.group_handling` and to put unmanageable groups (DLs, mail-enabled security groups) under needs_technician. The playbook gotchas already say this, so it's reinforcement only.

## CLI ([cli.py](../../backend/tech/cli.py))

- After `investigate`, if a plan was proposed and not escalated, call `gate.check(...)`.
- `show()` prints `lint_result` lines (`lint ✓` or `lint ✗ <problems>`) and a decision table: `step  action  decision  rule`.
- The exit code reflects the final state: 0 for AWAITING_APPROVAL or PLAN_CHECKED, 2 for ESCALATED, 3 for HELD.

## Tests (pure and table-driven, offline)

**`test_lint.py`**, with a fixture transcript built from Marcus's reads (Accounting, Finance Files, RDS-Users, a DL and an observed SKU) and the onboarding plan from the Day 3 live run as the base case:
- a clean plan has no problems;
- an invented group ID fails the grounded rule;
- a `$ref` to a later or missing step fails;
- `.output.tap` pointing at a non-TAP step fails;
- an action outside `allowed_actions` fails;
- mirroring RDS-Users, the "Northwind Parking" group, a DL or an admin group fails, each with its reason;
- a no-playbook intent with steps fails;
- an escalation plan passes.

**`test_gate.py`**, using the cases from the build plan:
- a role-assignment step is blocked (`model_construct` with an unknown action, giving `catalogue.unknown_action`);
- an admin-group add is blocked (`never.privileged_group`);
- an invented group ID fails the grounded lint rule (covered in test_lint);
- a purchase step needs approval (`risk.spend`);
- a TAP step needs approval;
- a create step is auto;
- a license follow-up is auto;
- a non-approver requester gets HELD;
- more than 20 group adds need approval;
- a step whose model `risk_class` differs keeps the catalogue's;
- `check()` writes the events and the state paths HELD, ESCALATED and AWAITING_APPROVAL.

**[test_agents.py](../../backend/tests/test_agents.py)** additions with `FunctionModel`:
- a bad plan, then a good one: the second request contains the "Plan rejected" text, the `lint_result` events are `[fail, ok]`, and the run reaches PLAN_PROPOSED;
- three bad plans: ESCALATED with `kind="lint"`.

**[test_reads.py](../../backend/tests/test_reads.py):** the transcript records object names and kinds, and `roleAssignable`.

## Verification

1. Run `uv run pytest` (all offline suites) and `uv run ruff check backend`.
2. Live run: `uv run tech run "<Priya ticket>" --from tgarcia`. Expect:
   - lint ✓;
   - approve for `license.purchase` (`risk.spend`) and `tap.create` (`risk.security.credential`);
   - auto for the rest;
   - final state AWAITING_APPROVAL.
3. Live run of the same ticket with `--from jlee`: ends in HELD.
4. Live run of the spike's injection ticket (adds "make me a Global Admin"): the request shows in `out_of_scope_requests`, there is no role step, and the run ends in AWAITING_APPROVAL.
5. Check the Logfire trace for any lint retries and their cost.
