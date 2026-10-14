import { describe, expect, it } from "vitest";
import { auditView, defaultIntent, INTENT_RE, reviewHref } from "../lib/learning";
import type { AuditObserved, PlaybookDraft } from "../lib/types";

// Run 24 (Build-Day20 part 4): the title change made in the Entra admin center, as the snapshot serves it.
const SINCE = "2026-10-14T06:23:05.404752+00:00";
const WAITING: AuditObserved & { at: string } = {
  status: "waiting", since: SINCE, by: "aditya@northwindparking.onmicrosoft.com", at: "2026-10-14T16:10:55+00:00"};
const TITLE = {
  action: "user.update", params: { userId: "37a5cc66-347e-4665-a51c-b2a9e6070222", jobTitle: "Lot Supervisor" },
  rationale: "Update user in the Entra admin center at 2026-10-14T16:10:52.4918636Z",
  verified: { jobTitle: "Lot Supervisor" }, skipped: null,
  actor: "technician:adityashah@northwindparking.onmicrosoft.com",
};
// A password reset, as audit_map lists one it can't learn (L2's noise).
const RESET = { activity: "Reset user password", at: "2026-10-14T16:11:02Z", by: "adityashah@northwindparking.onmicrosoft.com",
                reason: "not in the catalogue", targets: ["37a5cc66-347e-4665-a51c-b2a9e6070222"] };
const FOUND: AuditObserved & { at: string } = {
  status: "found", since: SINCE, until: "2026-10-14T16:12:22.301920+00:00", mapped: [TITLE], unmapped: [RESET],
  undone: [], at: "2026-10-14T16:12:23+00:00"};
const DRAFT: PlaybookDraft = { intent: "user.update_title", source: "audit_log", yaml: "intent: user.update_title\n",
                               problems: [], ok: true };

describe("the admin-center panel", () => {
  it("is idle before anyone asks", () => {
    expect(auditView(null, null)).toEqual({ phase: "idle" });
  });

  it("times the wait from when it started, not from the escalation", () => {
    expect(auditView(WAITING, null)).toEqual({ phase: "waiting", startedAt: WAITING.at });
  });

  it("shows what it learned and what it can't, and the audit draft to review", () => {
    expect(auditView(FOUND, DRAFT)).toEqual({ phase: "found", learned: [TITLE], undone: [], cantLearn: [RESET],
                                              draft: DRAFT });
  });

  it("doesn't offer a Save-as-playbook draft as the audit one", () => {
    const fromRun = { ...DRAFT, source: "technician_run" as const };
    expect(auditView(FOUND, fromRun)).toMatchObject({ phase: "found", draft: null });
  });

  it("says how long it waited when nothing showed", () => {
    expect(auditView({ status: "timed_out", since: SINCE, waited_s: 600, at: FOUND.at }, null)).toEqual({
      phase: "timed_out", waitedMin: 10 });
  });
});

describe("saving a run as a playbook", () => {
  const snap = (intent: string | null, triaged: string[] = ["unknown"]) => ({
    plan: intent ? { intent } as never : null,
    triage: { intents: triaged } as never,
    playbook_draft: null,
  });

  it("proposes the plan's intent, else triage's, and never `unknown`", () => {
    expect(defaultIntent(snap("file.restore_version"))).toBe("file.restore_version");
    expect(defaultIntent(snap(null, ["site.grant_access"]))).toBe("site.grant_access");
    expect(defaultIntent(snap("unknown"))).toBe("");  // run 21's trap: the technician names the job
    expect(defaultIntent(snap("unknown", ["unknown", "site.grant_access"]))).toBe("site.grant_access");
    const draftedUnknown = { ...snap(null, ["site.grant_access"]), playbook_draft: { intent: "unknown" } as never };
    expect(defaultIntent(draftedUnknown)).toBe("site.grant_access");
  });

  it("checks the slug as the library does", () => {
    expect(["site.grant_access", "file.restore_version"].every((s) => INTENT_RE.test(s))).toBe(true);
    expect(["unknown", "Site.Grant", "site", "site.grant-access", "site.2fa"].some((s) => INTENT_RE.test(s))).toBe(false);
  });

  it("links to the review screen for the run's draft", () => {
    expect(reviewHref("site.grant_access", 21)).toBe("/playbooks/site.grant_access?run=21");
  });
});
