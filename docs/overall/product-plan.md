# Product Plan — AI-Native Technician → AI-Native MSP OS

*Last updated: 2026-10-09*

Companion docs: [tech-plan.md](tech-plan.md) (how the AI technician is built) · [ticket-coverage.md](ticket-coverage.md) (how each sample ticket gets handled)

---

## 1. Summary

**Thesis.** An MSP is a labor business. Most of its cost is technicians, and most technician time goes to repetitive L1/L2 work that is now API-addressable: Microsoft 365, Entra, Intune, Meraki, Sophos/Defender and RMM scripts. Whoever does that work owns the most valuable data in the MSP: what was done, for whom, how long it took, and what it cost. Every other MSP system (PSA, RMM, billing, quoting, documentation) is a record of that work.

**Wedge: an AI technician.** It works tickets inside the MSP's existing PSA and RMM, like a new hire would. It resolves what it safely can, escalates the rest with the diagnosis already done, and writes clean notes and time entries.

**End state: the AI-native MSP OS.** Because the AI technician does the work, it can produce the system of record as a by-product. We move from "plugin on ConnectWise/Kaseya" to "the place tickets, endpoints, contracts, quotes and invoices live." We replace the PSA first, then the RMM.

**Three bets that differentiate us from "AI ticket resolver" competitors (e.g. Squash):**

1. **Jobs, not tickets.** The real unit of work is "onboard Dan Sorensen," which spans 5+ systems, several days, hardware and approvals. Today it is scattered across many tickets and technicians. We orchestrate the whole job.
2. **Prevent, don't just resolve.** Recurring alerts and configuration drift should be removed at the source (an Intune profile, a policy tweak), not closed one at a time. Every resolution asks "should this ticket class exist?"
3. **The work record is the product.** Perfect notes, perfect time entries, no secrets in notes, and agreement-aware billing. This is what earns us the right to become the PSA and the billing system.

**Build first:** an AI technician for **M365-centric SMB clients** on **ConnectWise Manage + ConnectWise Automate** (the stack in our sample data). Then Autotask/Halo and NinjaOne/Datto.

---



## 2. The problem, from real ticket data

Sample: one MSP (EPC I.T. Solutions), one client (Douglas Parking, about 40 users), one month (April 2026). **34.75 technician hours: 27.75 billed and 7.00 written off as non-billable.**


| Work category                                                                                           | Hours     | Share | Automatable with today's APIs (est.) |
| ------------------------------------------------------------------------------------------------------- | --------- | ----- | ------------------------------------ |
| Identity lifecycle (onboard/offboard, licenses, forwarding, delegates, restores, password/CA/VPN users) | 10.75     | 31%   | ~8.5 h                               |
| Device provisioning & logistics (asset tag, lab setup, QA, packing, FedEx drives)                       | 8.25      | 24%   | ~6.0 h                               |
| Security alert triage (Sophos encryption false positives, Mac Full Disk Access)                         | 4.25      | 12%   | ~4.0 h                               |
| Network / VPN / remote desktop / drive mapping                                                          | 3.50      | 10%   | ~1.5 h                               |
| Printing & scanning                                                                                     | 2.50      | 7%    | ~1.0 h                               |
| Email security & delivery (phishing, message trace, NDRs)                                               | 1.50      | 4%    | ~1.25 h                              |
| MFA with third-party apps                                                                               | 1.50      | 4%    | ~0.25 h                              |
| Real troubleshooting (Hyper-V, slow PC, archives, file restore)                                         | 2.50      | 7%    | ~0.5 h                               |
| **Total**                                                                                               | **34.75** |       | **~23 h (≈65%)**                     |


What the data shows beyond the hour counts:

- **The work is scattered.** Dan Sorensen's onboarding took 8 time entries from 6 technicians over 4 days, plus two follow-up tickets (VPN setup, drive mapping). Ruben Negron was created, had his license upgraded, and was offboarded before he ever started. Nobody owned the job.
- **20% of hours were written off.** All 7 non-billable hours were absorbed by the MSP. They went to alert triage (4.25 h), license purchasing (1.75 h, one entry per new user) and small fixes. This comes straight out of margin.
- **The same problems recur unfixed.** DP-0004-PHARRELL raised "encryption suspended" three times in one month. It is BitLocker suspending during Windows updates, which is expected behavior. Someone checked it by hand each time.
- **The work record is weak.** Temporary passwords appear in notes, which are printed on the client's invoice. There is a duplicated entry billed on two tickets, an entry with no notes, and a 0.25 h entry that is a one-line email. One ticket ("Outlook unable to send") collected unrelated VPN, user-creation and password work. Clients dispute bills like this, and MSPs lose money on it.
- **Physical work is a choice, not a requirement.** 3.25 h of the month was driving laptops to FedEx. Autopilot plus OEM direct-ship removes it.

**Implication:** about two-thirds of this MSP's hours for this client could be automated with existing APIs. The remaining third is where technicians add real value.

> Validate on a larger sample before relying on the 65% figure: 6–12 months of ticket history from 3–5 MSPs.

---



## 3. Customer

**ICP (initial)**

- MSPs with **5–50 technicians**, owner-operated through lower mid-market.
- Clients are **SMBs on Microsoft 365 / Entra / Intune**, with a mix of on-prem servers (file server, RDS, Hyper-V) and cloud-managed networks (Meraki, Fortinet).
- They sell **flat-fee managed services agreements** (like the "PPSA Recurring" agreement in the sample), so every labor hour they save is margin. This makes the ROI direct.
- Stack: **ConnectWise Manage + Automate** first, then Autotask/HaloPSA + NinjaOne/Datto RMM.

**Buyer:** MSP owner/CEO (margin, scale without hiring) or COO/service manager (SLA, backlog, technician burnout).
**Users:** dispatchers and L1/L2 techs (handoffs, approvals, escalations), service managers (policy, quality), and indirectly the MSP's clients (faster resolution, self-serve requests).

**Hourly (T&M) MSPs are a harder sale.** Automation reduces their billable hours. Pitch them on non-billable work, capacity and response time, and offer the OS stage's packaging and billing tools to help them move to per-user pricing.

**Why now**

- Frontier models can now plan, call tools, verify and recover reliably enough to run bounded IT changes.
- GDAP (granular delegated admin) makes least-privilege, multi-tenant M365 administration programmable.
- Intune, Autopilot and Entra have matured, so most endpoint and identity work is now an API call, not a site visit.
- MSPs struggle with technician hiring and turnover, and incumbent PSA/RMM tools are old and widely disliked.

---



## 4. Product — the AI technician



### What it does

1. **Intake & triage.** Reads every new ticket and alert. It classifies intent, extracts the entities involved (user, device, mailbox, site), merges duplicates and splits mixed tickets. It also checks that the requester is authorized for the request.
2. **Context.** Pulls the client's environment (users, devices, licenses, network, vendor apps, conventions), similar past tickets and the client's standard procedures.
3. **Resolve.** Runs tested runbooks for known intents. For unknown ones it runs read-only diagnostics, then proposes or performs fixes within policy.
4. **Verify.** Independently confirms the outcome: the user can sign in, forwarding is set, BitLocker is on.
5. **Communicate.** Replies to the requester in the MSP's voice. Delivers credentials through Temporary Access Passes or one-time links, never in plaintext.
6. **Document & bill.** Writes PSA notes and time entries, logs configuration changes, and updates documentation.
7. **Escalate well.** When it stops, it hands over what it found, what it tried, the likely cause and the next step, so the technician starts at minute 20, not minute 0.
8. **Prevent.** Detects recurring ticket and alert classes and proposes the root-cause fix (policy tune, Intune profile, baseline), executing it with approval.
9. **Learn the long tail.** A ticket type it has never seen still gets investigated, then resolved with approval or escalated with a diagnosis. When a technician approves the fix, or resolves the ticket by hand, the agent drafts a playbook from what was done. Once reviewed, the next ticket of that type runs at the autonomy level the MSP chooses. Client facts are learned automatically; procedures only with a person's approval.



### Autonomy is set per intent, per client


| Level | Name       | Behavior                                                                                    |
| ----- | ---------- | ------------------------------------------------------------------------------------------- |
| A0    | Human      | Agent stays out (e.g. on-site hardware)                                                     |
| A1    | Assist     | Agent gathers context and drafts plan and notes; the tech executes                          |
| A2    | Approve    | Agent executes after a one-click approval (tech or client approver)                         |
| A3    | Autonomous | Agent executes within policy, verifies and closes; approval only for flagged action classes |


MSPs start in **shadow mode**, where the agent writes its plan but changes nothing. They then promote intents one at a time as the agent's track record builds. This trust ladder is a product feature, not just an onboarding step.

### Where it shows up

- **Inside their PSA:** the agent is a "member" that picks up tickets, adds notes and logs time. There is no new queue to watch.
- **Teams/Slack:** approvals, escalations and a daily digest for techs and client approvers.
- **Console:** policies, runbooks, autonomy settings, audit trail, metrics and ROI.
- **Client-facing intake:** structured request forms (new user, offboarding, access change, travel) via portal, email or Teams bot. Structured intake is how a 4-day onboarding becomes a single job.



### Not at launch

Phone and voice support, remote screen-share sessions with users, on-site work, and vendor-specific web UIs (copier address books, parking systems). These stay with technicians: the agent assists (A1) and we revisit them with voice and computer-use agents later.

---



## 5. Roadmap: from technician to OS

Each stage produces the data and trust that the next one needs.


| Stage                              | What we own                                                                                                                                                                 | Why the MSP adopts                                                                                                                                                                 | Gate to next stage                                                                               |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| **1. AI Technician** (months 0–12) | Ticket resolution on top of their PSA/RMM; runbooks; policy; audit                                                                                                          | Hours back, faster SLAs, less burnout. No rip-and-replace                                                                                                                          | ≥40% of all tickets autonomous at design partners; incorrect-action rate <0.5%; paying customers |
| **2. Service layer** (12–24)       | Client intake (portal/Teams), job orchestration (onboarding/offboarding/procurement), environment graph & documentation (IT Glue/Hudu replacement), QBR/user-review reports | Clients self-serve; documentation stays current automatically; MSP sells "faster onboarding" as a feature                                                                          | Most work enters through our intake; techs spend more time in our console than in the PSA        |
| **3. PSA** (18–36)                 | Tickets, time, agreements, billing & invoicing (QuickBooks/Xero sync), quoting & procurement (Pax8/TD Synnex/Ingram/Dell APIs), license reconciliation                      | The PSA becomes redundant: we already hold tickets, time and jobs. Billing is accurate because AI-written entries are complete. One-click migration from ConnectWise/Autotask/Halo | MSPs cancel their PSA. Billing and quoting run through us                                        |
| **4. RMM** (30–48)                 | Our endpoint agent (which grew out of our on-prem relay), patching, monitoring and alerting, plus Intune-native management for cloud-first fleets                           | Monitoring and remediation in one loop: an alert gets resolved, not just routed. Fewer vendors                                                                                     | MSPs cancel their RMM                                                                            |
| **5. Platform**                    | Integration marketplace, security services (MDR partner integrations), benchmarking across MSPs, embedded finance (payments, collections)                                   | The network effect: best-practice runbooks and baselines shared across MSPs                                                                                                        | —                                                                                                |


**Why this order:** the PSA is a record of work, and in stage 1 we become the source of that work. The RMM is the transport for work: our on-prem relay (needed in stage 1 for AD, file servers and printers) is the beginning of an endpoint agent. Billing is the record's financial output. Quoting and procurement are already showing up in tickets (228055: laptop sold out, alternatives offered).

---



## 6. Business model

Pricing hypotheses to test with design partners:

- **Stage 1 (AI technician):** a platform fee **per managed user/endpoint per month**, which matches how MSPs price their own agreements and grows with them. Each tier includes a volume of autonomous resolutions, with overage per resolution. Early design partners could pay **per autonomous resolution only**, to make the ROI obvious.
- **Value anchor:** in the sample, the average time entry is about 0.4 h. At an assumed fully loaded technician cost of $40–60/h, a routine L1 ticket costs the MSP roughly $15–25 in labor, plus handoffs. A resolution must cost well under that. *(Labor cost is an assumption; validate it with design partners.)*
- **Stage 3+ (OS):** replace the PSA line (typically per-technician seat) and the RMM line (typically per-endpoint) with one per-endpoint bundle. Payments and procurement margin are extra revenue.

**Unit economics to track:** model plus infrastructure cost per resolved ticket, human review minutes per ticket (ours and theirs), and implementation hours per new MSP.

---



## 7. Go-to-market

1. **Design partners (5–10).** ConnectWise Manage + Automate shops with M365-heavy clients. Free shadow mode, then paid. Run done-for-you setup: we build their runbooks and policies.
2. **Lead magnet: a ticket-history audit.** The MSP connects its PSA or uploads invoices. We return the share of hours that is automatable, the most-recurring tickets, write-off leakage and notes hygiene issues (secrets in notes, duplicates). This is the analysis we did on the Douglas Parking invoice, as a product. **Start it now, by hand, in parallel with the demo build.** It answers "can it handle *my* tickets?" better than a demo on a fictional client, and the exports it brings in are what playbooks get drafted from.

   **Sales sequence:**
   1. Audit (by hand, starting now).
   2. Demo, from the end of build week 2 (onboarding only), then the full demo at week 4.
   3. Read-only shadow mode on the MSP's real tickets.
   4. Paid design partnership.

   Details: [demo/Product.MD §9](../demo/Product.MD#9-how-the-demo-is-used-sales-sequence).
3. **Community distribution.** MSP peer groups and communities (IT Nation/Evolve, ASCII, r/msp, MSPGeek, Pax8 community) and vendor marketplaces (Pax8, ConnectWise Invent, Autotask/Halo marketplaces).
4. **Proof points.** Case studies built around hours returned per technician, non-billable hours removed, onboarding cycle time, and reopen rate.
5. **Expansion inside each MSP.** More intents, more clients set to A3, then the service layer, then the PSA migration.

---



## 8. Competition & positioning

*Verify the current positioning of each competitor before external use; this space changes quickly.*


| Player                             | What they are                                                                    | Our angle                                                                                                                                                             |
| ---------------------------------- | -------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Squash                             | AI L1/L2 technician on the incumbent stack; claims >70% L1/L2 resolution         | Same wedge. We differentiate on jobs (multi-day orchestration), prevention, work-record and billing quality, and a declared path to the OS                            |
| Rewst, Pia, MSPbots                | Automation / RPA / analytics platforms for MSPs; MSPs build workflows themselves | We don't require automation engineers. The agent plans and adapts, and runbooks are authored from ticket history                                                      |
| Thread and similar                 | AI-assisted service desk and chat intake                                         | We go beyond intake and copilot to execution and verification                                                                                                         |
| ConnectWise, Kaseya                | Incumbent PSA/RMM suites adding AI copilots                                      | They have a legacy data model and seat-based pricing that conflicts with automation. We are their API customer now and their replacement later                        |
| Atera (Robin, AI Copilot)          | RMM + helpdesk with "Autonomous IT" agents. Robin is employee-facing (chat, 24/7) and lists ~100 use cases, mostly fixes on the device through its own RMM agent, plus onboarding, password reset and MFA re-registration. The /ai page targets internal IT departments | They own the device agent, so their device coverage is broad from day one; that is our biggest gap (see [ticket-coverage.md](ticket-coverage.md#coverage-by-system-access)). We differentiate on MSP-native multi-tenancy, whole jobs rather than single tickets, code-enforced safety (approved requesters, out-of-band identity checks, read-back checks) versus "fully autonomous unless instructed otherwise", and the work record and billing |
| SuperOps, HaloPSA, NinjaOne        | Modern PSA/RMM, some marketing as "AI-native"                                    | They start from the system of record and add AI. We start from labor (the AI does the work) and add the record                                                        |


**Positioning:** "The technician that never forgets to document. It resolves the routine, prevents the recurring, and hands your team the hard stuff already diagnosed."

---



## 9. Metrics

**North star:** technician hours returned per MSP per month.


| Area          | Metric                                                                                                             |
| ------------- | ------------------------------------------------------------------------------------------------------------------ |
| Effectiveness | Autonomous resolution rate (of all tickets, and of in-scope intents); assisted resolution rate; time to resolution |
| Safety        | Incorrect-action rate (target <0.5%, zero in evaluations); rollback count; policy-violation attempts blocked       |
| Quality       | Reopen rate within 7 days; end-user CSAT; escalation usefulness (tech rating of the handoff)                       |
| Economics     | Non-billable hours removed; cost per resolution; gross margin per resolution; net revenue retention                |
| Prevention    | Recurring ticket classes eliminated; repeat alerts per device per month                                            |
| Trust ladder  | Number of intent × client pairs at A3; time from shadow mode to A3                                                 |


**Positioning:** "The technician that never forgets to document. It resolves the routine, prevents the recurring, and hands your team the hard stuff already diagnosed."

---



## 10. Initial E2E happy-path demo

A lightweight, demoable AI technician that handles three ticket types end to end, plus one it has never seen, in a real Microsoft 365 demo tenant, using Microsoft Graph only (no PowerShell):

- **New-user onboarding:** the flagship scenario. Its guardrail beat is spend approval for the license.
- **Offboarding:** a request from an unauthorized requester is held until an approved contact confirms.
- **Lost phone / MFA reset:** the requester's identity is confirmed out of band before any change. If recent sign-ins look suspicious, the agent refuses and escalates.
- **Unknown ticket (file version restore):** no playbook exists. The agent investigates, the restore runs after approval, and the run is saved as a playbook. The next similar ticket closes with no human involved. This answers "what happens with everything else?"

**How it works.** The agent gets guidelines, not steps. It investigates with read-only tools and proposes a full plan. Code checks the plan and applies policy, a person approves once, and code executes and verifies each step. The agent replans only when something deviates.

**Next (demo v2):** diagnose mode for troubleshooting, plus one RMM connection, because about 44% of sample hours need device access.

Full detail: **[demo/Product.MD](../demo/Product.MD)** (scenarios, screens, demo script) and **[demo/Tech.MD](../demo/Tech.MD)** (design).
