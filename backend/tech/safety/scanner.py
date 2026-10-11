"""Secret scanner (Tech.MD §4.8): nothing that looks like a credential leaves in outbound text.

Two checks:
  known     values this run holds in memory (the TAP, the new user's random password, link tokens),
            found anywhere in the text. Values shorter than MIN_KNOWN are ignored: "" would match everything.
  patterns  shapes of secrets we don't know: "password: X", bearer/JWT/API-key tokens, and TAP-like
            strings (8-48 characters with upper, lower, digit and a symbol that URLs, emails, GUIDs and
            server names don't use).

Hits carry where and what kind, never the matched text, so a hit can go into an event. The executor's
event guard uses known values only (tool results are full of random-looking IDs); replies, PSA notes and
link notes use both.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

MIN_KNOWN = 8

HitKind = Literal["known_secret", "password", "token", "tap_like"]

SECRETISH = r"(?=\S*[0-9!#$%&*+=?^~|<>{}\\])\S{4,}"  # a value with a digit or a symbol; "shown." isn't one
PATTERNS: list[tuple[HitKind, re.Pattern[str]]] = [
    ("password", re.compile(
        rf"(?i)\b(pass(word|code|phrase)?|pwd|temporary access pass|tap)\b\s*(is|:|=)\s*{SECRETISH}")),
    ("token", re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{5,}")),
    ("token", re.compile(r"(?i)\bbearer\s+[\w\-.~+/]{20,}")),
    ("token", re.compile(r"\bsk-[\w-]{20,}")),
    ("token", re.compile(r"(?i)\b(api[_-]?key|client[_-]?secret|secret|access[_-]?token|token)\b\s*[:=]\s*\S{8,}")),
]
WORD = re.compile(r"\S{8,48}")
STRIP = "()[]<>\"',.;:"
SYMBOL = re.compile(r"[!#$%&*+=?^~|\\]")


@dataclass(frozen=True)
class Hit:
    kind: HitKind
    start: int
    end: int

    def payload(self) -> dict[str, Any]:
        return {"kind": self.kind, "start": self.start}


def _tap_like(word: str) -> bool:
    return (any(c.isupper() for c in word) and any(c.islower() for c in word)
            and any(c.isdigit() for c in word) and bool(SYMBOL.search(word)))


def scan(text: str, *, known: Iterable[str] = (), patterns: bool = True) -> list[Hit]:
    hits: list[Hit] = []
    for value in {v for v in known if v and len(v) >= MIN_KNOWN}:
        start = text.find(value)
        while start != -1:
            hits.append(Hit("known_secret", start, start + len(value)))
            start = text.find(value, start + 1)
    if patterns:
        for kind, pattern in PATTERNS:
            hits += [Hit(kind, m.start(), m.end()) for m in pattern.finditer(text)]
        for m in WORD.finditer(text):
            word = m.group().strip(STRIP)
            if len(word) >= 8 and _tap_like(word):
                hits.append(Hit("tap_like", m.start(), m.end()))
    return sorted(hits, key=lambda h: h.start)
