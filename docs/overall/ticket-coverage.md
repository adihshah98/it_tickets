# Ticket Coverage Map — Sample Tickets

_Source: EPC I.T. Solutions invoice #71367 to Douglas Parking, April 2026 time detail. 34.75 h total (27.75 billed, 7.00 non-billable)._

How the AI technician ([tech-plan.md](tech-plan.md)) would handle each ticket. Each row doubles as a seed eval case.

**Autonomy levels:**

| Level | Meaning |
|---|---|
| **A3** | Autonomous within policy |
| **A2** | Agent executes after one-click approval |
| **A1** | Agent diagnoses and drafts; a person executes |
| **A0** | Person only |

The **Prevent** column is the root-cause fix that would stop the ticket class from recurring.

---

## Identity lifecycle & access (10.75 h)

| Ticket | Request | Hrs | Intent(s) | Target | How | Prevent |
|---|---|---:|---|---|---|---|
| 225081 | "Outlook unable to send" became a VPN fix, Jia Xue's new account, license purchase and password resets | 2.0 | Mixed (4 intents) | Split, then A3/A2 | Agent splits the ticket into separate intents. User create via Graph; license via Pax8 (spend approval); credentials via TAP | Structured intake form |
| 225636 | Create Dominic Lopez + license; later, iOS mail "needs admin approval" | 1.0 | `user.create`, `license.purchase`; `mail.mobile_access` | A3 / A1 | Graph user + license; for iOS, check the Entra sign-in log for the CA/consent reason and reply with the fix | — |
| 226642, 226906, 227740 | Ruben Negron: create, upgrade license, offboard (never started) | 1.5 | `user.create`, `license.change`, `user.offboard` | A3 (+ spend approval) | Graph + Pax8. At offboarding, agent notes "never signed in" and cancels the license to recover spend | Onboarding job waits for a confirmed start date before buying the license |
| 227331 | Ethan Huffman email | 0.5 | `user.create` | A3 | Graph; TAP instead of a predictable plaintext password in notes | Secret scanner |
| 227305, 227917 | Chance Slovacek account + "how to access email" | 0.75 | `user.create`, `howto.email` | A3 | Graph; welcome message template | — |
| 226451 (O365 part) | Dan Sorensen M365 + hosted RDS user | 0.5 | `user.create`, `rds.user.create` | A3 | Graph; RDS group via on-prem relay | Part of the onboarding job |
| 226549 | Offboard Jazmyne: sign-out, reset, block, forward, delegate, local password, 30-day follow-up | 0.25 | `user.offboard` | A3 (delegate = A2) | Graph revoke sessions + block; EXO forward + FullAccess; local account via RMM; 30-day timer in job engine | — |
| 225689 | Offboard Michael Littles | 0.25 | `user.offboard` | A3 | Same runbook; the intake form asks delete / forward / delegate up front | Intake form |
| 226455, 226643 | Tony Pauley: forward to Taylor, then FullAccess delegate | 0.5 | `mailbox.forward`, `mailbox.delegate` | A3 / A2 | EXO `Set-Mailbox`, `Add-MailboxPermission`; delegate needs client approver | — |
| 226645 | Reset departed Tony Pauley's local laptop password for reuse | 0.5 | `device.local_password_reset` | A2 | RMM script (or Windows LAPS read). Data-access grant needs approval | Intune + Windows LAPS; Entra-joined, not local profiles |
| 226415 | Change forwarding on ticket@ shared mailbox | 0.25 | `mailbox.forward` | A3 | EXO; requester authority check | — |
| 226447 | Restore deleted user Mohammed | 0.25 | `user.restore` | A3 | Graph restore deleted item (within 30 days); TAP to client | — |
| 225798 | Mailbox cleanup: delete 13, keep 3, notes per mailbox | 1.0 | `mailbox.bulk_cleanup` | A2 | Agent proposes list from last sign-in, license and forwarding data; client approves per line; soft-delete; export report | Monthly user review |
| 227886 | User review: list of licensed accounts | 0.5 | `report.user_review` | A3 | Graph report with last sign-in and license cost, scheduled monthly | Scheduled |
| 225520 | Allow 5 countries for travel in Conditional Access | 0.75 | `ca.travel_exception` | A2 | Graph named locations; **auto-expire after trip dates**; owner approval | Travel request form with dates |
| 225445 (part 1) | AD user deactivated, so no VPN or Skidata | 0.25 | `ad.user.enable` | A2 | On-prem relay: enable, move OU; verify VPN auth | — |
| 226505 | VPN for YC (Meraki cloud auth) | 0.75 | `vpn.user.create` | A2 | Meraki API client VPN user; approver Marlene; graph fact "VPN = Meraki cloud auth" | — |
| 225081 (4/15), 225636 | Password resets for SharePoint/O365 | 0.5 | `user.password_reset` | A3 + out-of-band verify | TAP / self-service password reset | Enable SSPR |
| Luo NC entries (7×) | "Acquired 1x license" per new user | 1.75 | `license.purchase` | A3 within budget rule | Pax8 API, folded into the create runbook | Removed as a separate step |
| 227062 | Add Lauren to onboarding/offboarding notification list | 0.25 | `platform.config` | A3 | Our intake platform setting | — |

## Device provisioning & logistics (8.25 h)

| Ticket | Request | Hrs | Intent | Target | How | Prevent |
|---|---|---:|---|---|---|---|
| 226451 | Dan Sorensen laptop: asset tag, OOBE, admin account, Automate, updates, Entra join, apps, welcome letter, QA, pack, FedEx | 3.5 | `job.onboard.device` | A3 orchestration; physical steps removed | Autopilot registration at Dell order; Intune app and config assignment; Asset Tiger API; agent QA checks (hostname, Intune, Sophos, apps, server auth) | **OEM direct-ship + Autopilot**; no lab touch |
| 227923 | Chance Slovacek laptop: check-in, tag, setup, QA, 2.25 h FedEx round trip | 3.5 | `job.onboard.device` | Same | Same; FedEx Ship API pickup if lab staging is still needed | Same |
| 228055 | Quote: laptop sold out; 16" vs 14" alternatives; shipping address | 0.25 | `procure.quote` | A2 | Distributor/Dell availability API; agent proposes alternatives; client approves in portal | Standard device catalog per client |
| 226241 | Manager computer PS ATL: license + sign into apps + SharePoint bookmark | 0.75 | `job.onboard.device` | A3 | Intune: Office + SharePoint/OneDrive sync policy + Edge favorites profile | Autopilot baseline |
| 227714 | Add VPN connection to Dan's computer | 0.25 | `endpoint.vpn_profile` | A3 | Intune VPN profile assignment | In baseline |

## Security alert triage (4.25 h, all non-billable)

| Ticket | Request | Hrs | Intent | Target | How | Prevent |
|---|---|---:|---|---|---|---|
| 224362, 225947, 226759 | PHARRELL "encryption suspended" ×3 | 1.25 | `alert.bitlocker_suspended` | A3 | On alert, RMM `Get-BitLockerVolume`. If protection is on (or resumes after reboot), close with evidence. If still suspended, `Resume-BitLocker`, then verify | Tune the Sophos alert to ignore update-reboot suspends; recurrence detector |
| 224429, 227006 | VESPR "not encrypted" ×2 | 0.75 | `alert.bitlocker_state` | A3 | Same | Same |
| 227239 | PORTLAN suspended | 0.5 | `alert.bitlocker_suspended` | A3 | Same | Same |
| 225481 | Mac doesn't meet Sophos prerequisites (Nick); calls and emails | 0.75 | `alert.mac_fda` | A3 if MDM-enrolled, else A1 | Push macOS PPPC/system-extension profile via Intune; else agent schedules with user and sends steps | **Fleet-wide PPPC profile** |
| 227726 | Mac not encrypted; owner unknown (serial V7QL7KXM6Y) | 1.0 | `asset.owner_lookup` + `alert.mac_fda` | A1→A3 | Agent correlates serial across Sophos, Intune, Entra sign-ins and asset system to find the user before asking the client | Asset reconciliation job |

## Network, VPN, remote desktop (3.5 h)

| Ticket | Request | Hrs | Intent | Target | How | Prevent |
|---|---|---:|---|---|---|---|
| 225624 (+ 225081 4/2 VPN entries) | Jennifer VPN connects once, not twice | 1.75 | `vpn.troubleshoot` | A1→A2 | Agent pulls Meraki client VPN events, endpoint VPN config and event logs; compares to working config; proposes fix | Managed VPN profile via Intune |
| 225495 | Scans mapped drive missing | 0.25 | `endpoint.drive_map` | A3 | RMM check, then remap; verify after reboot | Intune/GPO drive-map policy |
| 227034 | RDP redirection warning dialog (Dennis) | 0.5 | `endpoint.registry_fix` | A3 | Push `RedirectionWarningDialogVersion=1` via Intune to all DP-TS-01 users | Fleet-wide, not one machine |
| 227656 | Local drive access in remote session | 0.25 | `rds.drive_redirection` | A3 | Ship a managed .rdp file / RemoteApp setting with drive redirection | In baseline |
| 227511 | "Office network down" | 0.25 | `network.outage_check` | A3 | Meraki uplink and event API; auto-reply with status; ideally the alert opens the ticket first | Proactive uplink monitoring |
| 227210 | Firewall rules for new Denver site (44334, 1000, 1194) | 0.5 | `network.fw_rule` | A2 | Meraki L3/port-forward API; security-control approval | — |

## Printing & scanning (2.5 h)

| Ticket | Request | Hrs | Intent | Target | How | Prevent |
|---|---|---:|---|---|---|---|
| 225524 | Disable fax TX report on copier | 0.5 | `printer.setting` | A1 (later A2 via computer-use) | Vendor web UI on LAN; agent drafts steps | — |
| 226762 | Restricted scan folder for Sophie + copier address books | 1.0 | `scan.folder_setup` | A2 (folder/ACL) + A1 (copier) | On-prem relay creates folder and ACL; copier address book stays manual for now | Scan-to-SharePoint/OneDrive |
| 226756 | PDF prints blank from email attachment | 0.5 | `print.troubleshoot` | A1 | Known-fix retrieval (save locally / alternate viewer); draft reply | — |
| 227321 | Rob Jones can't print: wrong SSID | 0.5 | `print.troubleshoot` | A3 | Meraki client lookup shows SSID, so the answer is "join Staff SSID"; graph fact | Wi-Fi profile pushes Staff SSID to managed devices |

## Email security & delivery (1.5 h)

| Ticket | Request | Hrs | Intent | Target | How | Prevent |
|---|---|---:|---|---|---|---|
| 227617 | Phishing from info@oxford.expert | 0.5 | `email.phish_response` | A3 | Message trace, find all recipients, purge (where licensed), tenant block list, report to Microsoft, notify recipients | Phish-report button that triggers this automatically |
| 227786 | Phishing from a hotmail sender | 0.25 | `email.phish_response` | A3 | Same | Same |
| 227101 | Email to inugo.com rejected | 0.25 | `email.ndr_explain` | A3 | Parse NDR + message trace; plain-English reply | — |
| 227922 | External contact not receiving | 0.5 | `email.delivery_trace` | A3 | Message trace; reply with evidence | — |

## MFA with third-party apps (1.5 h)

| Ticket | Request | Hrs | Intent | Target | How | Prevent |
|---|---|---:|---|---|---|---|
| 225445 (part 2) | jlsreports MFA; app password fails in Parking Logic; real password shared | 0.75 | `mfa.service_account` | A1 + security flag | Agent flags the shared, MFA-less account and proposes a CA exclusion scoped to the vendor IP, or a dedicated service account | Service-account policy |
| 227284 | ParkWhiz (vendor) MFA setup; no password, then guided setup | 0.75 | `howto.vendor_mfa` | A1 | Agent sends a step-by-step guide; the human session stays | — |

## Real troubleshooting & misc (2.5 h)

| Ticket | Request | Hrs | Intent | Target | How | Prevent |
|---|---|---:|---|---|---|---|
| 225992 | Hyper-V/Veeam can't checkpoint File1 | 0.5 | `backup.checkpoint_failure` | A1→A2 | Agent collects VSS writers, integration services, event logs; proposes guest reboot in a maintenance window (approval) | Backup health monitor |
| 226061 | Restore file to earlier version | 0.25 | `file.restore_version` | A3 (SharePoint) / A2 (on-prem) | Graph driveItem versions, or VSS/backup restore point as a copy | User self-service restore |
| 227865 | Slow PC; install Chrome | 0.75 | `endpoint.performance` | A2→A3 | RMM health script (CPU/mem/disk, startup items), clear browser cache, Dell + Windows updates, SFC; install app via Intune | Patch and app baseline |
| 228740 | Find Outlook archives from an old recovery | 0.5 | `knowledge.history_lookup` | A1 | Agent searches ticket history and the graph ("archives recovered to external HDD") and drafts the answer | — |
| 225968 | "Laptop inv #40 — let me know when it's online" | 0.25 | `comms.followup` | A3 | Agent watches the RMM for the device check-in and replies automatically | — |

---

## Summary

| Target | Approx. hours | Share |
|---|---:|---:|
| A3 autonomous (incl. after root-cause fixes) | ~17 | ~49% |
| A2 one-click approval | ~6 | ~17% |
| A1 assist (human executes; agent cuts time) | ~6.5 | ~19% |
| Removed entirely (physical logistics via direct-ship) | ~5 | ~15% |

Approximate hours removed: **~23 of 34.75 (≈65%)**. A1 work still gets faster because the diagnosis arrives done.

### Coverage by system access

What each ticket needs the agent to reach, in approximate hours. Some tickets span several systems; each is counted under the access its main work needs.

| What the work needs | Approx. hours | Share | Main tickets |
|---|---:|---:|---|
| **Demo ticket types** (onboarding incl. license purchase, offboarding, password/MFA) | ~9 | **~25%** | User creates, Ruben, Luo license entries, 226549, 225689, password resets |
| All Microsoft 365 work (Graph + Exchange Online), including the demo types | ~11 | ~32% | Above, plus forwarding/delegates, restores, user review, CA travel, email security, file version restore |
| **Device access** (RMM or Intune) | ~15 | **~44%** | Laptop provisioning (226451, 227923, 226241), BitLocker/Sophos/Mac alerts, drive map, RDP registry, slow PC, local password, Hyper-V |
| Network (Meraki API) | ~4.25 | ~12% | Jennifer's VPN, VPN users, firewall rules, SSID lookup, outage check |
| On-prem servers and AD (local relay) | ~1.25 | ~4% | AD enable, RDS user, scan folder ACL |
| People only (vendor UIs, how-to) | ~2.25 | ~6% | Copier settings, blank PDF, vendor MFA guide, archive lookup |
| Other (quote, platform setting) | ~0.5 | ~1% | 228055, 227062 |

What this means:

- **Microsoft 365 alone covers about a third of the queue at most.** Device work is the largest bucket. Every security alert (4.25 h, all non-billable) arrived through the RMM or Sophos.
- **Device work splits two ways.** About 8 h is provisioning and configuration that Intune can do through Graph, *if* the client is managed in Intune (this one mostly isn't). About 7 h needs RMM scripts: BitLocker checks, drive remaps, health scripts, local accounts, server checks. About 4 h of the 15 is physical work that direct-ship removes.
- **By ticket count, identity is a larger share than by hours**, because those tickets are short (0.25–0.5 h).
- One client over one month. Recompute on design-partner histories before relying on it.

The demo covers the first row ([demo/Product.MD](../demo/Product.MD)). Demo v2 adds device access through one RMM ([demo/Product.MD §10](../demo/Product.MD#10-next-demo-v2-diagnose-mode-and-devices)).

Notes-hygiene problems the AI technician would have caught:
- **Plaintext temporary passwords** on the invoice (Jia, Dominic, Chance, Ethan).
- **One note billed on two tickets** (4/2 MacDonald, 225081 and 225624).
- **A 0.25 h entry with no notes** (4/15, 225081).
- **A single "Outlook" ticket** that collected four unrelated requests.
