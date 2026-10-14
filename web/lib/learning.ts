// The workspace's learning actions (Build-Day21 part 4): "Save as playbook" and "I fixed it in the admin
// center". Pure, so vitest can test it on recorded payloads.

import type { AuditObserved, ObservedStep, PlaybookDraft, Snapshot, UnmappedAudit } from "./types";

/** The review screen for a draft recorded on a run (Part 5 reads `playbook_draft` from that run). */
export const reviewHref = (intent: string, runId: number) =>
  `/playbooks/${encodeURIComponent(intent)}?run=${runId}`;

/** The intent to save a run's playbook as: the plan's, else triage's first. Empty for `unknown`, which can't
 * be saved (run 21's draft came back as `intent: unknown`): the technician names the job. */
export function defaultIntent(snap: Pick<Snapshot, "plan" | "triage" | "playbook_draft">): string {
  const candidates = [snap.playbook_draft?.intent, snap.plan?.intent, ...(snap.triage?.intents ?? [])];
  return candidates.find((i) => i && i !== "unknown") ?? "";
}

/** An intent slug as the library checks it (backend models/base.py INTENT_RE, less `unknown`). */
export const INTENT_RE = /^[a-z]+(?:_[a-z]+)*(?:\.[a-z]+(?:_[a-z]+)*)+$/;

export type AuditView =
  | { phase: "idle" }
  | { phase: "waiting"; startedAt: string }
  | { phase: "found"; learned: ObservedStep[]; undone: ObservedStep[]; cantLearn: UnmappedAudit[];
      draft: PlaybookDraft | null }
  | { phase: "timed_out"; waitedMin: number };

/** What the admin-center panel shows, from the snapshot's last `audit_observed` and `playbook_draft`. A draft
 * counts only if it came from the audit logs (a run can also have a Save-as-playbook draft). */
export function auditView(audit: (AuditObserved & { at?: string }) | null, draft: PlaybookDraft | null): AuditView {
  if (!audit) return { phase: "idle" };
  switch (audit.status) {
    case "waiting":
      return { phase: "waiting", startedAt: audit.at ?? audit.since };
    case "timed_out":
      return { phase: "timed_out", waitedMin: Math.round(audit.waited_s / 60) };
    case "found":
      return { phase: "found", learned: audit.mapped, undone: audit.undone, cantLearn: audit.unmapped,
               draft: draft?.source === "audit_log" ? draft : null };
  }
}
