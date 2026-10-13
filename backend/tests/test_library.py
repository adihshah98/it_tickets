"""The playbook library: shipped playbooks synced into the store, versioning, and that runs see exactly
what they saw when playbooks were files (Build-W4 Day 17, part 1)."""

import shutil

import pytest
from conftest import DOMAIN, plan_with, shipped_library
from sqlmodel import select

from tech.core.store import ClientAutonomy, PlaybookVersion
from tech.playbooks import PlaybookLibrary, intents, load_playbooks, prompt_text
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
            autonomy=autonomy, transcript=transcript).decisions]

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
