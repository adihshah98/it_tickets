"""Code checks on learned and written playbooks (Build-W4, Build-Day19 part 1): each rejection, and that
the learned restore playbook passes against the run it came from."""

import pytest
import yaml
from conftest import DOMAIN, LEARNED_RESTORE, TAYLOR

from tech import runner
from tech.playbooks import intents
from tech.playbooks.loader import HERE
from tech.safety import playbook_check
from tech.safety.policy import DEFAULT_NEVER, UNIVERSAL_NEVER, required_never

INTENT = "file.restore_version"
RESTORE = {"file.restoreVersion"}


def edited(**changes) -> str:
    data = yaml.safe_load(LEARNED_RESTORE.read_text()) | changes
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def test_the_learned_restore_playbook_passes():
    assert playbook_check.check(LEARNED_RESTORE.read_text(), INTENT, observed=RESTORE) == []


async def test_it_passes_against_the_run_it_was_learned_from(rt, restore_gated):
    run_id, _, _ = restore_gated()
    assert playbook_check.observed_actions(rt.store, run_id) is None  # not finished: nothing to learn from yet
    await runner.approve(rt, run_id, by=TAYLOR)

    observed = playbook_check.observed_actions(rt.store, run_id)
    assert observed == RESTORE
    assert playbook_check.check(LEARNED_RESTORE.read_text(), INTENT, observed=observed) == []


def test_an_action_the_run_never_did_is_rejected():
    widened = edited(allowed_actions=["file.restoreVersion", "tap.create"])
    problems = playbook_check.check(widened, INTENT, observed=RESTORE)
    assert "allowed_actions has tap.create, which the run it was learned from never did." in problems
    # tap.create is security.credential, so its default `never` line is now required as well
    assert f"never must include: {DEFAULT_NEVER['security.credential'][0]!r}" in problems


def test_a_written_playbook_has_no_observed_steps_to_stay_within():
    widened = edited(allowed_actions=["file.restoreVersion", "group.addMember"])
    assert playbook_check.check(widened, INTENT) == []
    assert playbook_check.check(widened, INTENT, observed=RESTORE) != []


def test_a_dod_name_with_no_probe_is_rejected():
    text = edited(definition_of_done=[{"version_restored": {}}, {"file_is_fine": {}}])
    assert playbook_check.check(text, INTENT, observed=RESTORE) == [
        "definition_of_done has 'file_is_fine', which is not a probe."]


def test_a_dod_no_allowed_action_can_meet_is_rejected():
    text = edited(definition_of_done=[{"version_restored": {}}, {"user_in_groups": {}}])
    assert playbook_check.check(text, INTENT) == [
        "Nothing in allowed_actions could meet the definition of done 'user_in_groups'."]


def test_a_missing_default_never_line_is_rejected():
    data = yaml.safe_load(LEARNED_RESTORE.read_text())
    text = edited(never=[line for line in data["never"] if line != UNIVERSAL_NEVER[0]])
    assert playbook_check.check(text, INTENT, observed=RESTORE) == [f"never must include: {UNIVERSAL_NEVER[0]!r}"]


def test_unknown_actions_and_read_tools_are_rejected():
    text = edited(allowed_actions=["file.restoreVersion", "file.delete"],
                  must_investigate=["files.search(name)", "sharepoint.audit(file)"])
    problems = playbook_check.check(text, INTENT)
    assert "allowed_actions has file.delete, which is not in the write catalogue." in problems
    assert "must_investigate has sharepoint.audit, which is not a read tool." in problems


@pytest.mark.parametrize("text, saved_as", [
    ("intent: [unclosed", INTENT),
    ("- just a list", INTENT),
    ("intent: file.restore_version\ngoal: no actions or DoD\n", INTENT),
    (LEARNED_RESTORE.read_text(), "file.restore"),  # declares a different intent than it's saved under
    (edited(owner="someone"), INTENT),              # an unknown key
])
def test_a_playbook_that_doesnt_parse_is_rejected(text, saved_as):
    [problem] = playbook_check.check(text, saved_as)
    assert problem.startswith("The playbook doesn't parse")


def test_required_never_is_the_universal_lines_plus_each_risk_class():
    assert required_never({"write.reversible"}) == list(UNIVERSAL_NEVER)
    assert required_never({"spend", "security.credential"}) == [
        *UNIVERSAL_NEVER, *DEFAULT_NEVER["security.credential"], *DEFAULT_NEVER["spend"]]


def test_shipped_playbooks_pass_everything_but_the_newer_never_defaults():
    """The shipped playbooks predate DEFAULT_NEVER and aren't checked against it (sync_shipped). This pins
    which default lines each lacks, so adding them is a choice, not an accident."""
    for intent in intents():
        text = (HERE / f"{intent}.yaml").read_text().replace("{domain}", DOMAIN)
        problems = playbook_check.check(text, intent)
        assert all(p.startswith("never must include") for p in problems), (intent, problems)
        assert len(problems) == {"mfa.reset": 2, "user.offboard": 4, "user.onboard": 4}[intent], (intent, problems)
