# it_tickets — The AI Technician

An AI technician for MSPs: it reads a support ticket, investigates it against a real Microsoft 365 tenant
with read-only tools, proposes a typed plan, has that plan checked by deterministic safety code and approved
by a human, then executes it — snapshotting, acting, and verifying every change by reading it back — and
closes out with a reply, a PSA note, and a definition-of-done check.

The guiding principle, which explains every design choice in the code:

> **The model decides and plans; typed, tested code acts. The model can never call a write tool.**

The longer-term thesis — an AI-native MSP OS (PSA, RMM, billing, quoting) — is in the planning docs below.

## Start here

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — a ground-up tour of the code: directory → file → function → how it
  flows → why it's shaped that way. Read this to understand the system.
- **[docs/overall/expansion-to-multi-customers.md](docs/overall/expansion-to-multi-customers.md)** — what has
  to change to turn this (one MSP, one tenant) into a product MSPs run across all of their clients.

## Planning docs

- [docs/overall/product-plan.md](docs/overall/product-plan.md): thesis, customer, product, roadmap to OS,
  pricing, GTM, competition, metrics
- [docs/overall/tech-plan.md](docs/overall/tech-plan.md): architecture, integrations, agent loop, safety
  model, evals, 12-month build plan
- [docs/overall/ticket-coverage.md](docs/overall/ticket-coverage.md): every ticket in the April 2026 sample
  invoice mapped to intent, autonomy level, tooling and prevention fix
- [docs/overall/invoice-learnings.md](docs/overall/invoice-learnings.md): what the sample invoice teaches
  about how MSP work actually happens
- [docs/demo/Product.MD](docs/demo/Product.MD) and [docs/demo/Tech.MD](docs/demo/Tech.MD): the end-to-end
  demo design (onboarding, offboarding, MFA reset, and an unknown ticket it learns a playbook from)

## Layout

```
backend/tech/   the engine (agents, safety, execution, Graph, playbooks)   — see ARCHITECTURE.md
backend/tests/  256 tests (offline) + a `live` tier against the demo tenant
web/            Next.js console (server-sent events + a pure state reducer)
tenant/         scripts to set up / seed / reset the demo Microsoft 365 tenant
spike/          model-risk experiments
docs/           product + tech plans, and the day-by-day build log
```

## Running the demo

```bash
uv run tenant/reset.py                                                     # tenant back to the seeded state
uv run uvicorn tech.api:create_app --factory --port 8000 --no-access-log  # API
cd web && npm install && npm run dev                                       # UI at http://localhost:3000
```

Or headless, from the CLI:

```bash
uv run tech run "hey team, we have a new hire starting monday..." --from tgarcia@<domain>
uv run tech approve 1 --confirm upn_format=priya.nair
```

## Development

```bash
uv run pytest          # the offline suite (256 tests)
uv run pytest -m live  # the live scenarios (run after tenant/reset.py)
uv run ruff check backend
```

Build log: [Build-W1-2.md](docs/demo/Build-W1-2.md) through [Build-Day8.md](docs/demo/Build-Day8.md), then
the Week 3 plan [Build-W3.md](docs/demo/Build-W3.md).
