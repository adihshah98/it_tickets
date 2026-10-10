from logfire._internal.scrubbing import Scrubber

from tech.core.observability import EXTRA_SCRUB_PATTERNS


def scrub(attrs: dict) -> dict:
    scrubbed, _ = Scrubber(EXTRA_SCRUB_PATTERNS).scrub_value(("attributes",), attrs)
    return scrubbed


def test_secrets_are_scrubbed():
    out = scrub({
        "password": "p",
        "access_token": "a",
        "accessToken": "b",
        "http.body": '{"temporaryAccessPass": "Xy12abcd"}',
        "note": "the TAP is Xy12abcd",
    })
    assert all("[Scrubbed due to" in str(v) for v in out.values())
    assert "Xy12abcd" not in str(out)


def test_token_usage_counts_are_kept():
    attrs = {"gen_ai.usage.input_tokens": 12, "gen_ai.usage.output_tokens": 3, "max_tokens": 5, "displayName": "Priya"}
    assert scrub(attrs) == attrs
