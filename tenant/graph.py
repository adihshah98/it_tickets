"""Thin Microsoft Graph client shared by the tenant scripts.

Retries throttling and transient errors, follows paging, and can retry 404s
caused by directory replication lag on objects we just created.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterator

import httpx

GRAPH = "https://graph.microsoft.com/v1.0"
GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"  # Microsoft Graph's own appId

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE / ".env"
SECRETS_DIR = HERE / ".secrets"


class GraphError(RuntimeError):
    def __init__(self, resp: httpx.Response):
        self.status = resp.status_code
        try:
            err = resp.json().get("error", {})
        except ValueError:
            err = {"message": resp.text}
        self.code = err.get("code", "")
        self.message = err.get("message", "")
        super().__init__(
            f"{resp.request.method} {resp.request.url} -> {self.status} {self.code}: {self.message}"
        )


class Graph:
    def __init__(self, credential: Any, scopes: tuple[str, ...] = ("https://graph.microsoft.com/.default",)):
        self._cred = credential
        self._scopes = scopes
        self._token = None
        self._http = httpx.Client(timeout=60)

    def _auth(self) -> dict[str, str]:
        if self._token is None or self._token.expires_on - time.time() < 300:
            self._token = self._cred.get_token(*self._scopes)
        return {"Authorization": f"Bearer {self._token.token}"}

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        retry_404: int = 0,
    ) -> Any:
        url = path if path.startswith("https://") else GRAPH + path
        for attempt in range(8):
            try:
                resp = self._http.request(
                    method, url, json=json, content=content, params=params,
                    headers=self._auth() | (headers or {}),
                )
            except httpx.TransportError:  # Graph can take over 60 s to answer; a read is safe to repeat
                if method != "GET" or attempt >= 2:
                    raise
                time.sleep(2)
                continue
            if resp.status_code in (429, 502, 503, 504):
                time.sleep(float(resp.headers.get("Retry-After", 2**attempt)))
                continue
            if resp.status_code == 404 and attempt < retry_404:
                time.sleep(2**attempt)  # replication lag on a just-created object
                continue
            if resp.is_error:
                raise GraphError(resp)
            if resp.content and "json" in resp.headers.get("content-type", ""):
                return resp.json()
            return None
        raise GraphError(resp)

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.request("POST", path, json=body, **kw)

    def patch(self, path: str, body: Any, **kw: Any) -> Any:
        return self.request("PATCH", path, json=body, **kw)

    def put(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.request("PUT", path, json=body, **kw)

    def list(self, path: str, params: dict[str, str] | None = None, **kw: Any) -> Iterator[dict]:
        page = self.get(path, params=params, **kw)
        while True:
            yield from page.get("value", [])
            nxt = page.get("@odata.nextLink")
            if not nxt:
                return
            page = self.get(nxt, **kw)

    def first(self, path: str, params: dict[str, str] | None = None) -> dict | None:
        return next(self.list(path, params), None)


def q(value: str) -> str:
    """Escape a string literal for an OData $filter."""
    return value.replace("'", "''")


def load_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        raise SystemExit(f"{ENV_PATH} not found. Run setup_app.py first.")
    env = {}
    for line in ENV_PATH.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def app_graph() -> tuple[Graph, dict[str, str]]:
    """Graph client authenticated as the demo app (app-only, certificate)."""
    from azure.identity import CertificateCredential

    env = load_env()
    cred = CertificateCredential(
        tenant_id=env["AZURE_TENANT_ID"],
        client_id=env["AZURE_CLIENT_ID"],
        certificate_path=str(HERE / env["AZURE_CLIENT_CERTIFICATE_PATH"]),
    )
    return Graph(cred), env
