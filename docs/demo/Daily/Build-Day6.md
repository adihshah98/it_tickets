# Day 6 plan: the API (REST + SSE)

## Context

Day 5 is committed (`9aac426`): `tech run` → `tech approve` executes Priya's onboarding against the tenant, and the Week 1 exit passes. Everything is driven by the CLI. The store already has an append-only, per-run `seq`-numbered event log, an in-process `EventBus`, and `Store.stream(run_id, after_seq)` (replay, then live, with no gap). Links and follow-ups exist, but nothing serves them.

Day 6 ([Build-W1-2.md](Build-W1-2.md), Day 6) puts an HTTP face on the runner so the Day 7 Next.js workspace can drive a run: paste a ticket, watch the event stream, approve with confirm answers, "run now" the license follow-up, and open the one-time link.

**Outcome:** `uv run uvicorn tech.api:create_app --factory` exposes all of this. A run can be started, watched over SSE, approved and finished with curl alone. The CLI keeps working on the same store.

## Decisions

- **Use FastAPI's native SSE** (`fastapi.sse.EventSourceResponse` + `ServerSentEvent`, available in the installed fastapi 0.143). Keepalive comments are built in. No new dependency.
- **Wire format:** `id: <seq>`, `event: <type>`, `data: <payload JSON>`. A reconnect resumes from the `Last-Event-ID` header, or from `?after=<seq>` on the first connect. `GET /runs/{id}` returns `last_seq`, so the UI fetches the snapshot, then streams `?after=last_seq`.
- **The stream ends when the run is finished.** Once the run is DONE or ESCALATED *and* no background task is working on it, the stream yields `event: end` and closes. A run that is already terminal replays its history, then ends. The client calls `es.close()` on `end`, which stops EventSource's automatic reconnect loop. During "run now" on a DONE run, the follow-up task keeps the stream open until it finishes. The UI opens the stream with `?after=last_seq` after posting. This also keeps HTTP tests finite.
- **Long work runs as tracked asyncio tasks, not `BackgroundTasks`.** `TaskRunner` on `app.state` holds the tasks. `spawn(run_id, coro)` wraps each one: on an unexpected exception it calls `logfire.exception`, appends an `error` event, and moves a live run to ESCALATED with `kind="error"`. This means a crashed task never leaves a run stuck in INVESTIGATING. Shutdown cancels the tasks. `drain()` exists for tests.
- **Approval is checked synchronously, then executed in the background.** Split `runner.approve` into:
  - `runner.accept(rt, run_id, *, by, answers, reject) -> Plan | None`: everything up to and including the `approval` event, plus the transition (to EXECUTING on approve, to ESCALATED on reject). It never awaits between the state check and the transition, so a second concurrent POST gets a 409.
  - `runner.approve` becomes `accept` followed by `execute` (renamed from `_execute`, now that the API calls it). The CLI is unchanged. `execute` already skips the transition when the run is in EXECUTING.

  The API calls `accept` inline: errors come back as HTTP errors immediately. Then it spawns `execute`.
- **Errors:** `RunError` → 409 with `detail`; `NotFound` → 404; request validation → 422 (FastAPI's default). `NotFound` is a new `KeyError` subclass that the store, the scheduler and links raise for unknown ids. A plain `KeyError` from a bug stays a 500 rather than a misleading 404, and the CLI's `except KeyError` still catches it.
- **One-time link: look, then reveal.** A plain view-once `GET` gets burned by link previewers (Outlook Safe Links, Slack unfurls). So:
  - `GET /l/{token}` returns metadata only: `to`, the new user's UPN (from the `user.create` step's `step_verified` output), `expires_at`, `viewed`, `includes`, `note`.
  - `POST /l/{token}/reveal` returns the TAP once. It runs a conditional `UPDATE link SET viewed_at=now, ciphertext='' WHERE token=? AND viewed_at IS NULL AND expires_at > now`. If no row matches, it answers 410. This pulls the "then destroyed" part of Day 8 forward, because it costs one line.
  - Reveal appends a new `link_viewed` event `{link_id, step_id, at}` (added to `EVENT_TYPES`), so the workspace can show "Taylor opened the link".
  - This changes Build-W1-2's `GET /l/{token}` (update that doc).
- **Keep the token out of traces.** `logfire.instrument_fastapi(app, excluded_urls="/l/.*")`, because the URL path *is* the secret. The TAP never goes into a span: the reveal response body isn't captured, and the existing scrubbing covers `tap` and `token` attribute names.
- **The snapshot shows the link URL** (`{WEB_ORIGIN}/l/{token}`) while the link is unviewed. The demo presenter needs to click it, and Day 8's reply draft will carry it anyway. Once viewed, it shows only `viewed_at`.
- **Shared runtime construction.** Move the CLI's setup into `runner.Runtime.from_settings(settings)`: Logfire environment, `ANTHROPIC_API_KEY`, the `data/` directory, Graph, facts and link key. `cli.with_runtime` and the API lifespan both call it.
- **App factory.** `create_app(runtime: Runtime | None = None) -> FastAPI`, served with `uvicorn --factory`. With no runtime, it builds one from settings (`environment="api"`), instruments FastAPI, and closes Graph on shutdown. Tests pass their own runtime. There is no module-level `app`, so importing `tech.api` needs no tenant settings.
- **CORS** for the Day 7 dev server: allow `WEB_ORIGIN`, a new optional setting that defaults to `http://localhost:3000`.

## Endpoints (`backend/tech/api.py`)

| Method & path | Does | Returns |
|---|---|---|
| `POST /tickets` `{text, from}` | creates the ticket and run (a bare name gets `@domain`, as in the CLI), spawns `runner.start` | 202 `{run_id, ticket_id}` |
| `GET /runs?limit=50` | recent runs for the inbox: id, state, from, the first line of the ticket, timestamps | 200 list |
| `GET /runs/{id}` | snapshot (below) | 200 / 404 |
| `GET /runs/{id}/events` | SSE: replay after `Last-Event-ID` or `?after`, then live, then `end` at a terminal state | `text/event-stream` |
| `POST /runs/{id}/approval` `{by, decision: approve\|reject, answers: {}}` | `runner.accept` inline, then spawns `execute` on approve | 202 `{state}` / 409 |
| `POST /runs/{id}/resume` | spawns `runner.resume` (for a run a dead process left in EXECUTING) | 202 / 409 |
| `POST /followups/{id}/run-now` | 409 if already done; otherwise spawns `runner.run_followup`. A failure appends an `error` event but never escalates the (DONE) run | 202 / 404 / 409 |
| `GET /l/{token}` | link metadata, without the secret | 200 / 404 / 410 expired |
| `POST /l/{token}/reveal` | the TAP, once, plus sign-in hints (UPN, expiry) | 200 / 404 / 410 |

**Snapshot** (`snapshot(store, run_id, web_origin)`, a pure function in `api.py`, built from events with the existing `load_plan`, `load_triage` and `events_of` helpers):

```text
run {id, state, created_at, updated_at}  ticket {id, text, from}  last_seq
triage, requester_approved, findings[]
plan (as proposed) + effective answers (the last approval's answers)
steps[]: {id, action, params, rationale, depends_on, risk_class,
          decision, rule_id, reason,                       <- from gate_decision
          status: pending|running|verified|skipped|failed, output, error, observed}
needs_technician, confirm_on_approval, questions_for_requester
approval (the last approval payload or null)
outcome: the last state payload when ESCALATED or HELD (kind, reason, rollback, ...)
followups[] {id, step_id, kind, run_at, status}
links[] {id, step_id, to, expires_at, viewed_at, url (only while unviewed)}
```

## Files

- **New `backend/tech/api.py`:** `create_app`, `TaskRunner`, the routes, `snapshot`, and `event_stream(store, run_id, after_seq)`. `event_stream` is an async generator over `Store.stream` that yields `ServerSentEvent`s and stops after `end`. It is unit-testable without HTTP.
- **`backend/tech/runner.py`:** split out `accept`, and add `Runtime.from_settings`.
- **`backend/tech/cli.py`:** `with_runtime` uses `Runtime.from_settings`. The docstring mentions the API.
- **`backend/tech/execution/links.py`:** add `by_token`, `public_view(link, store)` and `reveal(store, token, key) -> (link, tap)`, which raises `Gone` or `KeyError`. Update the module docstring.
- **`backend/tech/core/store.py`:** add `"link_viewed"` to `EVENT_TYPES`, and `list_runs(limit)` (joined with the ticket).
- **`backend/tech/core/config.py`:** add an optional `web_origin` setting (`WEB_ORIGIN`).
- **`backend/tests/conftest.py`:** move the `rt` and `gated` fixtures here from `test_runner.py`, so the API tests can reuse them.

## Tests (`backend/tests/test_api.py`, offline)

Use `httpx.AsyncClient(transport=ASGITransport(app))` with `create_app(runtime=rt)`, the fake tenant, and `await app.state.tasks.drain()` after each spawning call.

- **Tickets.** POST returns 202 with a run id. `runner.start` (monkeypatched with a fake that appends a triage event and goes to TRIAGED) receives the text and a `@domain`-completed sender.
- **Crash guard.** A `start` that raises leads to an `error` event, then ESCALATED with `kind="error"`.
- **Snapshot.** On a `gated()` run: the state is AWAITING_APPROVAL, every step carries its gate decision, the purchase and the TAP read `approve`, and `last_seq` equals the last event. An unknown run is a 404.
- **Approval.**
  - Approve with `upn_format=pnair`, then drain: the run is DONE, `pnair@` exists in the fake tenant, every step's status is `verified`, and no event and no snapshot contains `TAP_VALUE`.
  - A second POST is a 409, whether it comes before or after the drain.
  - A non-approver is a 409, and the run stays in AWAITING_APPROVAL.
  - Reject leads to ESCALATED with `kind="rejected"`, and the tenant gets no calls.
- **Run now.** 202, then the follow-up is done and Priya has the SKU. Running it again is a 409.
- **Link.**
  - GET shows metadata with no TAP.
  - Reveal returns `TAP_VALUE` once, and the stored ciphertext is now empty.
  - A second reveal is a 410, and so is a reveal on an expired link.
  - An unknown token is a 404.
  - A `link_viewed` event is recorded and doesn't contain the TAP.
- **SSE, on `event_stream` directly:**
  - Replay comes in seq order, with `id == seq`.
  - `after=n` resumes after n.
  - Events appended concurrently while the stream is live arrive once, in order.
  - On a run reaching DONE, the stream yields `end` and stops.
- **SSE over HTTP**, on a DONE run (so the response ends):
  - `text/event-stream` content, with parsed ids `1..N` and `end` last;
  - `Last-Event-ID: k` replays only `k+1..N`.
- **`test_runner.py`:** a new test checks that `accept` alone records the approval and moves to EXECUTING without touching the tenant. The existing `approve` tests (now `accept` + `execute`) pass unchanged.

## Verification

1. Run `uv run pytest` and `uv run ruff check backend`.
2. Reset the tenant with `uv run tenant/reset.py`, then start the server with `uv run uvicorn tech.api:create_app --factory --port 8000 --no-access-log`.
3. Start a run:

   ```
   curl -XPOST localhost:8000/tickets -H 'content-type: application/json' -d '{"text": "<Priya ticket>", "from": "tgarcia"}'
   ```

4. In another terminal, run `curl -N localhost:8000/runs/<id>/events`. You should see triage, tool calls, findings, the plan and the gate decisions stream in, ending in AWAITING_APPROVAL. The stream stays open.
5. Approve:

   ```
   curl -XPOST .../runs/<id>/approval -d '{"by": "tgarcia", "decision": "approve", "answers": {"upn_format": "priya.nair"}}'
   ```

   The open stream should show each step's `step_started` and `step_verified`, then DONE, then `end`.
6. Kill and reconnect with `curl -N -H 'Last-Event-ID: 10' ...`. Only the events after seq 10 should come back.
7. Fetch `GET /runs/<id>`. The steps should all be verified, with a follow-up and a link URL.
8. Run `POST /followups/<fid>/run-now`. Priya gets the license, which you can check in Entra.
9. Run `GET /l/<token>`: no TAP. Then `POST /l/<token>/reveal`: the TAP. Reveal again: 410.
10. Search `data/tech.db` and `.logfire/` for the TAP value and the link token. Neither should appear.
11. Run `uv run tech approve <id>` against the same database. The CLI still works and refuses the DONE run.

## Risks

- **The event bus is in-process.** A run started from the CLI replays over the API's SSE, but it won't push live events into the API process. This is acceptable for the demo, where everything goes through the API. If it matters later, `Store.stream` can fall back to polling the database every 0.5 s.
- **Synchronous SQLite calls block the event loop for milliseconds.** This is fine at demo scale. Revisit together with Postgres.
- **Uvicorn's access log prints `/l/<token>` paths.** Run the server with `--no-access-log` in the demo, or filter that path. Once a link is revealed, its token is worthless anyway.
- **Contacts for the inbox's sender picker** aren't in client facts. Day 7 decides whether that list comes from a `GET /contacts` (Graph `users.list`) or is hardcoded.
