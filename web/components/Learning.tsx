// The learning loop's pieces on the workspace (Build-Day21): the no-playbook banner, the escalation diagnosis,
// and the badge on a run that followed a learned playbook.

import Link from "next/link";
import type { PlaybookUsed, Snapshot } from "@/lib/types";
import { Section } from "./ui";

/** Tech.MD §4.9: a ticket type with no playbook is still investigated, but the gate has every change approved. */
export function NoPlaybookBanner() {
  return (
    <p className="rounded-lg border border-amber-400 bg-amber-50 px-4 py-2 text-sm text-amber-950">
      <span className="font-semibold">No playbook: every change needs approval.</span>{" "}
      Nothing has taught the agent this kind of ticket yet. Once it&apos;s done, it can be saved as a playbook.
    </p>
  );
}

const strings = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : []);
const text = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

/** The investigator's write-up when it escalates with no playbook: the keys it puts on the ESCALATED state. */
export const DIAGNOSIS_KEYS = ["diagnosis", "evidence", "checked", "suggested_next_step", "missing_action"] as const;

export function EscalationDiagnosis({ outcome }: { outcome: NonNullable<Snapshot["outcome"]> }) {
  const evidence = strings(outcome.evidence);
  const checked = strings(outcome.checked);
  const next = text(outcome.suggested_next_step);
  const missing = text(outcome.missing_action);
  return (
    <Section title="Diagnosis" tone="warn" aside={<span className="text-xs text-neutral-500">from read-only checks</span>}>
      <p className="text-sm">{text(outcome.diagnosis)}</p>
      {evidence.length > 0 && (
        <div className="mt-3 text-sm">
          <h3 className="font-semibold">Evidence</h3>
          <ul className="ml-5 list-disc">{evidence.map((e) => <li key={e}>{e}</li>)}</ul>
        </div>
      )}
      {checked.length > 0 && (
        <div className="mt-3 text-sm">
          <h3 className="font-semibold">Checked and ruled out</h3>
          <ul className="ml-5 list-disc">{checked.map((c) => <li key={c}>{c}</li>)}</ul>
        </div>
      )}
      {next && (
        <p className="mt-3 text-sm"><span className="font-semibold">Suggested next step:</span> {next}</p>
      )}
      {missing && (
        <p className="mt-3 rounded bg-neutral-100 p-2 text-sm">
          {/* not always a missing catalogue action: run 22's is a group add the playbook told it not to propose */}
          <span className="font-semibold">The change it couldn&apos;t propose:</span> {missing}
        </p>
      )}
    </Section>
  );
}

const SOURCE = {
  agent_run: "an approved agent run", technician_run: "a technician's run", written: "a technician's write-up",
  audit_log: "admin-center changes",
} as const;

/** "Learned playbook": a run that followed a playbook a person approved from a run or a write-up, not a shipped one. */
export function LearnedBadge({ used, handsFree }: { used: PlaybookUsed; handsFree: boolean }) {
  const source = used.source && used.source !== "shipped" ? used.source : null;
  if (!source) return null;
  return (
    <span className="flex items-center gap-1.5 rounded bg-violet-100 px-2 py-0.5 text-xs text-violet-900">
      <Link href={`/playbooks/${used.intent}`} className="font-semibold hover:underline">
        Learned playbook
      </Link>
      <span className="font-mono">{used.intent} v{used.version}</span>
      <span>
        · from{" "}
        {used.source_run_id
          ? <Link href={`/runs/${used.source_run_id}`} className="underline">run #{used.source_run_id}</Link>
          : SOURCE[source]}
      </span>
      {handsFree && <span className="font-semibold">· no approvals</span>}
    </span>
  );
}
