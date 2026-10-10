"""Plan linter, rules 1-3 (Tech.MD §4.3). Runs as the investigator's output validator.

Each problem is one sentence the model can act on; a non-empty list goes back to it as ModelRetry.
Rules 4-7 (definition of done, must-investigate, questions, ordering) arrive in Week 3.

  1. Schema:    the action is in the catalogue and its params validate; step ids are unique.
  2. Grounded:  every object ID in the params was observed during investigation, and every
                `$ref:` points to an earlier step it depends on (directly or through another step)
                and that produces that output.
  3. In scope:  the action is allowed by a triaged intent's playbook, and a group add targets
                a group the agent may change (see policy.group_problem).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from pydantic import ValidationError

from tech.core.models import GUID_RE, Plan, StepBase, parse_ref
from tech.execution.catalogue import ACTIONS, FOLLOWUP_ACTIONS
from tech.graph.reads import Transcript
from tech.playbooks import ClientFacts, Playbook
from tech.safety.policy import group_problem

# Which action produces each kind of step output a later step can reference.
PRODUCES = {"id": {"user.create"}, "tap": {"tap.create"}}


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def params_of(step: StepBase) -> dict[str, Any]:
    params = getattr(step, "params", {})
    return params.model_dump(mode="json", by_alias=True) if hasattr(params, "model_dump") else dict(params)


def allowed_actions(playbooks: dict[str, Playbook]) -> set[str]:
    return {a for p in playbooks.values() for a in p.allowed_actions}


def lint(plan: Plan, transcript: Transcript, playbooks: dict[str, Playbook], facts: ClientFacts) -> list[str]:
    problems: list[str] = []
    if plan.escalate and not plan.steps:
        return problems
    if plan.steps and not playbooks:
        return ["There is no playbook for this ticket's intent, so propose no steps: set escalate=true and explain."]

    allowed = allowed_actions(playbooks)
    earlier: dict[str, str] = {}  # step id -> action, for steps before the current one
    needs: dict[str, set[str]] = {}  # step id -> every step it depends on, directly or not

    for step in plan.steps:
        sid, action = step.id, step.action
        if sid in earlier:
            problems.append(f"Step id {sid!r} is used twice; give every step a unique id.")
        ancestors = {d for dep in step.depends_on for d in {dep, *needs.get(dep, ())}}
        needs[sid] = ancestors

        # 1. schema
        spec = ACTIONS.get(action)
        if spec is None:
            problems.append(f"Step {sid}: {action!r} is not an action the executor has; remove it.")
            earlier[sid] = action
            continue
        params = params_of(step)
        try:
            spec.params.model_validate(params)
        except ValidationError as exc:
            problems.append(f"Step {sid}: params don't fit {action}: {exc.errors()[0]['msg']}.")

        # 2. grounded
        for guid in sorted({g.lower() for g in GUID_RE.findall(json.dumps(params))}):
            if not transcript.observed(guid):
                problems.append(f"Step {sid}: {guid} was not in any tool result. Use only IDs you observed; "
                                "look the object up first if you need it.")
        for text in _strings(params):
            if (ref := parse_ref(text)) is None:
                continue
            target, output = ref
            if target not in earlier:
                problems.append(f"Step {sid}: {text} must refer to an earlier step in the plan.")
            elif target not in ancestors:
                problems.append(f"Step {sid}: uses {text}, so add {target!r} to its depends_on.")
            elif earlier[target] not in PRODUCES.get(output, set()):
                makers = " or ".join(sorted(PRODUCES.get(output, set()))) or "no action"
                problems.append(f"Step {sid}: {text} must point to a {makers} step, not {earlier[target]}.")

        # 3. in scope
        if action not in allowed:
            problems.append(f"Step {sid}: {action} is not allowed for this ticket type; remove it "
                            "(list anything it was meant to do under needs_technician or out_of_scope_requests).")
        if action == "followup.schedule" and (later := FOLLOWUP_ACTIONS.get(params.get("kind", ""))):
            if later not in allowed:
                problems.append(f"Step {sid}: the follow-up would run {later}, which is not allowed here.")
        if action == "group.addMember" and (problem := group_problem(params["groupId"], transcript, facts)):
            problems.append(f"Step {sid}: {problem[1]}.")

        earlier[sid] = action
    return problems
