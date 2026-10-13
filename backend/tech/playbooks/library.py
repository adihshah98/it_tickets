"""The playbook library: every playbook, shipped or learned, as versioned rows in the store (Build-W4).

A run uses the newest approved version of each intent. `sync_shipped()` imports `playbooks/*.yaml` as
approved `shipped` versions on startup: v1 when the intent is new, and a new version when a file's
content changed since its last shipped version. Learning (Week 4 Day 19) adds versions the same way.

Autonomy is client_facts.autonomy with the per-intent overrides set in the product (ClientAutonomy) on
top. Learning never writes client facts.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

from sqlmodel import col, func, select

from tech.core.store import ClientAutonomy, PlaybookVersion, Store
from tech.playbooks.loader import CLIENT_FACTS, HERE, ClientFacts, Playbook, client_facts_text, parse_playbook


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class PlaybookLibrary:
    def __init__(self, store: Store, domain: str, facts: ClientFacts):
        self.store = store
        self.domain = domain
        self.facts = facts

    # -------------------------------------------------------------- seeding

    def sync_shipped(self, directory: Path = HERE) -> list[PlaybookVersion]:
        """Import each shipped playbook file that is new or changed. Returns the versions added."""
        added = []
        for path in sorted(directory.glob("*.yaml")):
            if path.name == CLIENT_FACTS:
                continue
            text = path.read_text().replace("{domain}", self.domain)
            parse_playbook(text, path.stem)  # a broken file fails startup, not a run
            with self.store.session() as s:
                last = s.exec(select(PlaybookVersion).where(PlaybookVersion.intent == path.stem,
                                                            PlaybookVersion.source == "shipped")
                              .order_by(col(PlaybookVersion.version).desc())).first()
            if last is None or last.content_hash != content_hash(text):
                added.append(self._add_approved(path.stem, text, source="shipped"))
        return added

    def _add_approved(self, intent: str, text: str, *, source: str, source_run_id: int | None = None,
                      approved_by: str | None = None) -> PlaybookVersion:
        """A new approved version; the previous approved one is retired."""
        with self.store.session() as s:
            top = s.exec(select(func.max(PlaybookVersion.version)).where(PlaybookVersion.intent == intent)).one()
            for old in s.exec(select(PlaybookVersion).where(PlaybookVersion.intent == intent,
                                                           PlaybookVersion.status == "approved")):
                old.status = "retired"
                s.add(old)
            row = PlaybookVersion(intent=intent, version=(top or 0) + 1, yaml=text, content_hash=content_hash(text),
                                  status="approved", source=source, source_run_id=source_run_id,
                                  approved_by=approved_by)
            s.add(row)
            s.commit()
            return row

    # -------------------------------------------------------------- reads

    def current(self, intent: str) -> PlaybookVersion | None:
        """The approved version runs use for this intent, or None if it has no playbook."""
        with self.store.session() as s:
            return s.exec(select(PlaybookVersion).where(PlaybookVersion.intent == intent,
                                                        PlaybookVersion.status == "approved")
                          .order_by(col(PlaybookVersion.version).desc())).first()

    def versions(self, intent: str) -> list[PlaybookVersion]:
        with self.store.session() as s:
            return list(s.exec(select(PlaybookVersion).where(PlaybookVersion.intent == intent)
                               .order_by(PlaybookVersion.version)))

    def intents(self) -> list[str]:
        """The intents with an approved playbook."""
        with self.store.session() as s:
            query = select(PlaybookVersion.intent).where(PlaybookVersion.status == "approved").distinct()
            return sorted(s.exec(query))

    def has(self, intent: str) -> bool:
        return self.current(intent) is not None

    def get(self, intent: str) -> Playbook:
        row = self.current(intent)
        if row is None:
            raise KeyError(f"no playbook for {intent!r}")
        return parse_playbook(row.yaml, intent)

    def for_intents(self, intents: Iterable[str]) -> dict[str, Playbook]:
        """The playbooks for the triaged intents that have one (none for 'unknown' or a no-playbook intent)."""
        rows = {i: row for i in dict.fromkeys(intents) if (row := self.current(i)) is not None}
        return {i: parse_playbook(row.yaml, i) for i, row in rows.items()}

    def prompt_text(self, intents: Iterable[str]) -> str:
        """Client facts plus the playbooks for the triaged intents, as YAML, for the investigator's instructions."""
        parts = [client_facts_text(self.domain)]
        parts += [row.yaml for i in dict.fromkeys(intents) if (row := self.current(i)) is not None]
        return "\n---\n".join(parts)

    # -------------------------------------------------------------- autonomy

    def autonomy(self) -> dict[str, str]:
        """Per-intent autonomy: client_facts.autonomy, then the product's overrides on top."""
        with self.store.session() as s:
            overrides = {row.intent: row.level for row in s.exec(select(ClientAutonomy))}
        return {**self.facts.autonomy, **overrides}
