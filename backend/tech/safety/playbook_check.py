"""Code checks on a learned or written playbook (Build-W4 "Code checks are hard failures").

They run on the drafter's output (Week 4 Day 19, part 2) and again on the YAML a person approves, so an
edit at approval time can't widen what a playbook allows. Each problem is one sentence; any problem
rejects the playbook.

  - the YAML parses into a Playbook, declares the intent it is saved under, and round-trips;
  - every allowed action is in the write catalogue, every must_investigate tool is a read tool, and
    every definition_of_done name is a probe;
  - every required DoD item has an allowed action that could produce it (the linter's rule 4);
  - `never` has policy.required_never() for the risk classes the playbook allows;
  - with observed steps (a run it was learned from), every allowed action was observed in that run.

The autonomy ceiling isn't here: it depends on the store's runs (PlaybookLibrary.ceiling).
"""

from __future__ import annotations

from collections.abc import Collection

import yaml
from pydantic import ValidationError

from tech.core.store import RunState, Store
from tech.execution.catalogue import ACTIONS
from tech.execution.dod import PROBES
from tech.graph.reads import READS
from tech.playbooks.loader import Playbook, parse_playbook
from tech.safety.lint import dod_unmet
from tech.safety.policy import required_never


def check(text: str, intent: str, *, observed: Collection[str] | None = None) -> list[str]:
    """Every reason this playbook text can't be approved under `intent`. `observed` is the set of actions
    the source run verified, or None when there is no run behind it (a written playbook)."""
    try:
        playbook = parse_playbook(text, intent)
    except (yaml.YAMLError, ValidationError, ValueError, TypeError, AttributeError) as exc:
        return [f"The playbook doesn't parse: {exc}"]
    problems: list[str] = []
    if Playbook.model_validate(yaml.safe_load(yaml.safe_dump(playbook.model_dump()))) != playbook:
        problems.append("The playbook doesn't round-trip through YAML.")
    if not playbook.allowed_actions:
        problems.append("allowed_actions is empty; a playbook must allow at least one action.")

    unknown = [a for a in playbook.allowed_actions if a not in ACTIONS]
    problems += [f"allowed_actions has {a}, which is not in the write catalogue." for a in unknown]
    problems += [f"must_investigate has {t}, which is not a read tool." for t in playbook.must_investigate_tools
                 if t not in READS]
    problems += [f"definition_of_done has {name!r}, which is not a probe." for item in playbook.definition_of_done
                 if (name := next(iter(item))) not in PROBES]

    known = {a for a in playbook.allowed_actions if a in ACTIONS}
    problems += [f"Nothing in allowed_actions could meet the definition of done {name!r}."
                 for name in dod_unmet(playbook.definition_of_done, known)]

    have = {line.strip() for line in playbook.never}
    problems += [f"never must include: {line!r}" for line in required_never({ACTIONS[a].risk_class for a in known})
                 if line not in have]

    if observed is not None:
        problems += [f"allowed_actions has {a}, which the run it was learned from never did."
                     for a in playbook.allowed_actions if a not in observed and a not in unknown]
    return problems


def observed_actions(store: Store, run_id: int) -> set[str] | None:
    """The actions a finished run verified (not skipped), or None if it isn't a run a playbook can be
    learned from: one that reached DONE having changed something."""
    if store.get_run(run_id).state != RunState.DONE:
        return None
    actions = {e.payload["action"] for e in store.events_of(run_id, "step_verified") if not e.payload.get("skipped")}
    return actions or None
