"use client";

import { useState } from "react";
import { ApiError, decide } from "@/lib/api";
import { ANSWER_KEYS, answersFor } from "@/lib/run";
import { APPROVERS, senderName } from "@/lib/samples";
import type { Snapshot } from "@/lib/types";
import { DecisionLabel, KeyValues, Section } from "./ui";

/** One card for the whole plan: the risky steps, the confirm questions, and approve / reject. */
export function ApprovalCard({ snap, onDecided }: { snap: Snapshot; onDecided: () => void }) {
  const plan = snap.plan!;
  const steps = snap.steps ?? [];
  const risky = steps.filter((s) => s.decision === "approve");
  const blocked = steps.filter((s) => s.decision === "block");
  const autoCount = steps.filter((s) => s.decision === "auto").length;
  const held = snap.run.state === "HELD";

  const [by, setBy] = useState<string>(APPROVERS[0]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);

  async function submit(decision: "approve" | "reject") {
    setBusy(true);
    setRefusal(null);
    try {
      await decide(snap.run.id, by, decision, answersFor(plan.confirm_on_approval, edits));
      onDecided();
    } catch (e) {
      setRefusal(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="Approval" tone="warn">
      {held && (
        <p className="mb-3 rounded bg-amber-100 p-2 text-sm text-amber-900">
          <span className="font-semibold">Held:</span> {senderName(snap.ticket.from)} isn&apos;t an approved requester for this.
          An approver must release it.
        </p>
      )}
      {blocked.length > 0 && (
        <div className="mb-3 rounded bg-red-50 p-2 text-sm text-red-900">
          <span className="font-semibold">The gate blocked {blocked.length} step{blocked.length > 1 ? "s" : ""}; this plan can&apos;t be approved.</span>
          <ul className="ml-5 list-disc">{blocked.map((s) => <li key={s.id}>{s.action}: {s.reason}</li>)}</ul>
        </div>
      )}

      <p className="text-sm text-neutral-600">
        {autoCount} step{autoCount === 1 ? "" : "s"} run automatically. {risky.length ? "These need your approval:" : "Nothing else needs approval."}
      </p>
      <ul className="mt-2 space-y-2">
        {risky.map((s) => (
          <li key={s.id} className="rounded border border-amber-300 bg-amber-50 p-2">
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm font-semibold">{s.action}</span>
              <DecisionLabel decision={s.decision} />
              <span className="font-mono text-xs text-neutral-500">{s.rule_id}</span>
            </div>
            <p className="text-sm">{s.rationale}</p>
            <div className="mt-1"><KeyValues values={s.params} /></div>
          </li>
        ))}
      </ul>

      {plan.confirm_on_approval.length > 0 && (
        <div className="mt-4 space-y-3">
          <h3 className="text-sm font-semibold">Please confirm</h3>
          {plan.confirm_on_approval.map((c) => {
            const editable = (ANSWER_KEYS as readonly string[]).includes(c.key);
            return (
              <div key={c.key + c.question} className="text-sm">
                <p className="font-medium">{c.question}</p>
                {editable ? (
                  <input
                    className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 font-mono text-sm"
                    value={edits[c.key] ?? c.proposed}
                    onChange={(e) => setEdits({ ...edits, [c.key]: e.target.value })}
                  />
                ) : (
                  <p className="mt-1 font-mono text-sm">✔ {c.proposed} <span className="font-sans text-xs text-neutral-500">(approving confirms this)</span></p>
                )}
                <p className="mt-0.5 text-xs text-neutral-500">{c.evidence}</p>
              </div>
            );
          })}
        </div>
      )}

      {plan.questions_for_requester.length > 0 && (
        <div className="mt-3 text-sm">
          <h3 className="font-semibold">Questions for the requester</h3>
          <ul className="ml-5 list-disc">{plan.questions_for_requester.map((q) => <li key={q}>{q}</li>)}</ul>
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2 border-t pt-3">
        <label className="text-sm text-neutral-600">
          Approving as{" "}
          <select className="rounded border border-neutral-300 px-1 py-1" value={by} onChange={(e) => setBy(e.target.value)}>
            {APPROVERS.map((a) => <option key={a} value={a}>{senderName(a)}</option>)}
          </select>
        </label>
        <button
          type="button" disabled={busy || blocked.length > 0} onClick={() => submit("approve")}
          className="rounded bg-emerald-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-40"
        >
          {held ? "Release and approve" : "Approve plan"}
        </button>
        <button
          type="button" disabled={busy} onClick={() => submit("reject")}
          className="rounded border border-neutral-300 px-4 py-1.5 text-sm hover:bg-neutral-100 disabled:opacity-40"
        >
          Reject
        </button>
      </div>
      {refusal && <p className="mt-2 text-sm font-medium text-red-700">Refused: {refusal}</p>}
    </Section>
  );
}

export function ApprovalSummary({ approval }: { approval: NonNullable<Snapshot["approval"]> }) {
  const answers = Object.entries(approval.answers);
  return (
    <p className="text-sm text-neutral-600">
      {approval.decision === "approve" ? "✔ Approved" : "✖ Rejected"} by {senderName(approval.by)}
      {approval.released_hold && " (released the hold)"}
      {answers.length > 0 && <> · confirmed {answers.map(([k, v]) => `${k} = ${v}`).join(", ")}</>}
    </p>
  );
}
