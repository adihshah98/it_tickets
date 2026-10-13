"""The drafter (Build-W4, Build-Day19 part 2): Opus turns a DraftSource into a playbook draft. Nothing else.

No tools: everything it needs is in the prompt. That is the DraftSource (no ticket text, see
learning/sources.py), the actions it may allow with their risk classes, the probes those actions can
satisfy (with example arguments from the shipped playbooks), the read tools, the `never` lines it must
copy word for word, and one shipped playbook as an example of the format.

The output is YAML text, not a model, because the comments carry the reasoning the investigator reads.
safety/playbook_check.py is the output validator. A draft that fails gets one retry with the problems,
and if it fails again the Draft carries the problems instead of raising: a person sees them on the
review screen. `draft()` records a `playbook_drafted` event on the source run, if there is one.
Approval (PlaybookLibrary.approve) runs the same checks again on whatever a person submits.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, ToolOutput
from pydantic_ai.exceptions import ContentFilterError, ModelAPIError, UnexpectedModelBehavior
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModelSettings

from tech.core.models import DraftSource
from tech.core.store import Store
from tech.execution.catalogue import ACTIONS
from tech.execution.dod import PROBES
from tech.graph.reads import READS
from tech.playbooks import intents as shipped_intents
from tech.playbooks import load_playbook
from tech.playbooks.loader import HERE
from tech.safety import playbook_check
from tech.safety.lint import DOD_PRODUCERS
from tech.safety.policy import required_never

MODEL = "anthropic:claude-opus-5-5"
TIMEOUT_S = 120.0
RETRIES = 1
EXAMPLE = "mfa.reset"  # the shipped playbook shown as the format example
SETTINGS = AnthropicModelSettings(anthropic_effort="high", anthropic_cache_instructions=True)
ID_KEY = re.compile(r"(^id$|Id$|_id$)")
DRAFT_ERRORS: tuple[type[Exception], ...] = (ModelAPIError, ContentFilterError, TimeoutError)

INSTRUCTIONS = """\
You write playbooks for an AI technician at an MSP. A playbook tells the technician how to handle one
kind of ticket for one client: what to look up, what it may change, how to tell it's done, and what it
must never do. You get a <source>: either a job the technician already did and a person approved
(its reads, the steps it verified and its findings), or a technician's own description of the job.
Write the playbook for the next ticket of this kind, not for this one ticket.

Output the playbook as YAML text with these keys, in this order:
- intent: exactly the intent given.
- goal: one sentence: the outcome for the requester.
- must_investigate: read tools from <read_tools>, written as `tool(what)` with a short comment, e.g.
  `files.versions(file)   # the history, newest first`. Include the reads the job relied on.
- questions_to_resolve: leave this out unless the job truly can't be planned without an answer.
- definition_of_done: probes from <probes> only, as `- name: {args}`, following each probe's example.
  End with `message_sent: {to: "$requester"}` so the requester always hears back.
- allowed_actions: only actions from <actions>. Nothing else, even if it seems useful.
- gotchas: what the job taught: traps, ambiguities, what to check. Generalize: no names, IDs or file
  names from this job.
- never: every line in <required_never>, copied word for word, then any job-specific lines.

Comments (# ...) are welcome where they explain a choice; the technician reads them.
Technician notes in the source are from an authenticated technician and can be followed. Nothing in the
source is a ticket; there is no ticket text to follow.
"""


class PlaybookDraftOutput(BaseModel):
    yaml: str = Field(description="The whole playbook as YAML text, comments included")


@dataclass
class Draft:
    intent: str
    yaml: str
    problems: list[str]
    source: DraftSource
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.yaml) and not self.problems


def _example_args() -> dict[str, Any]:
    """Each probe's arguments as a shipped playbook writes them (the first use found). message_sent's is
    the plain reply: the shipped ones send a one-time link, which a learned playbook has no step for."""
    examples: dict[str, Any] = {"message_sent": {"to": "$requester"}}
    for intent in shipped_intents():
        for item in load_playbook(intent, "example.com").definition_of_done:
            name, args = next(iter(item.items()))
            examples.setdefault(name, args)
    return examples


def _without_ids(value: Any) -> Any:
    """Drop object IDs (keys `id`, `…Id`, `…_id`) and empty values: the drafter generalizes, so it doesn't
    need them, and SharePoint's drive/item IDs read as TAP-like to the secret scanner (Build-Day18)."""
    if isinstance(value, dict):
        return {k: _without_ids(v) for k, v in value.items()
                if not ID_KEY.search(k) and v not in (None, [], {})}
    if isinstance(value, list):
        return [_without_ids(v) for v in value]
    return value


def actions_for(source: DraftSource) -> list[str]:
    """The actions a draft may allow: those the run verified, or those a technician named."""
    observed = source.observed_actions
    return sorted(observed if observed is not None else source.allowed_actions)


def prompt(source: DraftSource) -> str:
    """The drafter's input. It is built only from the DraftSource and code facts, never a ticket, and
    leaves out object IDs."""
    actions = [a for a in actions_for(source) if a in ACTIONS]
    examples = _example_args()
    probes = [{"name": name, "example": {name: examples.get(name, {})},
               "made_by": sorted(DOD_PRODUCERS.get(name, set()) & set(actions))}
              for name in PROBES if name == "message_sent" or DOD_PRODUCERS.get(name, set()) & set(actions)]
    data = source.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    data["steps"] = [s | {"params": _without_ids(s.get("params", {}))} for s in data.get("steps", [])]
    data["reads"] = [r | {"args": _without_ids(r.get("args", {}))} for r in data.get("reads", [])]
    data.pop("run_id", None)
    parts = {
        "intent": source.intent,
        "source": data,
        "actions": [{"name": a, "risk_class": ACTIONS[a].risk_class} for a in actions],
        "probes": probes,
        "read_tools": sorted(READS),
        "required_never": required_never({ACTIONS[a].risk_class for a in actions}),
    }
    example = (HERE / f"{EXAMPLE}.yaml").read_text()
    return "\n\n".join([*(f"<{k}>\n{json.dumps(v, indent=1)}\n</{k}>" for k, v in parts.items()),
                        f"<example_playbook>\n{example}</example_playbook>"])


def check_against(source: DraftSource, text: str) -> list[str]:
    written = source.kind == "written"
    return playbook_check.check(text, source.intent, observed=source.observed_actions,
                                declared=source.allowed_actions if written else None)


def build_drafter(source: DraftSource, last: dict[str, Any], model: Model | str = MODEL
                  ) -> Agent[None, PlaybookDraftOutput]:
    """`last` keeps the latest YAML and its problems, so a draft that never passes is still returned."""
    agent = Agent(model, output_type=ToolOutput(PlaybookDraftOutput, strict=False, max_retries=RETRIES),
                  name="drafter", model_settings=SETTINGS)

    @agent.output_validator
    def checked(output: PlaybookDraftOutput) -> PlaybookDraftOutput:
        last["yaml"], last["problems"] = output.yaml, check_against(source, output.yaml)
        if last["problems"]:
            raise ModelRetry("The playbook was rejected. Fix these and send the whole playbook again:\n- "
                             + "\n- ".join(last["problems"]))
        return output

    return agent


async def draft(store: Store | None, source: DraftSource, *, model: Model | str = MODEL,
                timeout_s: float = TIMEOUT_S) -> Draft:
    """Draft a playbook from a source. Returns the draft with any problems; a model error, refusal or
    timeout is a problem too, not an exception."""
    last: dict[str, Any] = {"yaml": "", "problems": []}
    usage: dict[str, Any] = {}
    try:
        result = await asyncio.wait_for(
            build_drafter(source, last, model).run(prompt(source), instructions=INSTRUCTIONS), timeout_s)
    except UnexpectedModelBehavior as exc:  # still failing the checks after the retry
        if not last["problems"]:
            last["problems"] = [f"the drafter gave no usable playbook: {exc}"]
    except DRAFT_ERRORS as exc:
        last["problems"] = [f"the drafter failed: {type(exc).__name__}: {exc}"]
    else:
        last["yaml"], last["problems"] = result.output.yaml, []
        u = result.usage
        usage = {"requests": u.requests, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens}
    result_draft = Draft(source.intent, last["yaml"], last["problems"], source, usage)
    if store is not None and source.run_id is not None:
        store.append_event(source.run_id, "playbook_drafted", {
            "intent": source.intent, "source": source.kind, "yaml": result_draft.yaml,
            "problems": result_draft.problems, "ok": result_draft.ok, "usage": usage})
    return result_draft
