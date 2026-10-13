"""Close-out writing (Tech.MD §4.8): Sonnet drafts the requester's reply and the PSA note. Nothing else.

The model gets a summary of the run built here from its events: what was done, follow-ups, the DoD
results and the technician hand-offs. It never gets the ticket text (untrusted, and not needed), a TAP
or a link URL. Where the reply carries the sign-in link it writes LINK_PLACEHOLDER; the snapshot puts the
URL in when the workspace shows it, so the token stays out of events, traces and model output.
The secret scanner still checks both drafts in code before they're recorded (tech/closeout.py).
No tools are registered: the agent only returns CloseoutDrafts.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import ContentFilterError, ModelAPIError, UnexpectedModelBehavior
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModelSettings

from tech.core.models import Plan
from tech.core.store import Store
from tech.execution import links, scheduler
from tech.execution.dod import Probe
from tech.playbooks import Playbook

MODEL = "anthropic:claude-sonnet-5-5"
TIMEOUT_S = 45.0
LINK_PLACEHOLDER = "{{link}}"
# What the requester should hear whenever a step of this action was done, playbook or not.
REPLY_NOTES = {
    "file.restoreVersion": "Restoring a version creates a new version of the file; nothing is lost. The "
                           "overwritten version is still in the file's version history.",
}
DRAFT_ERRORS: tuple[type[Exception], ...] = (ModelAPIError, UnexpectedModelBehavior, ContentFilterError,
                                             TimeoutError)
SETTINGS = AnthropicModelSettings(anthropic_effort="low", anthropic_cache_instructions=True)

INSTRUCTIONS = """\
You write the close-out for an AI technician at an MSP, after a job finished. You get a JSON summary of
the run. Write two texts. State only facts that are in the summary.

reply_subject and reply_body: to the requester (requester.name). Plain text, short and friendly, no markdown.
- Say what was set up for the new user in plain words: no object IDs.
- Sign-in: if one_time_link is present, write {{link}} exactly where the link goes, and say it opens once
  and when it expires. Never write a password, passcode or Temporary Access Pass, or ask for one.
- Recommend the Outlook mobile app for email on a phone; the built-in iOS Mail app can hit "needs admin approval".
- Mention the file shares and team groups the new user got.
- Items under needs_technician: say a technician is handling them now.
- Scheduled follow-ups, such as a license on the start date: say when.
- Anything in out_of_scope_requests: say it was not done and needs a separate request.
- Each line under notes: say it in plain words.

psa_note: for a technician reading the ticket later. Terse lines: what was done (UPN, group names),
definition-of-done results, follow-ups, items handed to a technician, requests declined.
"""


class CloseoutDrafts(BaseModel):
    reply_subject: str
    reply_body: str = Field(description=f"Plain text to the requester; write {LINK_PLACEHOLDER} where the link goes")
    psa_note: str = Field(description="Internal note on the ticket, for a technician")


def build_closeout(model: Model | str = MODEL) -> Agent[None, CloseoutDrafts]:
    return Agent(model, output_type=CloseoutDrafts, retries=1, name="closeout", model_settings=SETTINGS)


def build_context(store: Store, run_id: int, plan: Plan, probes: list[Probe], *, requester: dict[str, str],
                  playbook: Playbook | None) -> dict[str, Any]:
    """What the model needs to write the close-out, from the run's events. No secrets, no ticket text."""
    verified = {e.payload["step_id"]: e.payload for e in store.events_of(run_id, "step_verified")}
    done = []
    for step in plan.steps:
        if step.id in verified:
            params = step.params.model_dump(mode="json", by_alias=True, exclude_none=True)
            params.pop("tap", None)
            done.append({"action": step.action, "params": params, "skipped": verified[step.id].get("skipped")})
    new_user = next((v.get("output") for v in verified.values() if v["action"] == "user.create"), None)
    link = next((row for row in links.for_run(store, run_id) if row.to.casefold() == requester["email"].casefold()),
                None)
    return {
        "requester": requester,
        "new_user": new_user,
        "steps_done": done,
        "followups": [{"kind": f.kind, "run_at": links.as_utc(f.run_at).isoformat(), "status": f.status}
                      for f in scheduler.for_run(store, run_id)],
        "one_time_link": {"write": LINK_PLACEHOLDER, "expires_at": links.as_utc(link.expires_at).isoformat(),
                          "includes": link.includes} if link else None,
        "needs_technician": [t.model_dump() for t in plan.needs_technician],
        "out_of_scope_requests": plan.out_of_scope_requests,
        "definition_of_done": [p.payload() for p in probes],
        "gotchas": playbook.gotchas if playbook else [],
        "notes": [REPLY_NOTES[a] for a in dict.fromkeys(d["action"] for d in done) if a in REPLY_NOTES],
    }


async def draft(context: dict[str, Any], *, model: Model | str = MODEL,
                timeout_s: float = TIMEOUT_S) -> CloseoutDrafts:
    """Sonnet's drafts. Raises one of DRAFT_ERRORS on a model error, refusal or timeout."""
    prompt = "<run>\n" + json.dumps(context, indent=2, default=str) + "\n</run>"
    result = await asyncio.wait_for(build_closeout(model).run(prompt, instructions=INSTRUCTIONS), timeout_s)
    return result.output
