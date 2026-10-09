# Tech Plan — The AI Technician

*Last updated: 2026-10-09*

Companion docs: [product-plan.md](product-plan.md) · [ticket-coverage.md](ticket-coverage.md)

---

## 1. Design principles

1. **The LLM decides and plans; deterministic code acts.** The model chooses intents, fills parameters, diagnoses and writes. Every change to a client system goes through typed, tested tool functions behind a policy engine the model cannot bypass.
2. **Runbooks where the path is known, agentic where it isn't.** About 80% of volume falls into about 30 intents (onboarding, offboarding, forwarding, license change, alert triage, and so on). Those run as versioned runbooks with LLM-filled parameters. Unknown problems get a read-only diagnostic loop that ends in a proposed plan or an escalation, under stricter rules than known intents. Approved resolutions become new runbooks after human review, so the known set grows from real work.
3. **Verify every write independently.** Every write is followed by a separate read that confirms the end state. A step is not done until verification passes.
4. **A job is the unit of work, not a ticket.** Long-running, multi-system, multi-day work (onboarding, procurement, 30-day offboarding follow-up) runs as durable workflows with wait states.
5. **Treat ticket and email content as untrusted.** Ticket text, emails and tool output are data, never instructions. This matters because the agent will read phishing emails as part of its job.
6. **Least privilege, per tenant, per action.** No standing global admin. Credentials are scoped to the action family and the client tenant.
7. **Every action leaves a reversible trail.** Capture the state before each change, log the diff, and roll back where the API allows it.

---



## 2. System architecture

```
                     ┌──────────────────────── Intake ─────────────────────────┐
                     │ PSA webhooks/poll · email · Teams/Slack bot · client      │
                     │ portal forms · alert sources (Sophos, Defender, RMM,      │
                     │ Meraki, backup)                                           │
                     └───────────────────────────┬─────────────────────────────┘
                                                 ▼
┌──────────────────┐     ┌────────────────────────────────────────────────────┐
│ Environment      │◄───►│ Understand: classify intents · extract entities ·  │
│ graph (per       │     │ resolve to graph objects · dedupe/merge/split ·    │
│ client tenant)   │     │ requester authority check · priority               │
│ users, devices,  │     └───────────────────────────┬────────────────────────┘
│ licenses, groups,│                                 ▼
│ mailboxes, shares│     ┌────────────────────────────────────────────────────┐
│ sites, VPN, apps,│◄───►│ Plan: runbook match + params  OR  agentic          │
│ SOPs, approvers, │     │ diagnose loop (read-only tools) → proposed plan    │
│ conventions,     │     └───────────────────────────┬────────────────────────┘
│ ticket history   │                                 ▼
│ (+ embeddings)   │     ┌────────────────────────────────────────────────────┐
└──────────────────┘     │ Policy engine (deterministic): action class ×      │
                         │ client policy × autonomy level → allow / approve / │
                         │ deny. Approvals via Teams/Slack/PSA/portal         │
                         └───────────────────────────┬────────────────────────┘
                                                     ▼
                         ┌────────────────────────────────────────────────────┐
                         │ Job engine (durable workflows): execute steps ·    │
                         │ snapshot pre-state · retries · waits (approval,    │
                         │ shipment, user action, 30-day timers) · verify     │
                         └───────────────────────────┬────────────────────────┘
                                                     ▼
                         ┌────────────────────────────────────────────────────┐
                         │ Tool / connector layer (typed actions)             │
                         │ Graph · Exchange Online PS · Intune · Partner      │
                         │ Center/GDAP · PSA · RMM scripts · on-prem relay ·  │
                         │ Meraki · Sophos/Defender · Pax8 · Dell · FedEx ·   │
                         │ IT Glue/Hudu · Asset Tiger                         │
                         └───────────────────────────┬────────────────────────┘
                                                     ▼
                         ┌────────────────────────────────────────────────────┐
                         │ Close-out: requester comms (TAP / one-time links) ·│
                         │ PSA notes + time entry · change log · graph update │
                         │ · escalation package if stopped · eval capture     │
                         └────────────────────────────────────────────────────┘
          Cross-cutting: audit log · observability/tracing · secrets vault · kill switch
```

---



## 3. Integration layer

Every integration exposes **typed actions** with: an input schema, an **action class** (see §6), idempotency key, `dry_run`, a `snapshot()` of pre-state, a `verify()` read, and `rollback()` where possible.


| System family                | First targets                                                                                                                   | Transport / notes                                                                                                                        |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| **PSA**                      | ConnectWise Manage → Autotask, HaloPSA                                                                                          | REST APIs. Read tickets, contacts, agreements, configs. Write notes, time entries, status. The agent appears as a member, e.g. "AI Tech" |
| **Microsoft 365 / Entra**    | Users, licenses, groups, Conditional Access named locations, sign-in logs, TAP, MFA method reset, deleted-user restore          | Microsoft Graph via a multi-tenant app per MSP. Client tenants reached through GDAP relationships and admin consent                      |
| **Exchange Online**          | Forwarding, FullAccess/SendAs delegates, convert to shared, message trace, tenant allow/block list                              | Exchange Online PowerShell with app-only certificate auth, run in containerized `pwsh` workers. Graph lacks parity for much of this      |
| **Intune / Autopilot**       | Device enrollment state, config profiles (drive maps, VPN, registry, macOS PPPC), app assignment, Autopilot device registration | Graph (device management). Most configuration drift fixes ship as profiles here                                                          |
| **Licensing / distributors** | Pax8 first (subscriptions), then TD Synnex/Ingram; Dell Premier for hardware                                                    | Vendor APIs. Spend actions always gated                                                                                                  |
| **RMM**                      | ConnectWise Automate → NinjaOne, Datto RMM                                                                                      | Script execution, device inventory, alerts. **Phase-1 transport to endpoints and on-prem servers**                                       |
| **On-prem relay**            | AD (users, OUs, password reset, unlock), file server shares/ACLs, Hyper-V/VSS health, printers                                  | Phase 1: RMM script jobs. Phase 2: our own outbound-only relay agent (this becomes the RMM in OS stage 4)                                |
| **Security**                 | Sophos Central, Defender for Endpoint, then SentinelOne/Huntress                                                                | Alerts in; endpoint health reads; alert acknowledge/close                                                                                |
| **Network**                  | Meraki Dashboard (client VPN users, firewall rules, uplink status, client→SSID lookup), then Fortinet/UniFi                     | REST APIs                                                                                                                                |
| **Backup**                   | Veeam, Datto BCDR                                                                                                               | Job status, restore-point lookup                                                                                                         |
| **Docs / assets**            | IT Glue, Hudu, Asset Tiger                                                                                                      | Read SOPs and configurations; write asset tags and changes. Later replaced by our environment graph                                      |
| **Logistics**                | FedEx/UPS Ship APIs                                                                                                             | Labels and pickups, so nobody drives to FedEx                                                                                            |
| **Comms**                    | Email (Graph send-as the MSP helpdesk), Teams/Slack bots                                                                        | End-user replies, approvals, escalations                                                                                                 |


**Credential model:** each MSP has its own isolated vault namespace. Each client tenant's credentials are separate. Tokens are fetched just in time per run and scoped to the action family. No credential is ever placed in model context.

---



## 4. Environment graph (the moat)

A per-client model of the environment, built three ways:

- **Crawled:** users, licenses, devices, groups, mailboxes, CA policies, Intune profiles, Meraki networks and VPN configuration, Sophos endpoints.
- **Extracted:** an LLM reads historical tickets and notes to learn facts the APIs can't give. Examples from the sample: "VPN uses Meraki cloud authentication, not AD" (226505), "jlsreports is a shared account used by Parking Logic, which can't do MFA" (225445), "printers are only reachable on the Staff SSID" (227321), "scan folders live on FILE\Scans; HR scans go in the restricted folder" (226762).
- **Declared:** client SOPs, approved requesters and approvers (e.g. Taylor, Lauren, Marlene), naming conventions, vendor contacts.

It powers entity resolution ("Jennifer" → the actual user object), authority checks, planning context, drift detection and documentation. It also catches inconsistencies. The sample uses four different email formats for new users: `c.slovacek@`, `D.Sorensen@`, `ehuffman@`, `Dominic@`. The agent should enforce one convention or flag the conflict.

Storage: Postgres (entities, edges, facts with provenance and confidence) plus pgvector (ticket and resolution embeddings). A dedicated graph database is not needed at this scale.

---



## 5. Agent loop

```
ingest → understand → retrieve → plan → policy gate → execute → verify → communicate → document → learn
                                   ▲                                │
                                   └──── replan on failure ─────────┘   (bounded; else escalate)
```

1. **Understand.** Multi-label intent classification (one ticket can hold several intents; 225081 held four). Entity extraction and resolution. Duplicate and job detection (225624 and 225081 were the same VPN issue for the same user). Requester authority check against the graph.
2. **Retrieve.** Environment graph slice, top-k similar resolved tickets for this client and across the MSP, the client's SOPs, open jobs for the same user.
3. **Plan.**
  - *Known intent* → select runbook → model fills parameters → schema validation → missing or ambiguous parameters become a clarifying question to the requester (e.g. "delete, forward, or delegate Michael's mailbox?").
  - *Unknown intent (no runbook)* → bounded loop with **read-only** tools. Output: a diagnosis with evidence, plus either a plan or an escalation. A plan may only use low-risk reversible actions, and every write needs approval. Credential, spend, data-access and destructive actions are blocked until a reviewed runbook exists.
  - *Troubleshooting (diagnose mode)* → plan-first doesn't fit when each step depends on the last result ("slow PC", "VPN connects once", "can't print"). The loop is: diagnose → hypothesis → one small fix through the gate → observe → repeat, up to a limit. Diagnostics run freely, but only scripts from a reviewed, hashed read-only library count as reads. Done means the verify read passes **and** the requester confirms the problem is gone.
4. **Policy gate.** Deterministic code (§6). The model cannot call write tools directly. It submits a plan, and the gate decides per step.
5. **Execute.** The job engine runs steps with idempotency keys, a pre-state snapshot, retries with backoff, and durable waits for approvals, users, shipments and timers.
6. **Verify.** A separate read confirms the end state per step: the user can authenticate, the license is assigned, forwarding equals X, BitLocker protection is on, the drive is mapped after a reboot. A failure triggers a replan, at most N times, then escalation.
7. **Communicate.** Templated plus model-written reply in the MSP's tone. Credentials go out only as an Entra **Temporary Access Pass** or a one-time-view link.
8. **Document.** PSA note (what, why, evidence, change diff), time entry with an automation work type and actual effort, change log, graph update. **Secret scanner on all outbound text.**
9. **Escalate (when stopping).** Structured package: symptoms, evidence, hypotheses ranked, actions taken plus rollback state, recommended next step, the right tech or skill to route to.
10. **Learn.** Tech edits, overrides, reopens and CSAT become labeled eval cases and runbook improvement suggestions. New runbooks come from three sources, and **every one needs human approval** before it runs:
    - **Approved agent runs.** A successful no-runbook run can be saved as a draft runbook.
    - **Human resolutions.** When a tech closes an escalated ticket, the agent reads what changed in that window and drafts a runbook from it. Sources: Entra directory audits, the Microsoft 365 unified audit log, Exchange admin audit, RMM script history, and the PSA note. System logs are trusted over notes, which are often incomplete.
    - **Ticket history.** At onboarding, the agent clusters 6–12 months of tickets by intent and drafts the top 20 runbooks before go-live.

    **Two kinds of memory.** *Client facts* (environment-graph facts with provenance) can be learned automatically and stay editable. *Procedures* (runbooks) change only through review. Ticket text is untrusted, so it can never change how the agent works on its own.



### Runbooks as code

- Defined in YAML, validated into Pydantic models: `intent`, `params` schema, `preconditions`, `steps` (typed actions), `verify`, `rollback`, `action_class` per step, `comms` templates.
- **Drafted by the model from historical ticket notes, reviewed by a human, tested in a sandbox, versioned.** Each MSP can override per client.
- The initial library is about 30 intents covering identity, mailbox, licensing, alert triage, endpoint configuration, network lookups and email security. See the coverage map.



### Model usage


| Task                                                                           | Model                       | Notes                    |
| ------------------------------------------------------------------------------ | --------------------------- | ------------------------ |
| Intent classification, entity extraction, secret scanning, summaries           | `claude-haiku-4-5-20251001` | High volume, cheap, fast |
| Runbook parameter filling, comms drafting, note writing                        | `claude-sonnet-5-5`         |                          |
| Diagnosis loops, multi-intent planning, escalation packages, runbook authoring | `claude-opus-5-5`           | Low volume, high stakes  |


Prompt-cache the per-client environment context and the tool definitions. Track cost per resolution as a first-class metric.

---



## 6. Safety, permissions, trust



### Action classes (enforced by the policy engine)


| Class                         | Examples from the sample                                                                                              | Default handling                                                         |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| **Read**                      | Message trace, sign-in logs, Sophos state, Meraki uplink                                                              | Auto                                                                     |
| **Low-risk reversible write** | Set forwarding (authorized requester), remap drive, push VPN profile, add to group                                    | Auto at A3                                                               |
| **Spend**                     | Buy license (Luo's 7 entries), order hardware                                                                         | Client approver or pre-approved budget rule                              |
| **Data-access grant**         | Delegate to a departed user's mailbox (226643), reset a departed user's laptop password (226645)                      | Client approver                                                          |
| **Security-control change**   | Conditional Access countries (225520), firewall port opens (227210), MFA reset (225445), creating a VPN user (226505) | MSP tech approval, plus client approval where the policy requires it     |
| **Destructive**               | Delete mailboxes (225798), remove licenses, wipe a device                                                             | Two-step: approval, then delayed execution with soft-delete; bulk limits |
| **Production disruption**     | Reboot a server or VM (225992)                                                                                        | Maintenance window plus tech approval                                    |




### Identity and social-engineering defenses

- Requests are honored only from **approved requesters** per client and action class.
- Password and MFA resets need **out-of-band confirmation** to a known channel (registered phone or manager approval). Help-desk social engineering is a known attack path, and an AI that resets passwords on request would make it easy.



### Prompt-injection defenses

- Untrusted content is tagged and quoted, never merged into instructions.
- Write tools are reachable only through the policy gate, which checks against the original ticket's intent and requester. A plan that adds actions unrelated to the request is rejected.



### Blast radius

- MSP-level isolation, with per-run scoping to one client tenant. No cross-tenant tool calls in a run.
- Rate limits on destructive classes.
- A global and per-MSP **kill switch**.
- Anomaly alerts, e.g. "agent attempting 15 deletions in 5 minutes."

> Remote-management platforms have been used in supply-chain attacks against MSP clients. Security is existential for this product, not a later compliance task.



### Secrets

- Never in notes, emails or model context.
- Use TAPs and one-time links.
- Run a vault-backed secret scanner on every outbound string. It would have blocked the 4 plaintext passwords in the sample invoice.



### Audit

- An immutable log per action: who/what requested it, plan, approval, pre-state, diff, verify result.
- Exportable for MSP clients and auditors.



### Compliance

- Start SOC 2 Type I early; MSPs will ask for it.
- Data is tenant-isolated and encrypted.
- No training on customer data without opt-in.

---



## 7. Evaluation & data flywheel

- **Historical replay set.** Convert each resolved historical ticket into an eval case: ticket text, intent(s), expected actions, expected end state, and "should escalate?". The sample invoice gives about 45 cases ([ticket-coverage.md](ticket-coverage.md)). Design partners' histories give thousands.
- **Sandbox environments.** Microsoft 365 developer tenants seeded with synthetic orgs, a mock PSA, a Meraki DevNet sandbox, and Windows/macOS VMs under a test RMM. Every runbook change runs end to end before release.
- **Scores.**
  - Intent accuracy
  - Parameter accuracy
  - End-state correctness (via verify reads)
  - Escalation precision and recall
  - Unsafe-action attempts (must be 0)
  - Cost and latency per ticket
- **Shadow mode in production.** The agent's plan is compared with what the tech actually did. Disagreements are triaged into "agent wrong" (new eval case) or "tech wrong" (product insight, e.g. the duplicate billing entry).
- **Promotion rule.** An intent moves from A1 → A2 → A3 for a client after N consecutive verified successes with zero incorrect actions. Demotion is automatic on an incident.

---



## 8. Tech stack (recommended)


| Layer              | Choice                                                   | Why                                                        |
| ------------------ | -------------------------------------------------------- | ---------------------------------------------------------- |
| Core services | Python (FastAPI) | Matches the team's existing Python + Logfire stack; strong Graph SDK (`msgraph-sdk`, `azure-identity`) |
| Durable workflows  | Temporal                                                 | Multi-day jobs, retries, timers, human-in-the-loop signals |
| Data               | Postgres + pgvector                                      | Graph, audit, embeddings in one place                      |
| PowerShell workers | Containerized `pwsh` with ExchangeOnlineManagement       | Exchange operations without Graph parity                   |
| Secrets            | HashiCorp Vault or Azure Key Vault, per-MSP namespaces   | Isolation, just-in-time fetch                              |
| Agent runtime | **PydanticAI** on Anthropic models; policy gate and executor stay in our own code | Typed tools, validated structured output with retries, model-swappable, Logfire tracing, `pydantic_evals` for evals |
| Observability      | OpenTelemetry tracing per ticket run (e.g. Logfire)      | Debug agent runs end to end                                |
| Front ends         | Next.js console; Teams/Slack apps; client portal         |                                                            |


---



## 9. Build plan — first 12 months


| Months | Milestone                      | Scope                                                                                                                                                                                                                                                                | Exit criteria                                                                         |
| ------ | ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| 0–1    | **Foundations**                | 3–5 design partners signed; historical ticket export; intent taxonomy from history; ConnectWise Manage + Graph/GDAP connectors (read); sandbox tenants; eval harness v0                                                                                              | Taxonomy covers ≥80% of partner ticket volume; replay set ≥500 cases                  |
| 1–3    | **Shadow mode / copilot (A1)** | Triage, entity resolution, duplicate/merge detection, context packs, proposed plans and draft notes written into PSA tickets; read-only diagnostics (sign-in logs, message trace, Sophos state, Meraki status)                                                       | Plan agreement with techs ≥70% on top-10 intents; techs rate drafts useful            |
| 3–5    | **First autonomy (A2→A3)**     | Identity lifecycle runbooks (create, offboard, forward, delegate, license, restore, password reset with TAP + out-of-band check); email security (phishing trace/block, NDR explain); BitLocker/Sophos auto-verify-and-close; Teams approvals; Pax8 license purchase | ≥30% of partner tickets autonomous; 0 incorrect actions in evals; <0.5% in production |
| 5–7    | **Endpoints & network**        | RMM transport (Automate, then NinjaOne); on-prem AD/file-server actions via RMM scripts; Intune profile pushes (drive maps, VPN, registry, macOS PPPC); Meraki (VPN users, SSID lookup, uplink, firewall with approval); scripted slow-PC remediation                | ≥45% autonomous; first prevention fixes shipped (recurring alerts removed)            |
| 7–9    | **Jobs engine**                | End-to-end onboarding/offboarding with client intake forms, Autopilot + Dell direct-ship, FedEx labels, asset tagging, 30-day follow-ups; second PSA (Autotask or Halo)                                                                                              | Onboarding cycle time and techs touched per onboarding cut ≥70% at partners           |
| 9–12   | **Prevention & record**        | Recurring-class detection with proposed root-cause fixes; monthly user and license reviews; ticket-history audit as self-serve lead magnet; billing reconciliation (time entries vs agreement); our own on-prem relay agent (beta); start PSA-lite design            | 10+ paying MSPs; NRR >120%; clear evidence for the stage-2 build                      |


**Team for year one (indicative):** 2–3 integration engineers, 1–2 agent/eval engineers, 1 security engineer (early), 1 front-end engineer, and **1 ex-MSP service manager or senior tech**, who owns runbooks, policy defaults and design-partner success.

---



## 10. Technical risks & open questions

- **Incumbent API dependency.** ConnectWise and Kaseya control API access and terms. Mitigations: multi-PSA support early, a clean internal ticket model, and a plan to own intake.
- **Exchange/Graph gaps and throttling.** PowerShell workers are slower and heavier, so per-tenant queues and throttling need careful engineering.
- **Long tail of vendor web UIs.** Copiers, parking systems and line-of-business apps. These stay A1. Computer-use agents through the on-prem relay are a later bet.
- **Voice.** Many tickets start or end on the phone. Do we build a voice agent (intake plus guided fixes) in year one or defer it?
- **Liability.** Contract terms, insurance and per-action audit when the agent makes a mistake. Do we carry E&O ourselves or pass through to the MSP?
- **Device access is the coverage ceiling.** In the sample, about 44% of hours need an RMM or Intune, against about 32% for all Microsoft 365 work ([ticket-coverage.md](ticket-coverage.md#coverage-by-system-access)). Competitors that own the RMM (e.g. Atera) have device access from day one. **Decision needed:** keep RMM connections at months 5–7, pull them into the first autonomy milestone, or build our own endpoint agent earlier than OS stage 4.
- **Open:** which PSA/RMM to prioritize after ConnectWise (check design-partner mix); whether to build our own relay agent before or after the PSA stage; whether pricing is per endpoint or per resolution at launch.

---



## 11. Initial E2E happy-path demo

The demo build uses a guided agent, a gated plan and verified execution, built in Python with **PydanticAI** for the agent parts:

- **Phases:** intake (Haiku) → investigation with read-only tools (Opus) → structured plan → plan linter and policy gate (code) → one approval → executor (code: snapshot → act → verify by read-back) → replan only on deviation (at most 3) → definition-of-done check and close-out.
- **Tickets without a playbook:** investigated under stricter rules, then planned with approval on every write, or escalated. A successful run can be saved as a playbook after review, with autonomy set per client.
- **Integrations:** Microsoft Graph only, app-only, against a single demo tenant. License purchase and out-of-band confirmation are mocked. We serve the one-time credential link ourselves.
- **Effort:** about 4 weeks for one engineer, starting with 2 days of tenant and model risk tests, with onboarding working end to end by week 2. An optional week 5 connects a real PSA (HaloPSA trial). Cash cost is about $200–300 in API credits; Microsoft is free on a trial, then about $30–35 per month with a minimal license mix.
- **Next (v2):** diagnose mode plus one RMM connection for device tickets.

Full design: **[demo/Tech.MD](../demo/Tech.MD)** (plan schema, playbooks, no-playbook path, playbook learning, tool catalogue with Graph permissions, policy rules, bounds, tenant reset and safe mode, 28-case eval set, diagnose mode).
