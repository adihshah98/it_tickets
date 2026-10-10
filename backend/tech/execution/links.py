"""One-time links (Tech.MD §7): the only way a TAP leaves the executor.

The TAP is Fernet-encrypted at rest and expires with the TAP. Only the link id goes into events; the
token is the URL's secret part. Opening a link is two calls, so a link previewer (Safe Links, a chat
unfurl) can't burn it: `public_view` shows who it's for and when it expires, and `reveal` returns the
TAP once and destroys the ciphertext in the same conditional update.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import update
from sqlmodel import select

from tech.core.models import parse_ref
from tech.core.store import Link, NotFound, Store, utcnow
from tech.graph.writes import Deviation, Handler


def create(store: Store, *, key: str, run_id: int, step_id: str, tap: str, user_id: str, to: str,
           includes: list[str], note: str | None, expires_at: datetime) -> Link:
    link = Link(run_id=run_id, step_id=step_id, token=secrets.token_urlsafe(24),
                ciphertext=Fernet(key.encode()).encrypt(tap.encode()).decode(),
                user_id=user_id, to=to, includes=includes, note=note, expires_at=expires_at)
    with store.session() as s:
        s.add(link)
        s.commit()
        return link


def get(store: Store, run_id: int, step_id: str) -> Link | None:
    with store.session() as s:
        return s.exec(select(Link).where(Link.run_id == run_id, Link.step_id == step_id)).first()


def for_run(store: Store, run_id: int) -> list[Link]:
    with store.session() as s:
        return list(s.exec(select(Link).where(Link.run_id == run_id).order_by(Link.id)))


def decrypt(link: Link, key: str) -> str:
    return Fernet(key.encode()).decrypt(link.ciphertext.encode()).decode()


class Gone(Exception):
    """The link was already opened, or has expired."""


def by_token(store: Store, token: str) -> Link:
    with store.session() as s:
        link = s.exec(select(Link).where(Link.token == token)).first()
        if link is None:
            raise NotFound("no such link")
        return link


def as_utc(when: datetime) -> datetime:
    return when if when.tzinfo else when.replace(tzinfo=UTC)  # SQLite hands datetimes back naive


def expired(link: Link) -> bool:
    return as_utc(link.expires_at) <= utcnow()


def _upn(store: Store, link: Link) -> str | None:
    """The new user's sign-in name, from the user.create step that made them (onboarding)."""
    for event in store.events_of(link.run_id, "step_verified"):
        output = event.payload.get("output") or {}
        if event.payload.get("action") == "user.create" and output.get("id") == link.user_id:
            return output.get("userPrincipalName")
    return None


def public_view(store: Store, link: Link) -> dict[str, Any]:
    """What the link page shows before the reveal: never the TAP."""
    return {"to": link.to, "userPrincipalName": _upn(store, link), "expiresAt": as_utc(link.expires_at).isoformat(),
            "viewed": link.viewed_at is not None, "expired": expired(link), "includes": link.includes,
            "note": link.note}


def reveal(store: Store, token: str, key: str) -> tuple[Link, str]:
    """The TAP, once. Marks the link viewed and wipes the ciphertext in one conditional update, so two
    concurrent reveals can't both succeed. Raises NotFound for an unknown token, Gone after that."""
    link = by_token(store, token)
    if link.viewed_at is not None or expired(link):
        raise Gone("this link was already opened or has expired")
    tap = decrypt(link, key)
    now = utcnow()
    with store.session() as s:
        result = s.exec(update(Link).where(Link.id == link.id, Link.viewed_at.is_(None))
                        .values(viewed_at=now, ciphertext=""))
        s.commit()
    if result.rowcount != 1:
        raise Gone("this link was already opened")
    link.viewed_at, link.ciphertext = now, ""
    return link, tap


def _output(link: Link) -> dict[str, Any]:
    return {"linkId": link.id, "to": link.to, "expiresAt": link.expires_at.isoformat()}


class LinkSend(Handler):
    async def existing(self, ctx, p):
        link = get(ctx.store, ctx.run_id, ctx.step_id)
        return _output(link) if link else None

    async def act(self, ctx, p):
        if not ctx.link_key:
            raise Deviation("no LINK_KEY configured to encrypt the link")
        ref = parse_ref(p.get("tap"))
        tap = ctx.secrets.tap(ref[0]) if ref else ctx.secrets.tap_for_user(p["userId"])
        if tap is None:
            raise Deviation("the TAP for this link is not in memory (it was created by an earlier process, or "
                            "there is no tap.create step for this user). Delete the TAP and create a new one")
        link = create(ctx.store, key=ctx.link_key, run_id=ctx.run_id, step_id=ctx.step_id, tap=tap["value"],
                      user_id=p["userId"], to=p["to"], includes=list(p.get("includes") or []),
                      note=p.get("note"), expires_at=tap["expiresAt"])
        return _output(link)

    async def verify(self, ctx, p, output):
        link = get(ctx.store, ctx.run_id, ctx.step_id)
        ok = link is not None and link.viewed_at is None
        return ok, {"exists": link is not None, "viewed": bool(link and link.viewed_at)}
