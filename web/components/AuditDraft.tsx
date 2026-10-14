"use client";

// "I fixed it in the admin center" (Build-Day20, Build-Day21 decision 8 of the workspace): the technician made
// the change in Entra instead. We read the audit logs for this run's objects, show what can and can't be learned,
// and draft a playbook from the changes we can map (capped at A2: nobody watched them verified).

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, auditDraft } from "@/lib/api";
import { formatSecs } from "@/lib/run";
import { auditView, reviewHref } from "@/lib/learning";
import { TECHNICIANS } from "@/lib/samples";
import type { ObservedStep, Snapshot } from "@/lib/types";
import { KeyValues, Section, when } from "./ui";

function Waiting({ startedAt }: { startedAt: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <p className="text-sm">
      <span className="animate-pulse text-indigo-600">◌</span> Waiting for the audit logs (usually 1-3 minutes, up to
      10)… <span className="font-mono text-neutral-500">{formatSecs((now - Date.parse(startedAt)) / 1000)}</span>
    </p>
  );
}

function Steps({ title, steps, note }: { title: string; steps: ObservedStep[]; note?: string }) {
  if (!steps.length) return null;
  return (
    <div className="mt-3 text-sm">
      <h3 className="font-semibold">{title}</h3>
      {note && <p className="text-xs text-neutral-500">{note}</p>}
      <ul className="mt-1 space-y-1.5">
        {steps.map((s, i) => (
          <li key={i} className="rounded border border-neutral-200 p-2">
            <p><span className="font-mono">{s.action}</span> <span className="text-xs text-neutral-500">{s.rationale}</span></p>
            <KeyValues values={s.params} />
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AuditDraft({ snap, onStarted }: { snap: Snapshot; onStarted: () => void }) {
  const view = auditView(snap.audit, snap.playbook_draft);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);

  async function start() {
    setBusy(true);
    setRefusal(null);
    try {
      await auditDraft(snap.run.id, TECHNICIANS[0]);
      onStarted();
    } catch (e) {
      setRefusal(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  const button = (label: string) => (
    <button
      type="button" disabled={busy} onClick={start}
      className="mt-2 rounded border border-indigo-300 px-3 py-1 text-sm font-semibold text-indigo-700 hover:bg-indigo-50 disabled:opacity-40"
    >
      {label}
    </button>
  );

  return (
    <Section title="I fixed it in the admin center" aside={<span className="text-xs text-neutral-500">learns from the audit logs</span>}>
      {view.phase === "idle" && (
        <>
          <p className="text-sm text-neutral-600">
            Made the change in the Entra admin center instead? We read the audit logs for the people and groups this run
            looked at, since it escalated, and draft a playbook from the changes we can map to the catalogue. It&apos;s
            capped at A2 until a run on it succeeds.
          </p>
          {button("I fixed it in the admin center")}
        </>
      )}
      {view.phase === "waiting" && <Waiting startedAt={view.startedAt} />}
      {view.phase === "timed_out" && (
        <>
          <p className="text-sm">No change showed in the audit logs within {view.waitedMin} minutes.</p>
          {button("Look again")}
        </>
      )}
      {view.phase === "found" && (
        <>
          <Steps title="Learned" steps={view.learned} note="each checked still in place with one read" />
          <Steps title="Already undone, not learned" steps={view.undone} />
          {view.cantLearn.length > 0 && (
            <div className="mt-3 text-sm">
              <h3 className="font-semibold">Can&apos;t learn</h3>
              <ul className="ml-5 list-disc">
                {view.cantLearn.map((u, i) => (
                  <li key={i}>{u.activity} <span className="text-xs text-neutral-500">· {when(u.at)} · {u.reason}</span></li>
                ))}
              </ul>
            </div>
          )}
          {view.draft ? (
            <p className="mt-3 text-sm">
              <Link href={reviewHref(view.draft.intent, snap.run.id)} className="font-semibold text-sky-700 hover:underline">
                Review the draft ({view.draft.intent}) →
              </Link>{" "}
              <span className="text-xs text-neutral-500">capped at A2 (from audit logs)</span>
            </p>
          ) : view.learned.length ? (
            <p className="mt-3 text-sm"><span className="animate-pulse text-indigo-600">◌</span> Drafting the playbook… (about a minute)</p>
          ) : (
            <p className="mt-3 text-sm text-neutral-600">Nothing here can become a playbook.</p>
          )}
        </>
      )}
      {refusal && <p className="mt-2 text-sm font-medium text-red-700">Refused: {refusal}</p>}
    </Section>
  );
}
