# Expansion to Multi-Customers — From One Tenant to a Product MSPs Run

*Last updated: 2026-10-12*

Companion docs: [tech-plan.md](tech-plan.md) · [product-plan.md](product-plan.md)

---

## 0. The reframing

We are **not** an MSP that resolves tickets. We sell **the platform an MSP runs to resolve tickets for
their own clients.** That is a different product, and the difference is structural, not cosmetic: it
lives in the data model, not the sales deck.

The demo today is built as **one MSP operating on one of its own tenants**. There is exactly one layer
of "client" in the code where the product needs two:

- the **MSP** — our paying customer (billing, SSO, kill switch, their technicians);
- the **MSP's client** — the end business whose Microsoft 365 tenant actually gets changed (its own
  credentials, approvers, facts, playbooks, autonomy).

```
Today:                          What we sell:

[ one .env + one YAML set ]     Platform (us)
        │                         └─ MSP account (our customer)      ← billing, SSO, kill switch, techs
   one Graph app                       ├─ client "Northwind"         ← its own tenant, facts, playbooks
        │                              ├─ client "Acme"              ← isolated credentials (GDAP)
   one tenant                          └─ client "Contoso"           ← its own approvers, autonomy
```

Everything singular in the code today — the Graph app, `client_facts.yaml`, the playbooks directory,
the SQLite file, the auth-less API — has to become **"which MSP, which of their clients"** on every
request.

---

## 1. What this means for the code as it stands

Grounded in the current single-tenant assumptions (not a wishlist — these are the exact places the code
assumes one customer):

| File / symbol | Single-tenant assumption today | Why it blocks multi-customer |
| --- | --- | --- |
| [core/config.py](../../backend/tech/core/config.py) `Settings.load()` | Reads **one** `tenant/.env`: one `tenant_id`, `client_id`, cert, `domain` | A process can only ever talk to one client tenant |
| [runner.py](../../backend/tech/runner.py) `Runtime.from_settings` → `graph_from_settings` | Builds **one** `GraphClient` from one app cert | No way to reach a second client tenant in the same deployment |
| [playbooks/loader.py](../../backend/tech/playbooks/loader.py) `load_client_facts(domain)` / `load_playbooks(...)` | Reads YAML files from the repo, `{domain}`-substituted | Facts/playbooks are shared global files, not per-client |
| [agents/investigator.py](../../backend/tech/agents/investigator.py) `INSTRUCTIONS` | Hard-codes `"The client is Northwind Parking"` | The agent believes there is one client |
| [core/store.py](../../backend/tech/core/store.py) `Ticket` / `Run` / `Event` | No `msp_id` / `client_id`; `run_id` globally unique; SQLite | No isolation boundary; any run is visible to any caller |
| [api.py](../../backend/tech/api.py) `create_app` | No authentication on any endpoint; `approval.by` is a free string | Anyone can drive any run; no notion of *who* or *for which MSP* |
| [safety/gate.py](../../backend/tech/safety/gate.py) `BLAST_RADIUS` / `MAX_WRITES` | Module-level constants | Bounds and kill switch must be per-client, not global |

---

## 2. What has to change

### 2.1 Identity & isolation — the whole premise

- **Credentials resolved per (MSP, client) at run time, from a vault — never from a file.** The
  tech-plan already calls for per-MSP vault namespaces ([tech-plan.md §3](tech-plan.md), §6 Secrets);
  for multi-customer it stops being hygiene and becomes the product boundary. `Settings.load()` gives
  way to a resolver: `credentials_for(msp_id, client_id) -> GraphCredential`.
- **A `GraphClient` factory keyed by the client tenant**, not one client built at startup. For real MSP
  use this is **Microsoft GDAP** (Granted Delegated Admin Privileges): one multi-tenant Entra app,
  reaching each client tenant through the partner relationship, scoped to the action family. This is
  the single largest integration lift and it is MSP-specific — it does not exist in the demo at all.
- **Facts and playbooks become rows owned by (MSP, client)**, not repo files. The existing
  `ClientFacts` and `Playbook` Pydantic models in [loader.py](../../backend/tech/playbooks/loader.py)
  are already the right schemas — they need a storage layer and an editor, not a redesign.
- **Store isolation enforced in code.** `msp_id` + `client_id` on `Ticket`/`Run`/`Event`, Postgres,
  and every query scoped so an MSP can never read another MSP's runs. **No cross-tenant tool calls in a
  run** — the tech-plan asserts this; here it must be enforced, not assumed.

### 2.2 Authn / authz — currently absent

The API has **no auth today**. A sellable product needs:

- **MSP login / SSO** — MSPs expect SAML/OIDC (Entra, Okta).
- **Roles**: MSP admin, MSP technician, and the **client's approver** (e.g. Northwind's `tgarcia@`).
  The `approvers` map in [client_facts.yaml](../../backend/tech/playbooks/client_facts.yaml) already
  models the client-side approver — crucially, that person works at our customer's customer, and will
  usually approve via an email/Teams link, not our console.
- Every API call scoped to "this technician, acting as MSP X, on client Y." `approval.by` becomes an
  authenticated identity, not a free string.

### 2.3 Onboarding a new client (the product's first-run experience)

Today "set up a tenant" means running `tenant/setup_app.py` + `seed.py` by hand against a dev tenant.
For MSPs this becomes a product flow:

1. MSP clicks **add client** → GDAP consent handshake → the app can now reach that tenant.
2. **Crawl** the tenant to build the environment graph (users, groups, licenses) — the tech-plan's §4
   "crawled" path.
3. Facts start empty and get **declared** (approvers, conventions) and **extracted** (from ticket
   history). The demo's hand-written `client_facts.yaml` is the *output* of this flow; the product must
   produce it per client.

### 2.4 Playbooks: global vs per-client (a real product decision)

Today playbooks are one flat directory. For multi-customer they layer:

- **We** ship a baseline library (~30 intents — the tech-plan target).
- Each **MSP** overrides or disables per client.
- The **learned-playbook loop** (Week 4) now has a scoping question: a playbook a Northwind run produces
  — does it stay private to that MSP+client, get promoted to the MSP's library, or (with consent) to our
  global baseline? The gate's `allowed_actions` and per-client `autonomy` already give the override
  mechanism; what is missing is the **layering + review workflow** on top.

> **Open decision:** three tiers (our global baseline → MSP overrides → per-client) or two?

### 2.5 Billing, limits, kill switch

- **Metering per MSP** (likely per their client too) — resolutions and API cost. The event log already
  captures usage per run (`plan` events carry token counts); what is missing is aggregation and a
  billing boundary.
- **Per-MSP and per-client kill switch** — the tech-plan names it; the gate is the natural hook (a
  global `block` rule), but it does not exist yet.
- Blast-radius bounds (`MAX_WRITES` / `BLAST_RADIUS` in [gate.py](../../backend/tech/safety/gate.py))
  become **per-client configurable**, not constants.

### 2.6 Observability & audit, scoped

- Logfire tracing exists; it needs an **MSP/client dimension** on every span, so we can debug and an MSP
  sees only their own traces.
- The audit log (the event stream) must be **exportable per client**, for the MSP to hand to *their*
  client's auditors. This is a sales feature, not just hygiene.

### 2.7 Get tickets from where MSPs actually work — PSA

MSPs live in ConnectWise / Autotask / Halo. Tickets must **arrive from and close back into** the PSA,
not a bespoke console. Without this the product is not in an MSP's workflow, however good the agent is.
Co-equal in priority with GDAP for actual adoption.

---

## 3. What does NOT change (the good news)

The expensive, hard-to-retrofit decisions are already right and are **tenancy-agnostic**:

- **The safety architecture** — typed plan union, read-only investigator, linter grounding, policy
  gate, verify-everything, secret guard, DoD — is identical for one tenant or ten thousand. None of it
  assumes a single client.
- **Event sourcing** — adding `msp_id` / `client_id` columns is additive; replay / resume / audit are
  unchanged.
- **The run state machine** — per-run, already isolated.
- **`ClientFacts` / `Playbook` as Pydantic models** — ready to become rows instead of files with zero
  schema change.
- **Risk classes, action classes, autonomy levels** — these *are* the MSP governance model. "A3 after
  N verified successes, per client, per intent" is exactly how a cautious MSP earns trust gradually.
  Already built.

So the work is **at the edges** (identity, credential resolution, storage keying, auth, provisioning),
not the core. That is the difference between a bounded build-out and rethinking the product.

---

## 4. Sequencing toward sellable

1. **Multi-tenant data model** — `msp_id` / `client_id` on everything, Postgres, isolation enforced in
   the store. Foundational; everything else sits on it.
2. **GDAP multi-tenant Graph app + per-client credential resolution** — the MSP-specific integration;
   without it we can only touch our own tenant.
3. **Facts / playbooks as DB rows + a per-client editor** — turns the demo's YAML into a product surface.
4. **Auth** — MSP SSO, roles, scoped API.
5. **Client provisioning flow** — GDAP consent → crawl → facts.
6. **PSA integration** — tickets from and back into ConnectWise/Autotask. Arguably co-equal with (2) for
   adoption.
7. **Billing / metering, per-MSP kill switch, scoped audit export.**

Items 1–3 make the current code genuinely multi-tenant; item 6 makes an MSP able to use it at all.

---

## 5. Open decisions

- **GDAP vs. per-client app registration.** GDAP is the modern MSP-standard path (one multi-tenant app,
  partner relationships) but carries its own consent/role complexity. Confirm this is the assumption
  vs. each client installing a dedicated app — it changes the credential-resolution design.
- **Playbook layering** — two tiers or three (§2.4).
- **Approval delivery** — client approvers almost certainly approve via email/Teams link, not our
  console; confirm so the approval identity model (§2.2) is built for that from the start.
