"""Secret scanner: known values, password/token/TAP-like patterns, and what must not trip it."""

import pytest
from conftest import DOMAIN, NEW, TAYLOR, plan_with, step
from fake_tenant import TAP_VALUE

from tech import runner
from tech.core.store import RunState
from tech.graph.writes import SecretLeak, Secrets
from tech.safety.scanner import MIN_KNOWN, scan

URL = "http://localhost:3000/l/AbC_dEf-123xyzQRS456tuvWXY789ab"


def kinds(text, **kw):
    return [h.kind for h in scan(text, **kw)]


def test_a_known_value_is_found_anywhere():
    assert kinds(f"note:{TAP_VALUE}.", known=[TAP_VALUE], patterns=False) == ["known_secret"]


def test_short_or_empty_known_values_are_ignored():
    assert scan("anything at all", known=["", "a", "x" * (MIN_KNOWN - 1)]) == []


def test_a_tap_like_string_is_caught_without_knowing_it():
    assert kinds(f"Her pass: {TAP_VALUE}") != []
    assert "tap_like" in kinds(f"use {TAP_VALUE} to sign in")


@pytest.mark.parametrize("text", [
    "password: Welcome1", "Password is Summer2026!", "pwd=Hunter22", "Temporary Access Pass: K9#mQ2xz",
])
def test_password_shapes(text):
    assert "password" in kinds(text)


@pytest.mark.parametrize("text", [
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
    "sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
    "client_secret=AbcdEfgh1234",
])
def test_token_shapes(text):
    assert "token" in kinds(text)


def test_a_normal_reply_and_psa_note_are_clean():
    text = (f"Hi Taylor,\n\nPriya Nair (priya.nair@{DOMAIN}) is set up. Open {URL} on Monday; it opens once.\n"
            "Her password is shown once, through the link. Use the Outlook mobile app, not the built-in iOS Mail.\n"
            "Hosted desktop on NW-TS-01 and VPN are with a technician. License O365_BUSINESS_ESSENTIALS "
            "assigned 2026-10-12T10:00:00Z. Group 48297cf8-723a-4826-a8b0-199373841df5. Placeholder {{link}}. "
            "It's Priya's first day; seat-holder1 kept.")
    assert scan(text) == []


@pytest.mark.parametrize("text", [
    "Your Temporary Access Pass: LINKHERE", "Use this link to get her Temporary Access Pass: LINKHERE and sign in",
    "Her sign-in pass is LINKHERE", "Your Temporary Access Pass: {{link}}", "Her sign-in pass is {{link}}",
    "TAP: 2026-10-12T17:00Z expiry", "The pass is 1-time use only", "Token: delivered via one-time link",
    "https://x.com/a?id=Ab12cd34", "priya+Test1@corp.com", "Tag <b>x</b> and {braces}",
])
def test_terse_phrasings_urls_and_emails_are_clean(text):
    assert scan(text) == []


def test_hits_carry_kind_and_offsets_but_never_the_text():
    hit = scan(f"x {TAP_VALUE}", known=[TAP_VALUE])[0]
    assert hit.payload() == {"kind": "known_secret", "start": 2}
    assert TAP_VALUE not in str(hit.payload())


def test_the_executor_guard_uses_known_values_only():
    secrets = Secrets()
    secrets.watch(TAP_VALUE)
    secrets.guard({"result": "Xy7!random#id"})  # tap-like, but not a known value: event payloads are full of these
    with pytest.raises(SecretLeak):
        secrets.guard({"oops": TAP_VALUE})
    assert secrets.known() == {TAP_VALUE}


async def test_a_link_note_with_a_password_is_not_sent(rt, gated, tenant):
    link = step("send_link", "link.sendOneTime", {"to": TAYLOR, "userId": NEW, "tap": "$ref:tap.output.tap",
                                                  "note": "Her password: Welcome1"}, "none", ("tap",))
    run_id = gated(plan_with(link, drop=("send_link",)))

    assert await runner.approve(rt, run_id, by=TAYLOR) == RunState.ESCALATED
    state = rt.store.last_event(run_id, "state").payload
    assert state["failed_step"] == "send_link" and "secret" in state["reason"]
    assert "Welcome1" not in str(rt.store.last_event(run_id, "step_failed").payload)
