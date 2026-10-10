import pytest

from tech.graph.reads import READS
from tech.playbooks import intents, load_client_facts, load_playbook, prompt_text

DOMAIN = "NorthwindParking.onmicrosoft.com"


def test_client_facts_are_aligned_with_the_seeded_tenant():
    facts = load_client_facts(DOMAIN)
    assert facts.approvers["user.onboard"] == [f"tgarcia@{DOMAIN}"]
    assert facts.approvers["spend"] == [f"tgarcia@{DOMAIN}"]
    assert facts.approvers["vpn_access"] == [f"kevin@{DOMAIN}"]
    handling = {g.group: g.handling for g in facts.group_handling}
    assert handling == {"Northwind Parking": "automatic", "RDS-Users": "technician", "VPN-Users": "technician"}
    assert facts.license_reserve[0]["users"] == "seat-holder*"
    assert "lauren" not in prompt_text(["user.onboard"], DOMAIN) and "marlene" not in prompt_text([], DOMAIN)


def test_approver_check_ignores_case():
    facts = load_client_facts(DOMAIN)
    assert facts.is_approver("TGarcia@northwindparking.onmicrosoft.com", "user.onboard")
    assert not facts.is_approver(f"jlee@{DOMAIN}", "user.onboard")
    assert not facts.is_approver(f"tgarcia@{DOMAIN}", "no.such.scope")


def test_onboard_playbook_loads():
    playbook = load_playbook("user.onboard", DOMAIN)
    assert playbook.intent == "user.onboard"
    assert "tap.create" in playbook.allowed_actions
    assert playbook.must_investigate_tools[:2] == ["users.search", "users.get"]


@pytest.mark.parametrize("intent", intents())
def test_must_investigate_names_real_read_tools(intent):
    assert set(load_playbook(intent, DOMAIN).must_investigate_tools) <= set(READS)


def test_unknown_intent_has_no_playbook():
    with pytest.raises(KeyError):
        load_playbook("file.restore_version", DOMAIN)


def test_prompt_text_keeps_the_yaml_comments_and_fills_the_domain():
    text = prompt_text(["user.onboard", "unknown"], DOMAIN)
    assert "# mirror the peer's license, never guess" in text
    assert "{domain}" not in text and f"kevin@{DOMAIN}" in text
    assert text.count("\n---\n") == 1  # client facts + the one playbook that exists
