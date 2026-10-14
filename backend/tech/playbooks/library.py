"""The playbook library: every playbook, shipped or learned, as versioned rows in the store (Build-W4).

A run uses the newest approved version of each intent. `sync_shipped()` imports `playbooks/*.yaml` as
approved `shipped` versions on startup: v1 when the intent is new, and a new version when a file's
content changed since its last shipped version. `approve()` adds a learned or written version the same
way, after the code checks (safety/playbook_check.py) and the autonomy ceiling.

Autonomy is client_facts.autonomy with the per-intent overrides set in the product (ClientAutonomy) on
top. Learning never writes client facts.

The autonomy ceiling (Build-Day19 decision 3): A3 needs evidence that this version works, which is a
successful approved run on it, or a source run that is one (an agent or technician run the playbook was
learned from). Without it (written, edited shipped, audit log) the ceiling is A2. An A3 run that fails
its DoD or a step is demoted to A2 (`demote_on_failure`).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

import yaml
from pydantic import TypeAdapter, ValidationError
from sqlmodel import col, func, select

from tech.core.models.base import IntentSlug
from tech.core.store import ClientAutonomy, Event, PlaybookVersion, RunState, Store, utcnow
from tech.playbooks.loader import CLIENT_FACTS, HERE, ClientFacts, Playbook, client_facts_text, parse_playbook

LEVELS = ("A1", "A2", "A3")
SOURCES = ("shipped", "agent_run", "technician_run", "written", "audit_log")
RUN_SOURCES = ("agent_run", "technician_run")  # learned from a run's verified steps
DEMOTE_TO = "A2"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class PlaybookRejected(ValueError):
    """A playbook (or an autonomy level) that can't be approved, with every reason."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


class PlaybookLibrary:
    def __init__(self, store: Store, domain: str, facts: ClientFacts, *, learned_dir: Path | None = None):
        self.store = store
        self.domain = domain
        self.facts = facts
        self.learned_dir = learned_dir  # where approve() records the source run (decision 9); None: nowhere

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

    # -------------------------------------------------------------- learning

    def approve(self, intent: str, text: str, *, autonomy: str, by: str, source: str,
                source_run_id: int | None = None) -> PlaybookVersion:
        """Approve a learned or written playbook as the intent's next version, at this autonomy level.
        Everything is checked again here, whatever a draft said: the slug, the code checks (against the
        source run's verified actions, rebuilt from the store) and the autonomy ceiling."""
        from tech.agents.intake import near_duplicate
        from tech.safety import playbook_check

        problems = []
        if source not in SOURCES or source == "shipped":
            problems.append(f"source must be one of {', '.join(SOURCES[1:])}, not {source!r}")
        if autonomy not in LEVELS:
            problems.append(f"autonomy must be one of {', '.join(LEVELS)}, not {autonomy!r}")
        if not by.strip():
            problems.append("an approved playbook needs an approver (by)")
        try:
            TypeAdapter(IntentSlug).validate_python(intent)
        except ValidationError:
            problems.append(f"{intent!r} is not an intent slug (dot-separated lowercase words)")
        if intent == "unknown":
            problems.append("a playbook can't be saved as 'unknown'; name the job, e.g. file.restore_version")
        elif dup := near_duplicate(intent, [i for i in self.intents() if i != intent]):
            problems.append(f"{intent!r} is the known intent {dup!r}; save it as a new version of that")

        observed = None
        if source in RUN_SOURCES:
            if source_run_id is not None:
                observed = playbook_check.observed_actions(self.store, source_run_id)
            if observed is None:
                problems.append(f"a {source} playbook needs a source run that reached DONE and changed something")
        elif source == "audit_log":
            if source_run_id is not None:
                observed = self.audit_actions(source_run_id)
            if not observed:
                problems.append("an audit_log playbook needs a source run whose audit logs showed a change to learn")
        problems += playbook_check.check(text, intent, observed=observed)
        ceiling = None if problems else self.ceiling(intent, source=source, source_run_id=source_run_id)
        if autonomy == "A3" and ceiling == "A2":
            problems.append("A3 needs a successful approved run on this playbook first; approve it at A1 or A2")
        if problems:
            raise PlaybookRejected(problems)

        row = self._add_approved(intent, text, source=source, source_run_id=source_run_id, approved_by=by)
        self._set_level(intent, autonomy, by)
        if source_run_id is not None:
            self.store.append_event(source_run_id, "playbook_approved", {
                "intent": intent, "version": row.version, "source": source, "autonomy": autonomy, "by": by})
            self._record_learned(row, source_run_id)
        return row

    def audit_actions(self, run_id: int) -> set[str] | None:
        """The actions the run's audit logs showed (its last `audit_observed` with changes), or None."""
        found = [e.payload for e in self.store.events_of(run_id, "audit_observed") if e.payload.get("mapped")]
        return {s["action"] for s in found[-1]["mapped"]} if found else None

    def add_version(self, intent: str, text: str, *, source: str, source_run_id: int | None = None,
                    approved_by: str | None = None) -> PlaybookVersion:
        """Add an approved version with no checks or ceiling: for tests and eval setups that state their own
        history. Product code approves through `approve()`."""
        parse_playbook(text, intent)
        return self._add_approved(intent, text, source=source, source_run_id=source_run_id, approved_by=approved_by)

    def successful_run(self, run_id: int) -> bool:
        """A run that reached DONE with a person behind its changes: an approval, or a technician who made them
        in the console (Build-Day20; their own action is their approval)."""
        if self.store.get_run(run_id).state != RunState.DONE:
            return False
        approved = any(e.payload["decision"] == "approve" for e in self.store.events_of(run_id, "approval"))
        by_technician = any(e.payload.get("actor", "").startswith("technician:")
                            for e in self.store.events_of(run_id, "step_verified"))
        return approved or by_technician

    def successful_runs(self, intent: str, version: int) -> list[int]:
        """The successful approved runs that used this version of this intent's playbook."""
        with self.store.session() as s:
            plans = s.exec(select(Event).where(Event.type == "plan")).all()
        used = {e.run_id for e in plans if (e.payload.get("playbook_versions") or {}).get(intent) == version}
        return sorted(r for r in used if self.successful_run(r))

    def ceiling(self, intent: str, *, source: str, source_run_id: int | None = None,
                version: int | None = None) -> str:
        """The highest autonomy this playbook may have: A3 with a successful approved run behind it (its
        source run, or a run on `version`), else A2."""
        if source in RUN_SOURCES and source_run_id is not None and self.successful_run(source_run_id):
            return "A3"
        if version is not None and self.successful_runs(intent, version):
            return "A3"
        return "A2"

    def current_ceiling(self, intent: str) -> str:
        row = self.current(intent)
        if row is None:
            raise KeyError(f"no playbook for {intent!r}")
        if row.source == "shipped":
            return "A3"  # reviewed in the repo; the client's facts set its level
        return self.ceiling(intent, source=row.source, source_run_id=row.source_run_id, version=row.version)

    def _record_learned(self, row: PlaybookVersion, run_id: int) -> None:
        """Write the run a playbook was learned from as a YAML record, for turning into an eval case later."""
        if self.learned_dir is None:
            return
        events = self.store.events(run_id)

        def last(kind: str) -> dict | None:
            return next((e.payload for e in reversed(events) if e.type == kind), None)

        ticket = self.store.get_ticket(self.store.get_run(run_id).ticket_id)
        record = {
            "intent": row.intent, "version": row.version, "source": row.source, "run_id": run_id,
            "ticket": ticket.text, "from": ticket.from_address,
            "triage": (last("triage") or {}).get("triage"),
            "reads": [[e.payload["tool"], e.payload["args"]] for e in events if e.type == "tool_call"],
            "plan": (last("plan") or {}).get("plan"),
            "gate": {e.payload["step_id"]: f"{e.payload['decision']}/{e.payload['rule_id']}"
                     for e in events if e.type == "gate_decision"},
            "state": self.store.get_run(run_id).state.value,
        }
        self.learned_dir.mkdir(parents=True, exist_ok=True)
        path = self.learned_dir / f"{row.intent}-v{row.version}-run{run_id}.yaml"
        path.write_text(yaml.safe_dump(record, sort_keys=False, allow_unicode=True))

    # -------------------------------------------------------------- autonomy

    def unproven(self) -> set[str]:
        """Intents whose current playbook was learned or written, not shipped: below A3 the gate has every
        write approved (gate.py, autonomy.unproven)."""
        with self.store.session() as s:
            rows = s.exec(select(PlaybookVersion).where(PlaybookVersion.status == "approved")).all()
        return {r.intent for r in rows if r.source != "shipped"}

    def autonomy(self) -> dict[str, str]:
        """Per-intent autonomy: client_facts.autonomy, then the product's overrides on top."""
        with self.store.session() as s:
            overrides = {row.intent: row.level for row in s.exec(select(ClientAutonomy))}
        return {**self.facts.autonomy, **overrides}

    def set_autonomy(self, intent: str, level: str, *, by: str, enforce_ceiling: bool = True) -> None:
        """Set an intent's autonomy for this client, within its playbook's ceiling. `enforce_ceiling=False`
        is for tests and eval setups only."""
        if level not in LEVELS:
            raise PlaybookRejected([f"autonomy must be one of {', '.join(LEVELS)}, not {level!r}"])
        if enforce_ceiling:
            if level == "A3" and self.current_ceiling(intent) != "A3":
                raise PlaybookRejected(["A3 needs a successful approved run on this playbook first"])
        self._set_level(intent, level, by)

    def _set_level(self, intent: str, level: str, by: str) -> None:
        with self.store.session() as s:
            row = s.get(ClientAutonomy, intent) or ClientAutonomy(intent=intent, level=level)
            row.level, row.set_by, row.updated_at = level, by, utcnow()
            s.add(row)
            s.commit()

    def demote_on_failure(self, run_id: int, reason: str) -> list[str]:
        """A run that failed its DoD or a step: each playbook it used at A3 drops to A2 (Build-W4), with a
        playbook_demoted event on the run. Returns the intents demoted."""
        plan = self.store.last_event(run_id, "plan")
        used = (plan.payload.get("playbook_versions") or {}) if plan else {}
        levels = self.autonomy()
        demoted = [intent for intent in used if levels.get(intent) == "A3"]
        for intent in demoted:
            self._set_level(intent, DEMOTE_TO, "auto-demote")
            self.store.append_event(run_id, "playbook_demoted", {
                "intent": intent, "version": used[intent], "from": "A3", "to": DEMOTE_TO, "reason": reason})
        return demoted
