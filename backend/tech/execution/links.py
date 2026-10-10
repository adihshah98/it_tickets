"""One-time links (Tech.MD §7): the only way a TAP leaves the executor.

The TAP is Fernet-encrypted at rest and expires with the TAP. Only the link id goes into events; the
token is the URL's secret part. The view page (shown once, then destroyed) comes with the API and
close-out (Days 6 and 8).
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet
from sqlmodel import select

from tech.core.models import parse_ref
from tech.core.store import Link, Store
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


def decrypt(link: Link, key: str) -> str:
    return Fernet(key.encode()).decrypt(link.ciphertext.encode()).decode()


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
