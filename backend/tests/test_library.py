"""The playbook library: shipped playbooks synced into the store, versioning, and that runs see exactly
what they saw when playbooks were files (Build-W4 Day 17, part 1); approving learned and written
playbooks, the autonomy ceiling (Day 19, part 1)."""

import shutil

import pytest
import yaml
from conftest import DOMAIN, LEARNED_RESTORE, TAYLOR, add_learned_restore, plan_with, shipped_library
from sqlmodel import select

from tech import runner
from tech.core.store import ClientAutonomy, PlaybookVersion, RunState
from tech.playbooks import PlaybookLibrary, PlaybookRejected, intents, load_playbooks, prompt_text
from tech.playbooks.loader import HERE
from tech.safety import gate


def test_sync_imports_each_shipped_playbook_once(store, facts):
    library = shipped_library(store, facts)

    assert library.intents() == intents() == ["mfa.reset", "user.offboard", "user.onboard"]
    rows = [library.current(i) for i in intents()]
    assert all((r.version, r.status, r.source) == (1, "approved", "shipped") for r in rows)
    assert library.sync_shipped() == []  # unchanged files add nothing


def test_a_changed_shipped_file_becomes_the_next_version(store, facts, tmp_path):
    shipped = tmp_path / "playbooks"
    shutil.copytree(HERE, shipped)
    library = PlaybookLibrary(store, DOMAIN, facts)
    library.sync_shipped(shipped)

    path = shipped / "mfa.reset.yaml"
    path.write_text(path.read_text() + "\n# a new comment\n")
    added = library.sync_shipped(shipped)

    assert [(r.intent, r.version) for r in added] == [("mfa.reset", 2)]
    assert [(v.version, v.status) for v in library.versions("mfa.reset")] == [(1, "retired"), (2, "approved")]
    assert library.current("mfa.reset").yaml.endswith("# a new comment\n")


def test_a_broken_shipped_file_fails_the_sync(store, facts, tmp_path):
    shipped = tmp_path / "playbooks"
    shutil.copytree(HERE, shipped)
    (shipped / "mfa.reset.yaml").write_text("intent: user.onboard\ngoal: wrong file\n")

    with pytest.raises(ValueError):
        PlaybookLibrary(store, DOMAIN, facts).sync_shipped(shipped)


def test_runs_see_the_same_playbooks_and_prompt_as_the_files(store, facts):
    library = shipped_library(store, facts)
    triaged = ["user.offboard", "unknown", "user.onboard", "mfa.reset"]

    assert library.for_intents(triaged) == load_playbooks(triaged, DOMAIN)
    assert library.prompt_text(triaged) == prompt_text(triaged, DOMAIN)
    assert not library.has("unknown") and library.for_intents(["unknown"]) == {}
    with pytest.raises(KeyError):
        library.get("file.restore_version")


def test_onboarding_gate_decisions_are_unchanged(store, facts, transcript):
    library = shipped_library(store, facts)

    def decisions(playbooks, autonomy):
        return [d.model_dump(mode="json") for d in gate.evaluate(
            plan_with(), requester_approved=True, intents=["user.onboard"], playbooks=playbooks, facts=facts,
            autonomy=autonomy, unproven=library.unproven(), transcript=transcript).decisions]

    assert decisions(library.for_intents(["user.onboard"]), library.autonomy()) == decisions(
        load_playbooks(["user.onboard"], DOMAIN), facts.autonomy)


def test_autonomy_is_client_facts_with_overrides_on_top(store, facts):
    library = shipped_library(store, facts)
    assert library.autonomy() == facts.autonomy

    with store.session() as s:
        s.add(ClientAutonomy(intent="mfa.reset", level="A1", set_by="test"))
        s.commit()
    assert library.autonomy() == {**facts.autonomy, "mfa.reset": "A1"}


def test_a_runtime_syncs_the_shipped_playbooks_into_its_store(rt):
    assert rt.library.intents() == intents()
    with rt.store.session() as s:
        assert len(s.exec(select(PlaybookVersion)).all()) == len(intents())


# ------------------------------------------------------------------ approving learned and written playbooks
# (Build-Day19 part 1)

INTENT = "file.restore_version"


async def done_restore(rt, restore_gated, **kw) -> int:
    """Sophie's restore, approved and DONE: the run a playbook is learned from."""
    run_id, _, _ = restore_gated(**kw)
    assert await runner.approve(rt, run_id, by=TAYLOR) == RunState.DONE
    return run_id


async def test_an_agent_run_draft_can_be_approved_straight_to_a3(rt, restore_gated, tmp_path):
    rt.library.learned_dir = tmp_path
    run_id = await done_restore(rt, restore_gated)

    row = rt.library.approve(INTENT, LEARNED_RESTORE.read_text(), autonomy="A3", by="aditya", source="agent_run",
                             source_run_id=run_id)

    assert (row.version, row.status, row.source, row.source_run_id, row.approved_by) == (
        1, "approved", "agent_run", run_id, "aditya")
    assert rt.library.has(INTENT) and rt.library.autonomy()[INTENT] == "A3"
    assert rt.store.last_event(run_id, "playbook_approved").payload == {
        "intent": INTENT, "version": 1, "source": "agent_run", "autonomy": "A3", "by": "aditya"}
    [record] = tmp_path.glob("*.yaml")
    assert record.name == f"{INTENT}-v1-run{run_id}.yaml"
    learned = yaml.safe_load(record.read_text())
    assert learned["gate"] == {"restore": "approve/no_playbook.approve"} and learned["state"] == "DONE"


async def test_the_next_restore_runs_under_the_approved_playbook(rt, restore_gated):
    run_id = await done_restore(rt, restore_gated)
    rt.library.approve(INTENT, LEARNED_RESTORE.read_text(), autonomy="A3", by="aditya", source="agent_run",
                       source_run_id=run_id)

    next_run, _, _ = restore_gated()
    plan = rt.store.last_event(next_run, "plan").payload
    assert plan["no_playbook"] is False and plan["playbook_versions"] == {INTENT: 1}
    [decision] = [e.payload for e in rt.store.events_of(next_run, "gate_decision")]
    assert decision["rule_id"] == "autonomy.a3.write.reversible"


async def test_an_edit_widening_allowed_actions_is_rejected(rt, restore_gated):
    run_id = await done_restore(rt, restore_gated)
    text = LEARNED_RESTORE.read_text().replace("[file.restoreVersion]", "[file.restoreVersion, group.addMember]")

    with pytest.raises(PlaybookRejected) as rejected:
        rt.library.approve(INTENT, text, autonomy="A2", by="aditya", source="agent_run", source_run_id=run_id)
    assert rejected.value.problems == [
        "allowed_actions has group.addMember, which the run it was learned from never did."]
    assert not rt.library.has(INTENT)


async def test_a_written_playbook_is_a2_until_a_run_on_it_succeeds(rt, restore_gated):
    text = LEARNED_RESTORE.read_text()
    with pytest.raises(PlaybookRejected, match="A3 needs a successful approved run"):
        rt.library.approve(INTENT, text, autonomy="A3", by="aditya", source="written")

    rt.library.approve(INTENT, text, autonomy="A2", by="aditya", source="written")
    assert rt.library.current_ceiling(INTENT) == "A2"
    with pytest.raises(PlaybookRejected):
        rt.library.set_autonomy(INTENT, "A3", by="aditya")

    await done_restore(rt, restore_gated)  # gated `approve` at A2, approved, DONE, on version 1
    assert rt.library.current_ceiling(INTENT) == "A3"
    rt.library.set_autonomy(INTENT, "A3", by="aditya")
    assert rt.library.autonomy()[INTENT] == "A3"


async def test_an_audit_log_playbook_needs_the_audit_logs_behind_it(rt):
    """Its A2 cap and the check against the audit-logged actions are in test_learning.py (L2)."""
    with pytest.raises(PlaybookRejected, match="audit logs showed a change"):
        rt.library.approve(INTENT, LEARNED_RESTORE.read_text(), autonomy="A2", by="aditya", source="audit_log")


async def test_approving_again_makes_v2_and_retires_v1(rt, restore_gated):
    run_id = await done_restore(rt, restore_gated)
    text = LEARNED_RESTORE.read_text()
    rt.library.approve(INTENT, text, autonomy="A2", by="aditya", source="agent_run", source_run_id=run_id)
    rt.library.approve(INTENT, text + "# edited\n", autonomy="A1", by="aditya", source="written")

    assert [(v.version, v.status, v.source) for v in rt.library.versions(INTENT)] == [
        (1, "retired", "agent_run"), (2, "approved", "written")]
    assert rt.library.autonomy()[INTENT] == "A1"


@pytest.mark.parametrize("intent, problem", [
    ("unknown", "can't be saved as 'unknown'"),
    ("file.restore", "is the known intent 'file.restore_version'"),
    ("Restore File", "is not an intent slug"),
])
def test_a_bad_intent_slug_is_rejected(rt, intent, problem):
    add_learned_restore(rt.library)
    text = LEARNED_RESTORE.read_text().replace(f"intent: {INTENT}", f"intent: {intent}")
    with pytest.raises(PlaybookRejected) as rejected:
        rt.library.approve(intent, text, autonomy="A2", by="aditya", source="written")
    assert any(problem in p for p in rejected.value.problems), rejected.value.problems


def test_a_run_source_needs_a_finished_run(rt, restore_gated):
    run_id, _, _ = restore_gated()  # awaiting approval: nothing verified yet
    with pytest.raises(PlaybookRejected, match="needs a source run that reached DONE"):
        rt.library.approve(INTENT, LEARNED_RESTORE.read_text(), autonomy="A2", by="aditya", source="agent_run",
                           source_run_id=run_id)
    with pytest.raises(PlaybookRejected, match="source must be one of"):
        rt.library.approve(INTENT, LEARNED_RESTORE.read_text(), autonomy="A2", by="aditya", source="shipped")


def test_set_autonomy_checks_the_level_and_the_playbook(rt):
    with pytest.raises(PlaybookRejected):
        rt.library.set_autonomy("mfa.reset", "A4", by="aditya")
    with pytest.raises(KeyError):
        rt.library.set_autonomy(INTENT, "A3", by="aditya")
    rt.library.set_autonomy("mfa.reset", "A1", by="aditya")  # shipped: the client's call, up to A3
    assert rt.library.autonomy()["mfa.reset"] == "A1"
