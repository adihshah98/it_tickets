"""Async Microsoft Graph client (port of tenant/graph.py).

Retry policy is Tech.MD §4.6, owned by code, never the model:
  - 429: honour Retry-After (up to 6 tries).
  - 500/502/503/504: 3 tries.
  - Replication lag (404, or 400 "...does not exist..." on an object we just created):
    only when the caller passes retry_replication=True; 5 tries, backoff over about a minute.
  - Anything else (403, other 400s): raise immediately.
  - No response (a timeout, a dropped connection): a GET is tried TRANSPORT_TRIES times; then, or at once for a
    write (which may have been applied), GraphTransportError. It is a GraphError, so every caller that handles
    Graph errors handles it too: the investigator tells the model the read failed, the executor fails the
    step, a DoD probe fails, instead of the exception leaving a run stuck mid-state.

Each wait is reported to the `on_retry` listener of the current context (the executor sets it per step,
so the workspace can show "↻ retried"). A context variable, because concurrent runs share one client.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from typing import Any

import httpx

GRAPH = "https://graph.microsoft.com/v1.0"

TokenProvider = Callable[[], Awaitable[tuple[str, float]]]  # -> (access token, expires_on epoch seconds)

THROTTLE_TRIES = 6
SERVER_ERROR_TRIES = 3
TRANSPORT_TRIES = 2  # Graph's sign-in log has taken over 60 s to answer (Build-Day16 live results)
REPLICATION_DELAYS = (4.0, 8.0, 16.0, 32.0)  # 5 tries, 60 s of waiting in total
TOKEN_REFRESH_MARGIN = 300

on_retry: ContextVar[Callable[[str, float], None] | None] = ContextVar("on_retry", default=None)


class GraphError(RuntimeError):
    def __init__(self, resp: httpx.Response):
        self.status = resp.status_code
        try:
            err = resp.json().get("error", {})
        except ValueError:
            err = {"message": resp.text}
        self.code: str = err.get("code", "")
        self.message: str = err.get("message", "")
        super().__init__(f"{resp.request.method} {resp.request.url} -> {self.status} {self.code}: {self.message}")


class GraphTransportError(GraphError):
    """No HTTP response from Graph (status 0): a timeout or a dropped connection."""

    def __init__(self, method: str, url: str, exc: httpx.TransportError):
        self.status = 0
        self.code = type(exc).__name__
        self.message = (f"no response from Graph ({exc or 'timed out'}); it may succeed if tried again"
                        if method == "GET" else
                        f"no response from Graph ({exc or 'timed out'}); the change may or may not have been applied")
        RuntimeError.__init__(self, f"{method} {url} -> {self.code}: {self.message}")


def odata_str(value: str) -> str:
    """Escape a string literal for an OData $filter."""
    return value.replace("'", "''")


def is_replication_lag(resp: httpx.Response) -> bool:
    if resp.status_code == 404:
        return True
    if resp.status_code == 400:
        try:
            message = resp.json().get("error", {}).get("message", "")
        except ValueError:
            return False
        return "does not exist" in message.lower()
    return False


class GraphClient:
    def __init__(
        self,
        tokens: TokenProvider,
        *,
        http: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._tokens = tokens
        self._http = http or httpx.AsyncClient(timeout=60)
        self._sleep = sleep
        self._token: tuple[str, float] | None = None

    async def _auth(self) -> dict[str, str]:
        if self._token is None or self._token[1] - time.time() < TOKEN_REFRESH_MARGIN:
            self._token = await self._tokens()
        return {"Authorization": f"Bearer {self._token[0]}"}

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        retry_replication: bool = False,
    ) -> Any:
        url = path if path.startswith("https://") else GRAPH + path
        throttled = server_errors = replication = transport = 0
        while True:
            try:
                resp = await self._http.request(
                    method, url, json=json, content=content, params=params,
                    headers=await self._auth() | (headers or {}),
                )
            except httpx.TransportError as exc:
                if method == "GET" and transport < TRANSPORT_TRIES - 1:
                    transport += 1
                    await self._wait("no_response", 2.0)
                    continue
                raise GraphTransportError(method, url, exc) from exc
            if resp.status_code == 429 and throttled < THROTTLE_TRIES - 1:
                throttled += 1
                await self._wait("throttled", float(resp.headers.get("Retry-After", 2**throttled)))
                continue
            if resp.status_code in (500, 502, 503, 504) and server_errors < SERVER_ERROR_TRIES - 1:
                server_errors += 1
                await self._wait("server_error", 2.0**server_errors)
                continue
            if retry_replication and is_replication_lag(resp) and replication < len(REPLICATION_DELAYS):
                await self._wait("replication", REPLICATION_DELAYS[replication])
                replication += 1
                continue
            if resp.is_error:
                raise GraphError(resp)
            if resp.content and "json" in resp.headers.get("content-type", ""):
                return resp.json()
            return None

    async def _wait(self, reason: str, seconds: float) -> None:
        if (listener := on_retry.get()) is not None:
            listener(reason, seconds)
        await self._sleep(seconds)

    async def get(self, path: str, **kw: Any) -> Any:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, body: Any = None, **kw: Any) -> Any:
        return await self.request("POST", path, json=body, **kw)

    async def patch(self, path: str, body: Any, **kw: Any) -> Any:
        return await self.request("PATCH", path, json=body, **kw)

    async def put(self, path: str, body: Any = None, **kw: Any) -> Any:
        return await self.request("PUT", path, json=body, **kw)

    async def delete(self, path: str, **kw: Any) -> Any:
        return await self.request("DELETE", path, **kw)

    async def list(self, path: str, params: dict[str, str] | None = None, **kw: Any) -> AsyncIterator[dict]:
        page = await self.get(path, params=params, **kw)
        while True:
            for item in page.get("value", []):
                yield item
            nxt = page.get("@odata.nextLink")
            if not nxt:
                return
            page = await self.get(nxt, **kw)

    async def first(self, path: str, params: dict[str, str] | None = None) -> dict | None:
        async for item in self.list(path, params):
            return item
        return None

    async def aclose(self) -> None:
        await self._http.aclose()


def certificate_tokens(tenant_id: str, client_id: str, certificate_path: str) -> TokenProvider:
    """App-only tokens from the certificate written by tenant/setup_app.py.

    Uses the sync credential in a thread: azure.identity.aio needs aiohttp, and a token
    is fetched about once an hour, so a second HTTP stack isn't worth it."""
    from azure.identity import CertificateCredential

    credential = CertificateCredential(tenant_id, client_id, certificate_path=certificate_path)

    async def provider() -> tuple[str, float]:
        token = await asyncio.to_thread(credential.get_token, "https://graph.microsoft.com/.default")
        return token.token, float(token.expires_on)

    return provider


def graph_from_settings(settings: Any) -> GraphClient:
    return GraphClient(certificate_tokens(settings.tenant_id, settings.client_id, str(settings.certificate_path)))
