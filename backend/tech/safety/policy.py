"""What both the linter and the gate judge a step by: facts about its target, and the no-playbook allow-list.

The linter uses them to send the model back to fix its plan; the gate checks them again and blocks,
so a plan that somehow skipped the linter still can't add anyone to these groups, or make a change a
ticket type with no playbook may not make.
"""

from __future__ import annotations

import re

from tech.graph.reads import Transcript
from tech.playbooks import ClientFacts

PRIVILEGED_NAME = re.compile(r"\b(admins?|administrators?|privileged)\b", re.IGNORECASE)
UNMANAGEABLE_KINDS = {"distribution_list": "a distribution list", "mail_security": "a mail-enabled security group"}

# A run with no playbook (Tech.MD §4.9) may use only these actions, and only at these risk classes: so
# user.update may rename or move someone but not block their sign-in (access.revoke). Credential, spend,
# data-access, revoking and destructive changes need a reviewed playbook first. file.restoreVersion is
# Week 4 Day 18's; until it is in the catalogue, the catalogue check blocks it.
NO_PLAYBOOK_ALLOWED = frozenset({
    "user.update", "group.addMember", "group.removeMember", "mailbox.autoReply", "file.restoreVersion",
    "link.sendOneTime",
})
NO_PLAYBOOK_RISKS = frozenset({"write.reversible", "none"})


def group_problem(group_id: str, transcript: Transcript, facts: ClientFacts) -> tuple[str, str] | None:
    """(rule_id, reason) if a new member must not be added to this group by the agent, else None."""
    seen = transcript.object(group_id)
    name = seen.get("displayName") or group_id

    if seen.get("roleAssignable") or PRIVILEGED_NAME.search(name):
        return "never.privileged_group", f"{name} is a privileged group; never mirror admin or privileged groups"

    for rule in facts.group_handling:
        if rule.group.lower() == name.lower():
            if rule.handling == "automatic":
                return "client_facts.group_handling", f"{name}: {rule.note} Leave it out of the plan"
            return "client_facts.group_handling", f"{name}: {rule.note} Remove the step"

    if (kind := seen.get("kind")) in UNMANAGEABLE_KINDS:
        return ("catalogue.group_kind", f"{name} is {UNMANAGEABLE_KINDS[kind]}, which Graph can't change; "
                                        "remove the step and list it under needs_technician")
    return None
