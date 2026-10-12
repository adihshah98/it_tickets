# Day 7 plan: the workspace UI (Next.js)

## Context

Day 6 is committed (`5b22880`): `uvicorn tech.api:create_app --factory` serves tickets, run list, snapshot, an SSE event stream (replay after `Last-Event-ID`/`?after`, live, then `end` at DONE/ESCALATED), approval with confirm answers, resume, follow-up run-now, and the look-then-reveal one-time link. CORS already allows `WEB_ORIGIN` (default `http://localhost:3000`). There is no `web/` yet; the demo can only be driven with curl.

Day 7 ([Build-W1-2.md](Build-W1-2.md), Day 7) builds the screen the demo happens on: paste Priya's ticket as Taylor, watch triage → investigation → plan → gate stream in, approve on one card (confirming `priya.nair@`), watch every step verify, click "run now" on the license follow-up, and open the one-time link.

**Outcome:** `npm run dev` in `web/` plus the API runs the whole onboarding from the browser. No curl, no CLI.

## Decisions

- **Stack.** `web/` from `create-next-app@16.4.0` (Next 16.4, React 19.3, App Router, TypeScript, Tailwind 4, ESLint, npm). Cache Components is on by default, so each dynamic route is a server `page.tsx` that renders its client component (which reads `useParams`) inside `<Suspense>`. No component library: the screen is a few cards and lists. Vitest for the pure logic.
- **The browser talks to the API directly** at `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`). CORS is already set up, and a Next rewrite proxy can buffer SSE. Every data page is a client component; nothing is fetched server-side.
- **One hook drives the workspace: `useRun(id)`.**
  - Fetch `GET /runs/{id}` (the snapshot), and open `EventSource(/runs/{id}/events?after=0)`. Full replay, because the snapshot has findings but not tool calls, timings or step attempts. EventSource's auto-reconnect sends `Last-Event-ID` itself.
  - A pure reducer, `reduce(view, event)` in `lib/run.ts`, builds what the snapshot lacks: the investigation log (tool calls and findings interleaved, with `t`), lint attempts, per-step attempts (two `step_started` → "retried"), the current state, and the elapsed time. Events with `seq <= last` are dropped.
  - The snapshot stays the source for derived data (effective params after answers, gate decisions merged onto steps, follow-ups, links, outcome). It is re-fetched, debounced ~200 ms, on `state`, `plan`, `approval`, `step_verified`, `step_failed`, `link_viewed` and `end`. No re-implementing `runner.effective_plan` in TypeScript.
  - On `end`: `es.close()` (stops the reconnect loop). After a "run now" on a DONE run, the hook reopens the stream with `?after=<last seq>`.
- **Senders and samples are hardcoded** in `web/lib/samples.ts`, which closes Day 6's open risk without a `/contacts` endpoint. The API already completes a bare alias with `@domain` (tickets and approval `by`), so the web app never needs the tenant domain. Senders: Taylor (`tgarcia`, approver), Jordan (`jlee`, not an approver), Kevin, Dennis, Sophie. Samples: Priya onboarding, the injection variant (Product.MD 3.5), and Kevin offboarding from Jordan (shows HELD; the plan itself is Week 3).
- **Approval card.**
  - Lists only the steps whose gate decision is `approve`, highlighted with the rule id and reason; auto steps collapse to a count. A `block` decision disables the card and says why (the API refuses it anyway).
  - Confirm questions render from `plan.confirm_on_approval`. Only `upn_format` and `start_date` (the API's `ANSWER_KEYS`) are editable fields, prefilled with `proposed` and showing `evidence`. **Only fields the approver changed are sent** (`answersFor`): a proposal is the model's prose (the live run proposed `start_date` as "2026-10-12; license assigned at 11:00 UTC…"), which `apply_answers` would refuse, and the plan already carries it. `license_purchase` and `other` render as read-only acknowledgements that approval covers.
  - "Approving as" select, defaulting to `tgarcia`. Approve / Reject. A 409 shows its `detail` inline (wrong approver, bad answer) and leaves the card open.
  - HELD shows the same card under a "requester isn't an approver; an approver must release this" banner (`accept` allows HELD).
- **Minimal link page now, not Day 8.** The snapshot's link URL already points to `{WEB_ORIGIN}/l/{token}`, so the presenter's click needs a page today. `/l/[token]`: `GET` metadata (to, UPN, expiry) and a "Show my temporary access pass" button → `POST …/reveal` → the TAP plus the sign-in steps; 404/410 get plain messages. `noindex`, `Referrer-Policy: no-referrer`, the TAP held only in component state. Day 8 keeps the reply draft, scanner and DoD.
- **Inbox list polls `GET /runs` every 3 s.** No inbox stream; it's a demo-scale list.
- **No backend changes planned.** If the UI finds a gap, it goes into `snapshot()` with a test, not into the client.

## Screens

| Route | Shows |
|---|---|
| `/` (inbox) | Paste box + sender select + sample buttons → `POST /tickets` → navigate to `/runs/{id}`. Below: recent runs (state badge, sender, subject, age). |
| `/runs/[id]` (workspace) | In order, each section appearing as its events arrive: **header** (state badge, sender, elapsed timer against the 3-minute bar); **ticket**; **Understood** (intents, new user, peer, requester ✔/✖, out-of-scope asks in red); **Investigation log** (tool call rows with args, collapsed result, ✖ on error; findings in bold; lint retries); **Plan** (summary, assumptions, steps with action, params, rationale, expected, `auto`/`approve`/`block` label and rule); **Approval card**; **Live execution** (per step ✔ verified, ↻ retried, ⤼ skipped "already true", ✖ failed with error and observed); **Follow-ups** (run_at, status, "Run now"); **One-time link** (URL while unviewed, then "opened at …"); **Needs a technician**; **Outcome** when ESCALATED/HELD (kind, reason, rollback). |
| `/l/[token]` | The one-time link page above. |

## Files (`web/`)

- `app/layout.tsx`, `app/globals.css`: shell, header link to the inbox.
- `app/page.tsx`: inbox.
- `app/runs/[id]/page.tsx`: workspace, composed from `components/`.
- `app/l/[token]/page.tsx`: link page.
- `components/`: `StateBadge`, `Understood`, `InvestigationLog`, `PlanSteps`, `ApprovalCard`, `Execution`, `Followups`, `LinkStatus`, `NeedsTechnician`, `Outcome`.
- `lib/api.ts`: typed fetch wrappers; non-2xx throws `ApiError(status, detail)`.
- `lib/types.ts`: `Snapshot`, `Step`, `Plan`, `Triage`, event payloads, mirroring `snapshot()` in `backend/tech/api.py` and `core/models.py`.
- `lib/run.ts`: `reduce`, `initialView`, `answersFor(confirmations, edits)` (only `ANSWER_KEYS`, trimmed, empty dropped). Pure.
- `lib/useRun.ts`: the hook (snapshot + EventSource + debounced refetch + reopen).
- `lib/samples.ts`: senders and sample tickets.
- `tests/run.test.ts`, `tests/fixtures/onboard.sse`: an event stream captured from a live run with `curl -N …/events` (no secrets: the TAP and the link token never enter events).
- Root: add `web/node_modules`, `web/.next` to `.gitignore`; README gets the two-terminal run instructions. Update Build-W1-2 Day 7 (link page moved here, senders hardcoded) and Day 8 (link page already exists).

## Tests (`web/tests/run.test.ts`, vitest, offline)

- Replaying the fixture builds the log in seq order, tool calls and findings interleaved, and ends with state DONE.
- A step with two `step_started` before its `step_verified` reads "retried"; a `skipped` verify reads "skipped".
- A re-delivered event (`seq <= last`) is dropped, so a reconnect doesn't duplicate rows.
- `lint_result` with problems shows as a retry in the log.
- `answersFor`: keeps `upn_format` and `start_date`, drops `license_purchase`/`other`, trims, and drops empty values.

Plus `npx tsc --noEmit`, `npm run lint` and `npm run build` clean.

## Verification

1. `uv run pytest` (backend unchanged, stays green) and, in `web/`, `npm test`, `npx tsc --noEmit`, `npm run lint`, `npm run build`.
2. `uv run tenant/reset.py`, then `uv run uvicorn tech.api:create_app --factory --port 8000 --no-access-log` and `npm run dev` in `web/`.
3. Open `localhost:3000`, pick the Priya sample as Taylor, submit. The workspace shows Understood, then tool calls and findings live, then the plan with labels; the purchase and TAP steps are highlighted on the card; the UPN field shows `priya.nair`.
4. Try approving as Jordan: an inline 409, card stays. Approve as Taylor: steps tick to ✔ verified one by one, state reaches DONE, the timer stops.
5. Reload mid-run and after DONE: the page rebuilds identically from replay.
6. "Run now" on the license follow-up: it completes and the stream closes again; Priya has the license in Entra.
7. Click the link: metadata, then reveal shows the TAP once; reload shows "already viewed"; the workspace shows "opened at …".
8. Injection sample: the Global Admin ask shows under out-of-scope asks. Offboarding from Jordan: HELD banner.
9. Read the elapsed time against the under-3-minute bar and note it for Day 9.

## Risks

- **Runs saved before Day 5 don't load** (runs 1-6 in `data/tech.db`): their plans have a 1440-minute TAP, which the Day 5 cap (480) now rejects, so `GET /runs/{id}` is a 500. A 500 also has no CORS headers, so the browser sees it as unreachable; the UI says "no response (not running, or a server error)". Start the demo from a fresh database or ignore them.

- **Event payload shapes drift from `lib/types.ts`.** Types are hand-written; the fixture test catches drift on the onboarding path. Generating types from FastAPI's OpenAPI doesn't help: the snapshot is `dict[str, Any]`.
- **Large `tool_call` results** (e.g. `users_list`) slow the replay render. Results stay collapsed and render on expand only.
- **Next.js takes longer than planned** (Build-W1-2 risk). The hook and reducer are framework-agnostic; cut the inbox list and the link page polish first.
