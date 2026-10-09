# Learnings from the Sample Invoice

_Source: EPC I.T. Solutions → Douglas Parking, invoice #71367, April 2026 time detail (34.75 h: 27.75 billed, 7.00 non-billable). Last updated: 2026-10-08._

Related: [ticket-coverage.md](ticket-coverage.md) maps each ticket to an automation approach. This doc records what the invoice teaches us about **how MSP work actually happens** and what that means for the product and the demo playbooks.

> **Caveats.**
> - This is one MSP, one client and one month. Treat these as hypotheses to confirm against 6–12 months of tickets from several MSPs.
> - The invoice contains real names and real temporary passwords. **Never copy credentials into this repo.** Use the fictional "Northwind Parking" for demos.

---

## 1. Ten things the invoice shows

1. **The unit of work is a job, not a ticket.** Dan Sorensen's onboarding took 8 time entries from 6 technicians over 4 days. Two more tickets followed: VPN setup a week later (227714) and local drive access in remote sessions (227656). Nobody owned the whole job.
2. **Requests arrive incomplete, and the gaps cost follow-up tickets.**
   - Ruben got the wrong license, then an upgrade the next day (226642 → 226906).
   - Michael was blocked, then the tech had to ask what to do with his mailbox (225689).
   - Tony's laptop and mailbox decisions were made over several calls (226643, 226645).
   - An onboarding request asked only for email when Office and SharePoint were needed (226645 note).
   Asking the right questions at intake would have avoided most of these.
3. **Every job touches systems beyond Microsoft 365.** These include the hosted remote-desktop server (DP-TS-01), a Meraki VPN with its own logins (226505), on-prem Active Directory (225445), copiers, Wi-Fi networks and vendor apps (Parking Logic, ParkWhiz, Skidata). An agent limited to Microsoft 365 must name these gaps honestly, not ignore them.
4. **Credentials are handled insecurely.**
   - Four temporary passwords are printed in the notes, which appear on the client's invoice. One is predictable.
   - A shared account's real password was handed out because a vendor app couldn't do MFA (225445).
5. **Nobody verifies identity before resets.** No password or MFA reset in the sample shows any identity check. That's the help-desk social-engineering gap the demo's out-of-band check addresses.
6. **Buying licenses is a separate, unpaid step.** There are seven "Acquired 1x license" entries, one per new user, all non-billable. It's a hand-off that adds delay and margin loss.
7. **There are no conventions, or they aren't followed.** New-user emails use five formats: `Jia@`, `Dominic@`, `D.Sorensen@`, `ehuffman@`, `c.slovacek@`. Hostnames do follow a pattern (`DPG-0052-DSorensen`).
8. **Shared and service accounts aren't documented.** `jlsreports` is used by a parking app that can't do MFA. It was saved from deletion only because "a site employee, Edward, swears they use this" (225798).
9. **Knowledge about the client lives in technicians' heads.** Examples: "the VPN uses Meraki cloud login, not Active Directory" (226505), "printers only work on the Staff Wi-Fi network" (227321), "HR scans go in the restricted folder" (226762). This is exactly what the environment graph should capture.
10. **The same false alarms recur, and the work record is unreliable.**
    - DP-0004-PHARRELL raised the same "encryption suspended" alert 3 times, with no root-cause fix.
    - One note was billed on two tickets.
    - One billed entry has no notes.
    - One ticket ("Outlook unable to send") collected four unrelated requests.

---

## 2. Learnings per demo playbook

These feed the playbooks in [demo/Tech.MD §6](../demo/Tech.MD#6-playbooks-guidelines-not-steps).

### 2.1 Onboarding (Jia, Dominic, Dan, Ruben, Ethan, Chance)

| Playbook field | Learning | Source |
|---|---|---|
| Must investigate | **Copy the coworker's license**, not a guessed one. Ruben was given Exchange Online only and needed Office the next day. | 226642 → 226906 |
| Must investigate | **Access outside Microsoft 365:** whether the coworker has hosted-server and VPN access. These go to a technician as "needs a technician" items, raised up front instead of in follow-up tickets. | 226451, 227714, 226505 |
| Must investigate | **Start date.** Ruben was licensed and offboarded before he ever started. | 226642, 227740 |
| Client facts | **The email format is inconsistent.** When there's no clear rule, the agent proposes one (e.g. the format used by recent hires) and has the requester confirm it on the approval card. | 5 onboarding tickets |
| Client facts | Approvers: Taylor (most requests), Lauren (receives onboarding/offboarding forms, approves purchases), Marlene (VPN access). | 227062, 228055, 226505 |
| Definition of done | Make sure licenses are bought and assigned in the same job, so buying isn't a separate step. | 7 Luo entries |
| Definition of done | The welcome message covers how to get to email, recommends the **Outlook mobile app** (the built-in iOS mail app hit "needs admin approval"), and notes that file shares may need to be re-sent to the new address. | 227917, 225636, 225081 |
| Never | Plaintext or predictable passwords in notes or replies. | 225081, 225636, 227331, 227305 |

### 2.2 Offboarding (Jazmyne, Michael, Tony, Ruben)

| Playbook field | Learning | Source |
|---|---|---|
| Definition of done (template) | Jazmyne's ticket is close to a complete checklist: force sign-out, reset password, block sign-in, forward, delegate, local machine password, confirm not in Active Directory, 30-day follow-up. | 226549 |
| Ask up front | **What happens to the mailbox** (forward / delegate / keep / delete) and **to the device** (reuse / wipe / return). Asking after acting caused extra calls. | 225689, 226643, 226645 |
| Gotcha | **Removing the license makes the mailbox inaccessible.** Ruben's license was removed "as requested" with no warning. The default is to keep the license, and removal needs explicit confirmation of the consequence. | 227740 |
| Gotcha | Deleted accounts can be restored within 30 days, but calendar items are lost immediately. | 226447, 225798 |
| Needs a technician | Converting to a shared mailbox and adding mailbox delegates need Exchange PowerShell. Removing the Meraki VPN user, hosted-server user and on-prem Active Directory account are outside Graph. | 226455, 226643, 226505, 225445 |

### 2.3 MFA / credential reset (jlsreports, ParkWhiz, Jennifer, Dennis)

| Playbook field | Learning | Source |
|---|---|---|
| Must investigate | **Is this a person or a shared/service account?** Resetting MFA on `jlsreports` broke a vendor app and ended with the real password being handed out. A service account → escalate with a proposal for a scoped exception; never a standard reset. | 225445, 225798 |
| Must investigate | **Whose MFA is it?** ParkWhiz was a vendor app's MFA, not Microsoft 365. It gets a how-to guide, not a reset. | 227284 |
| Must investigate | **Which credential, which system?** "Password resets" covered SharePoint/M365, the remote server and the VPN. The agent resets only M365; the rest go to a technician. | 225081, 225624 |
| Must investigate | **Is it really an MFA problem?** "Needs admin approval" on iOS was an app-approval issue, not lost MFA. | 225636 |
| Never | Share a real password. Reset anything without an identity check. | 225445, all resets |

---

## 3. What the invoice doesn't tell us

Ask design partners for these:

| Gap | Why it matters | Ask for |
|---|---|---|
| Group memberships | No note mentions groups, so "same as Marcus" can't be grounded in this data | Tenant read access or a group export |
| Formal approver list | Who can approve which kind of request is only implied | The client's onboarding/offboarding form and approver list |
| Standard license per role | Prevents the Ruben-style wrong license | License policy per client |
| Out-of-band contacts | Needed for identity checks | Manager or registered phone per user |
| Mailbox retention policy | Determines the offboarding default | Retention / convert-to-shared policy |
| Volume and mix across clients | One month of one client can't size the market | 6–12 months of ticket exports |

---

## 4. Where these learnings changed the plan

- **Playbooks:** new must-investigate checks, questions to ask up front, gotchas and "never" rules for all three intents ([demo/Tech.MD §6](../demo/Tech.MD#6-playbooks-guidelines-not-steps)).
- **Scenarios:** onboarding confirms the email format on the approval card. Offboarding asks about the device. MFA reset checks for service accounts ([demo/Product.MD §3](../demo/Product.MD#3-scenarios)).
- **Demo tenant seed:** mixed email formats, a `reports`-style service account, and coworkers with VPN and hosted-server access ([demo/Tech.MD §10](../demo/Tech.MD#10-demo-tenant-reset-and-safe-mode)).
- **Test set:** seven new cases (16–22) drawn from these patterns ([demo/Tech.MD §11](../demo/Tech.MD#11-eval-set)).
- **Product thesis:** confirms the "jobs, not tickets" and "the work record is the product" bets ([product-plan.md §1](product-plan.md#1-summary)).
