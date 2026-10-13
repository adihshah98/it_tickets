"""Plan linter, rules 1-7 (Tech.MD §4.3). Runs as the investigator's output validator.

Each problem is one sentence the model can act on; a non-empty list goes back to it as ModelRetry.

  1. Schema:    the action is in the catalogue and its params validate; step ids are unique.
  2. Grounded:  every object ID in the params was observed during investigation, and every
                `$ref:` points to an earlier step it depends on (directly or through another step)
                and that produces that output. A file restore's drive and item came from files.search,
                and its version from that file's files.versions, and isn't the current one.
  3. In scope:  the action is allowed by a triaged intent's playbook, and a group add targets
                a group the agent may change (see policy.group_problem).
  4. Covers DoD:         every required definition_of_done item has a step that could satisfy it,
                         or is one the plan covers by listing it under needs_technician. Waived when
                         the plan pauses with questions_for_requester: it is partial on purpose (only
                         what is safe before the answer, or nothing), and the gate still gates each step.
  5. Must-investigate:   every must_investigate tool was actually called in the transcript.
  6. Questions resolved: every questions_to_resolve topic is answered (an assumption or a
                         confirm_on_approval entry), or the plan pauses with questions_for_requester.
  7. Ordering sane:      depends_on points only backwards and has no cycles.

Rules 4-6 use the playbooks, so they run only for a playbook-backed plan, not an escalation. Rule 6 is
deliberately lenient (keyword presence): the investigator has only two lint retries, so a false
rejection there is worse than a missed question the gate still gates.

No-playbook mode (no triaged intent has a playbook, Tech.MD §4.9) changes rules 3-6:
  3. the scope is policy.NO_PLAYBOOK_ALLOWED, at policy.NO_PLAYBOOK_RISKS, instead of allowed_actions;
  4. the definition of done is each step's own `expected` result (plus close-out's reply, which isn't a
     step), so every step needs a non-empty `expected`;
  5-6. skipped: there is no must-investigate list or questions to resolve.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from pydantic import ValidationError

from tech.core.models import GUID_RE, Plan, StepBase, parse_ref
from tech.execution.catalogue import ACTIONS, FOLLOWUP_ACTIONS, ActionSpec
from tech.graph.reads import Transcript
from tech.playbooks import ClientFacts, Playbook
from tech.safety.policy import NO_PLAYBOOK_ALLOWED, NO_PLAYBOOK_RISKS, group_problem

# Which action produces each kind of step output a later step can reference.
PRODUCES = {"id": {"user.create"}, "tap": {"tap.create"}}

# Rule 4: which plan action(s) could satisfy each definition_of_done probe. A required probe passes
# the linter if the plan has a step with one of its producing actions; the probe itself re-checks
# against live state at close-out. Probes not listed here are STRUCTURAL/OPTIONAL below.
DOD_PRODUCERS: dict[str, set[str]] = {
    "user_exists": {"user.create"},
    "user_property": {"user.create", "user.update"},
    "user_has_sku": {"license.assign", "followup.schedule"},
    "user_in_groups": {"group.addMember"},
    "tap_active": {"tap.create"},
    "message_sent": {"link.sendOneTime"},
    "account_disabled": {"user.update"},
    "sessions_revoked": {"sessions.revoke"},
    "auto_reply_set": {"mailbox.autoReply"},
    "forward_rule_active": {"mailbox.forwardRule"},
    "removed_from_groups": {"group.removeMember"},
    "ownership_transferred": {"group.addOwner"},
    "oob_confirmed": {"tap.create", "authMethod.delete"},  # a credential write means OOB was required first
    "old_methods_removed": {"authMethod.delete"},
    "followup_scheduled": {"followup.schedule"},
}
# Probes the plan satisfies by structure (a needs_technician entry), not by a write.
STRUCTURAL_DOD = {"needs_technician_listed"}
# Probes that apply only "if requested"; their absence is never a problem (the YAML notes them in a comment).
OPTIONAL_DOD = {"auto_reply_set", "forward_rule_active"}


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

    no_playbook = not playbooks
    allowed = set(NO_PLAYBOOK_ALLOWED) if no_playbook else allowed_actions(playbooks)
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
        if action == "file.restoreVersion":
            problems += [f"Step {sid}: {p}" for p in _file_problems(params, transcript)]

        # 3. in scope
        if no_playbook and action not in allowed:
            problems.append(f"Step {sid}: {action} needs a reviewed playbook, and this ticket type has none. "
                            f"Without one only {', '.join(sorted(allowed))} are possible: remove it, and if the fix "
                            "needs it, escalate and name it in missing_action.")
        elif action not in allowed:
            problems.append(f"Step {sid}: {action} is not allowed for this ticket type; remove it "
                            "(list anything it was meant to do under needs_technician or out_of_scope_requests).")
        elif no_playbook and (risk := _risk(spec, params)) and risk not in NO_PLAYBOOK_RISKS:
            problems.append(f"Step {sid}: this {action} is {risk}, which needs a reviewed playbook; without one "
                            "only reversible changes are possible. Remove it, or escalate.")
        if action == "followup.schedule" and (later := FOLLOWUP_ACTIONS.get(params.get("kind", ""))):
            if later not in allowed:
                problems.append(f"Step {sid}: the follow-up would run {later}, which is not allowed here.")
        if action == "group.addMember" and (problem := group_problem(params["groupId"], transcript, facts)):
            problems.append(f"Step {sid}: {problem[1]}.")

        # 4. (no playbook) the step's own expected result is its definition of done
        if no_playbook and not step.expected:
            problems.append(f"Step {sid}: set expected to what a verify read should show afterwards; with no "
                            "playbook, each step's expected result is how the run is checked as done.")

        earlier[sid] = action

    # 7. ordering sane (every step, referenced or not): backward deps only, no cycles
    problems += _ordering_problems(plan)
    # 4-6 need the playbook; an escalation returned above, and no-playbook mode covers rule 4 per step
    if playbooks:
        actions = {s.action for s in plan.steps}
        for playbook in playbooks.values():
            if not plan.questions_for_requester:
                problems += _dod_problems(playbook, actions)
            problems += _must_investigate_problems(playbook, transcript)
            problems += _question_problems(playbook, plan)
    return problems


def _risk(spec: ActionSpec, params: dict[str, Any]) -> str | None:
    """The catalogue's risk class for these params, or None if they don't validate (rule 1 says so)."""
    try:
        return spec.risk(spec.params.model_validate(params))
    except ValidationError:
        return None


def _file_problems(params: dict[str, Any], transcript: Transcript) -> list[str]:
    """Rule 2 for a file restore: the IDs aren't GUIDs, so they're checked against the file reads."""
    file, version = (params.get("driveId"), params.get("itemId")), params.get("versionId")
    if file not in transcript.files:
        return ["that driveId and itemId weren't in any files.search result; find the file with files.search."]
    if (versions := transcript.files[file]) is None:
        return ["read the file's version history with files.versions before choosing a version."]
    if version not in versions:
        return [f"version {version} isn't in this file's version history ({', '.join(versions)})."]
    if version == versions[0]:
        return [f"version {version} is the current version, so restoring it changes nothing; choose an earlier "
                "one, or ask the requester which one they mean."]
    return []


def _ordering_problems(plan: Plan) -> list[str]:
    """Rule 7: depends_on points only at earlier steps, and the dependency graph has no cycle.

    A dependency resolves to the first step with that id (duplicate ids are rule 1's concern), so a
    backward dep to the first occurrence isn't double-reported as forward here.
    """
    first: dict[str, int] = {}
    for i, step in enumerate(plan.steps):
        first.setdefault(step.id, i)
    problems: list[str] = []
    for i, step in enumerate(plan.steps):
        for dep in step.depends_on:
            if dep not in first:
                problems.append(f"Step {step.id}: depends_on {dep!r}, which is not a step in this plan.")
            elif first[dep] >= i:
                problems.append(f"Step {step.id}: depends_on {dep!r} comes later; put dependencies first.")
    return problems


def _dod_problems(playbook: Playbook, actions: set[str]) -> list[str]:
    """Rule 4: every required DoD item has a step whose action could satisfy it."""
    problems: list[str] = []
    for item in playbook.definition_of_done:
        name = next(iter(item))
        if name in STRUCTURAL_DOD or name in OPTIONAL_DOD:
            continue
        producers = DOD_PRODUCERS.get(name)
        if producers and not (producers & actions):
            makers = " or ".join(sorted(producers))
            problems.append(f"Nothing in the plan meets the definition of done '{name}'; add a {makers} step, "
                            f"or list it under needs_technician with a reason.")
    return problems


def _must_investigate_problems(playbook: Playbook, transcript: Transcript) -> list[str]:
    """Rule 5: every must_investigate tool was actually called."""
    return [f"The playbook requires investigating with {tool}, but it was never called; look it up before planning."
            for tool in dict.fromkeys(playbook.must_investigate_tools) if not transcript.called(tool)]


def _question_problems(playbook: Playbook, plan: Plan) -> list[str]:
    """Rule 6 (lenient): each questions_to_resolve topic appears in an assumption or a confirm field,
    unless the plan pauses for the requester (then every question is deferred to them)."""
    if plan.questions_for_requester:
        return []
    answered = " ".join([*plan.assumptions, *(f"{c.key} {c.question} {c.proposed}" for c in plan.confirm_on_approval)])
    answered = answered.casefold()
    return [f"The playbook asks to resolve '{topic}', but nothing in the plan answers it; state an assumption, "
            f"confirm it on the approval card, or ask the requester."
            for topic in playbook.questions_to_resolve if topic.casefold() not in answered]
