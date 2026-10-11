# it_tickets

Planning docs for an AI-native technician for MSPs, and the path to an AI-native MSP OS (PSA, RMM, billing, quoting).

- [docs/overall/product-plan.md](docs/overall/product-plan.md): thesis, customer, product, roadmap to OS, pricing, GTM, competition, metrics
- [docs/overall/tech-plan.md](docs/overall/tech-plan.md): architecture, integrations, agent loop, safety model, evals, 12-month build plan
- [docs/overall/ticket-coverage.md](docs/overall/ticket-coverage.md): every ticket in the April 2026 sample invoice mapped to intent, autonomy level, tooling and prevention fix
- [docs/overall/invoice-learnings.md](docs/overall/invoice-learnings.md): what the sample invoice teaches about how MSP work actually happens, and the per-playbook learnings for the demo
- [docs/demo/Product.MD](docs/demo/Product.MD) and [docs/demo/Tech.MD](docs/demo/Tech.MD): end-to-end happy-path demo (onboarding, offboarding, MFA reset, and an unknown ticket it learns a playbook from) with a guided agent, gated plan and verified execution; diagnose mode and devices are next (v2)

## Running the demo

```bash
uv run tenant/reset.py                                                          # tenant back to the seeded state
uv run uvicorn tech.api:create_app --factory --port 8000 --no-access-log       # API
cd web && npm install && npm run dev                                            # UI at http://localhost:3000
```

Build log: [Build-W1-2.md](docs/demo/Build-W1-2.md), then Build-Day4 through [Build-Day7.md](docs/demo/Build-Day7.md).
