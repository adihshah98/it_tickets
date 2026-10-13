"""Policy gate (Tech.MD §8): a decision per step, auto | approve | block, plus a run-level hold.

Plain code, evaluated after the linter. Risk classes come from the catalogue, never from the plan.
Rule order: requester hold, then never/catalogue blocks, then (rule 3) the no-playbook allow-list, then
the autonomy level (and the unproven rule below), then risk-class rules, then the blast-radius guard.
The first rule that blocks wins; otherwise the strictest decision wins. Autonomy is per intent per client (§8, from
PlaybookLibrary.autonomy()): A1 blocks every write (a technician checklist), A2 leaves the risk-class
rules in force, and A3 auto-approves reversible/revoke writes but never spend, data.access or
security.credential. On an MFA reset a credential step is
labelled `mfa.oob_required`: it still needs approval, and runner.accept refuses to run it until
out-of-band confirmation is recorded (the gate runs before anyone can confirm, so it can't block on it).

Rule 3, a run with no playbook (no triaged intent has one, Tech.MD §4.9): a step outside
policy.NO_PLAYBOOK_ALLOWED, or above policy.NO_PLAYBOOK_RISKS, is `block` (no_playbook.not_allowed);
every other step is `approve` (no_playbook.approve). It replaces the autonomy and risk-class rules, so no
autonomy level, A3 included, can make a no-playbook write automatic.

Unproven playbooks (Build-Day19): an intent whose current playbook was learned or written, not shipped,
has every write `approve` (autonomy.unproven) below A3. The risk-class rules alone would let A2 run a
reversible write unasked, so a learned playbook's A2 cap and its demotion to A2 would change nothing. A3
(earned by a successful approved run, PlaybookLibrary.ceiling) is what lets it run hands-free.

`evaluate` is pure. `check` records its result on a run: a gate_decision event per step, then the
next state. The Day 5 runner calls `check` too.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field

from tech.core.models import GateDecision, Plan, RiskClass, StepBase
from tech.core.store import RunState, Store
from tech.execution.catalogue import ACTIONS
from tech.graph.reads import Transcript
from tech.playbooks import ClientFacts, Playbook
from tech.safety.lint import allowed_actions, params_of
from tech.safety.policy import NO_PLAYBOOK_ALLOWED, NO_PLAYBOOK_RISKS, group_problem

BLAST_RADIUS = 20      # more writes than this of one risk class -> approve
MAX_WRITES = 40        # more writes than this in one run -> block (Tech.MD §9 bounds)

STRICTNESS = {"auto": 0, "approve": 1, "block": 2}
APPROVE_CLASSES: set[RiskClass] = {"spend", "data.access", "security.credential", "destructive.soft"}


@dataclass
class GateResult:
    decisions: list[GateDecision]
    held: bool = False
    hold_reason: str | None = None

    @property
    def blocked(self) -> list[GateDecision]:
        return [d for d in self.decisions if d.decision == "block"]

    @property
    def to_approve(self) -> list[GateDecision]:
        return [d for d in self.decisions if d.decision == "approve"]


@dataclass
class _Verdicts:
    """The decisions the rules give one step, in rule order."""

    items: list[tuple[str, str, str]] = field(default_factory=list)  # (decision, rule_id, reason)

    def add(self, decision: str, rule_id: str, reason: str) -> None:
        self.items.append((decision, rule_id, reason))

    def final(self) -> tuple[str, str, str]:
        for item in self.items:
            if item[0] == "block":
                return item
        return max(self.items, key=lambda item: STRICTNESS[item[0]])  # max keeps the first of equals


A3_WAIVES: set[RiskClass] = {"write.reversible", "access.revoke"}  # the only classes A3 auto-approves (§8)


def autonomy_level(intents: Sequence[str], autonomy: Mapping[str, str]) -> str:
    """The strictest autonomy level (A1 < A2 < A3) across the step's governing intents; A2 if unset."""
    levels = [autonomy.get(i, "A2") for i in intents] or ["A2"]
    return min(levels)  # "A1" < "A2" < "A3" as strings, and A1 is the strictest


def _risk_rule(risk: RiskClass, intents: Sequence[str], requester_approved: bool,
               autonomy: str = "A2") -> tuple[str, str, str]:
    # A3 waives approval for reversible/revoke writes, but never for the classes a person must see (§8).
    if autonomy == "A3" and risk in A3_WAIVES:
        return "auto", f"autonomy.a3.{risk}", f"A3: {risk} runs automatically for this client"
    rule = f"risk.{risk}"
    if risk == "security.credential" and "mfa.reset" in intents:
        # Approved like any credential step, and it can't run until runner.accept sees oob_confirmed.
        return "approve", "mfa.oob_required", ("resets a credential: needs out-of-band confirmation with the "
                                               "subject's manager, then a person's approval")
    if risk in APPROVE_CLASSES:
        return "approve", rule, f"{risk} always needs a person's approval"
    if risk == "access.revoke":
        if "user.offboard" in intents and requester_approved:
            return "auto", rule, "revoking a leaver's access, asked for by an approver"
        return "approve", rule, "revokes access outside an approved offboarding"
    return "auto", rule, f"{risk}: reversible, within the playbook"


def _step(step: StepBase, *, intents: Sequence[str], requester_approved: bool, allowed: set[str],
          no_playbook: bool, facts: ClientFacts, autonomy_levels: Mapping[str, str], unproven: Collection[str],
          transcript: Transcript) -> tuple[_Verdicts, RiskClass | None]:
    verdicts = _Verdicts()
    spec = ACTIONS.get(step.action)
    if spec is None:
        verdicts.add("block", "catalogue.unknown_action", f"{step.action} is not in the write catalogue")
        return verdicts, None

    params = params_of(step)
    if step.action == "group.addMember" and (problem := group_problem(params["groupId"], transcript, facts)):
        verdicts.add("block", *problem)
    typed = step.params if hasattr(step.params, "model_dump") else spec.params.model_validate(params)
    risk = spec.risk(typed)

    if no_playbook:  # rule 3
        if step.action not in NO_PLAYBOOK_ALLOWED:
            verdicts.add("block", "no_playbook.not_allowed", f"{step.action} needs a reviewed playbook first")
        elif risk not in NO_PLAYBOOK_RISKS:
            verdicts.add("block", "no_playbook.not_allowed", f"{risk} needs a reviewed playbook first")
        else:
            verdicts.add("approve", "no_playbook.approve", "no playbook for this request: every change needs approval")
        return verdicts, risk

    if step.action not in allowed:
        verdicts.add("block", "playbook.not_allowed", f"{step.action} is not in the playbook's allowed_actions")

    autonomy = autonomy_level(intents, autonomy_levels)
    if autonomy == "A1":  # the client runs this intent as a technician checklist; the agent writes nothing
        verdicts.add("block", "autonomy.a1", "this client reviews every step for this request (autonomy A1)")
    elif autonomy != "A3" and risk != "none" and (learned := [i for i in intents if i in unproven]):
        verdicts.add("approve", "autonomy.unproven", f"the {learned[0]} playbook is learned and not yet at A3: "
                                                     "every change needs approval")

    decision, rule, reason = _risk_rule(risk, intents, requester_approved, autonomy)
    if step.risk_class != risk:
        reason += f" (the plan said {step.risk_class}; the catalogue says {risk})"
    verdicts.add(decision, rule, reason)
    return verdicts, risk


def evaluate(plan: Plan, *, requester_approved: bool, intents: Sequence[str], playbooks: dict[str, Playbook],
             facts: ClientFacts, autonomy: Mapping[str, str], unproven: Collection[str],
             transcript: Transcript) -> GateResult:
    """`autonomy` is the per-intent levels from PlaybookLibrary.autonomy(); `unproven` the intents whose
    playbook is learned or written (PlaybookLibrary.unproven())."""
    allowed = allowed_actions(playbooks)
    judged = [(step, *_step(step, intents=intents, requester_approved=requester_approved, allowed=allowed,
                            no_playbook=not playbooks, facts=facts, autonomy_levels=autonomy, unproven=unproven,
                            transcript=transcript))
              for step in plan.steps]

    per_class = Counter(risk for _, _, risk in judged if risk is not None)
    for _, verdicts, risk in judged:
        if risk is not None and per_class[risk] > BLAST_RADIUS:
            verdicts.add("approve", "bounds.blast_radius",
                         f"{per_class[risk]} {risk} steps in one run (more than {BLAST_RADIUS})")
        if len(judged) > MAX_WRITES:
            verdicts.add("block", "bounds.writes_per_run", f"{len(judged)} writes in one run (limit {MAX_WRITES})")

    decisions = []
    for step, verdicts, risk in judged:
        decision, rule, reason = verdicts.final()
        decisions.append(GateDecision(step_id=step.id, action=step.action, decision=decision, rule_id=rule,
                                      reason=reason, risk_class=risk))
    hold_reason = None if requester_approved else "the requester is not on the client's approver list for this request"
    return GateResult(decisions, held=not requester_approved, hold_reason=hold_reason)


def check(store: Store, run_id: int, plan: Plan, *, requester_approved: bool, intents: Sequence[str],
          playbooks: dict[str, Playbook], facts: ClientFacts, autonomy: Mapping[str, str], unproven: Collection[str],
          transcript: Transcript, findings: Sequence[str] = ()) -> GateResult:
    """Gate a proposed plan: gate_decision events, PLAN_CHECKED, then HELD / ESCALATED / AWAITING_APPROVAL."""
    result = evaluate(plan, requester_approved=requester_approved, intents=intents, playbooks=playbooks,
                      facts=facts, transcript=transcript, autonomy=autonomy, unproven=unproven)
    for decision in result.decisions:
        store.append_event(run_id, "gate_decision", decision.model_dump(mode="json"))
    store.set_state(run_id, RunState.PLAN_CHECKED)

    if result.held:
        store.set_state(run_id, RunState.HELD, reason=result.hold_reason)
    elif result.blocked:
        store.set_state(run_id, RunState.ESCALATED, kind="gate",
                        reason="; ".join(f"{d.step_id}: {d.reason}" for d in result.blocked),
                        blocked=[d.step_id for d in result.blocked], findings=list(findings))
    elif result.to_approve:
        store.set_state(run_id, RunState.AWAITING_APPROVAL, steps=[d.step_id for d in result.to_approve])
    return result
