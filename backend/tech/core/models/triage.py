"""Triage (Tech.MD §4.0): the intake agent's typed output, extracted from the ticket text."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from tech.core.models.base import IntentSlug


class NewUser(BaseModel):
    display_name: str
    department: str | None = None
    job_title: str | None = None
    start_date: date | None = Field(None, description="Resolved to a date, e.g. 'monday' -> the coming Monday")


class TriageEntities(BaseModel):
    new_user: NewUser | None = None
    peer_user_hint: str | None = Field(None, description="The coworker the requester said to copy, as written")
    subject_user_hint: str | None = Field(None, description="The leaver or locked-out user, as written")
    apps: list[str] = []


class Requester(BaseModel):
    name: str
    email: str


class Triage(BaseModel):
    intents: list[IntentSlug]
    entities: TriageEntities
    requester: Requester
    out_of_scope_asks: list[str] = Field(default=[], description="Anything asked for beyond the ticket type, quoted")
    missing_info: list[str] = []
