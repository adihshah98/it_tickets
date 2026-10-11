"use client";

// After execution: scheduled follow-ups (with "run now"), the one-time link, the technician hand-off, and
// the outcome of an escalated or held run.

import { useState } from "react";
import { ApiError, runNow } from "@/lib/api";
import type { StepRun } from "@/lib/run";
import type { BlockedMessage, DoneCheck, OutboundMessage, Plan, Snapshot, TimeEntry as TimeEntryT } from "@/lib/types";
import { KeyValues, Section, when } from "./ui";

export function Followups({ snap, runs, onStarted }: {
  snap: Snapshot; runs: Record<string, StepRun>; onStarted: () => void;
}) {
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function start(id: number) {
    setBusy(id);
    setError(null);
    try {
      await runNow(id);
      onStarted();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Section title="Follow-ups">
      <ul className="space-y-2">
        {snap.followups.map((f) => {
          const run = runs[`${f.step_id}-run`];
          const status = run?.status === "running" ? "running…" : run?.status === "failed" ? `failed: ${run.error}` : f.status;
          return (
            <li key={f.id} className="flex flex-wrap items-center gap-3 text-sm">
              <span className="font-mono">{f.kind}</span>
              <span className="text-neutral-500">scheduled {when(f.run_at)}</span>
              <span className={f.status === "done" ? "text-emerald-700" : run?.status === "failed" ? "text-red-700" : "text-neutral-600"}>
                {f.status === "done" ? "✔ done" : status}
              </span>
              {f.status !== "done" && snap.run.state === "DONE" && run?.status !== "running" && (
                <button
                  type="button" disabled={busy !== null} onClick={() => start(f.id)}
                  className="ml-auto rounded border border-indigo-300 px-3 py-1 text-xs font-semibold text-indigo-700 hover:bg-indigo-50 disabled:opacity-40"
                >
                  Run now
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
    </Section>
  );
}

export function Links({ links }: { links: Snapshot["links"] }) {
  return (
    <Section title="One-time link">
      <ul className="space-y-1 text-sm">
        {links.map((l) => (
          <li key={l.id}>
            For {l.to} ·{" "}
            {l.viewed_at
              ? <span className="text-emerald-700">✔ opened {when(l.viewed_at)}</span>
              : <a href={l.url!} target="_blank" rel="noreferrer" className="font-mono text-sky-700 underline">open link</a>}
            <span className="text-neutral-500"> · expires {when(l.expires_at)}</span>
          </li>
        ))}
      </ul>
    </Section>
  );
}

export function NeedsTechnician({ items }: { items: Plan["needs_technician"] }) {
  return (
    <Section title="Needs a technician" tone="warn">
      <ul className="space-y-1.5 text-sm">
        {items.map((t) => (
          <li key={t.item}>
            <p className="font-medium">{t.item}</p>
            <p className="text-xs text-neutral-500">{t.reason}</p>
          </li>
        ))}
      </ul>
    </Section>
  );
}

export function Outcome({ outcome }: { outcome: NonNullable<Snapshot["outcome"]> }) {
  const { from, to, kind, reason, findings, ...rest } = outcome;
  void from;
  void findings;
  return (
    <Section title={to === "HELD" ? "Held" : "Escalated to a technician"} tone={to === "HELD" ? "warn" : "bad"}>
      {kind && <p className="font-mono text-xs text-neutral-500">{kind}</p>}
      {reason && <p className="text-sm">{reason}</p>}
      {Object.keys(rest).length > 0 && <div className="mt-2"><KeyValues values={rest} /></div>}
    </Section>
  );
}

export function DefinitionOfDone({ checks }: { checks: DoneCheck[] }) {
  const met = checks.every((c) => c.ok);
  return (
    <Section title="Definition of done" tone={met ? "good" : "bad"} aside={<span className="text-xs text-neutral-500">checked live in Graph</span>}>
      <ul className="space-y-1 text-sm">
        {checks.map((c) => (
          <li key={c.probe} className="flex gap-2">
            <span className={c.ok ? "text-emerald-700" : "text-red-700"}>{c.ok ? "✔" : "✖"}</span>
            <span className="font-mono">{c.probe}</span>
            <span className="text-neutral-500">{c.detail}</span>
          </li>
        ))}
      </ul>
    </Section>
  );
}

const MESSAGE_TITLE = { reply: "Reply to requester", psa_note: "PSA note" } as const;

export function Messages({ messages, blocked }: { messages: OutboundMessage[]; blocked: BlockedMessage[] }) {
  return (
    <>
      {blocked.map((b) => (
        <Section key={`blocked-${b.kind}`} title={`${MESSAGE_TITLE[b.kind]}: not sent`} tone="bad">
          <p className="text-sm">
            {b.reason === "secret"
              ? `The secret scanner blocked it (${b.hits.map((h) => h.kind).join(", ")}). Nothing was sent.`
              : `Held: ${b.detail ?? "the definition of done isn't met"}.`}
          </p>
        </Section>
      ))}
      {messages.map((m) => (
        <Section key={m.kind} title={MESSAGE_TITLE[m.kind]}>
          {m.to && <p className="text-xs text-neutral-500">To {m.to}</p>}
          {m.subject && <p className="mb-2 font-medium">{m.subject}</p>}
          <p className="whitespace-pre-wrap text-sm">{m.text}</p>
        </Section>
      ))}
    </>
  );
}

export function TimeEntry({ entry }: { entry: TimeEntryT }) {
  return (
    <Section title="Time entry">
      <p className="text-sm"><span className="font-mono">{entry.work_type}</span> · {entry.minutes} min</p>
      <p className="text-xs text-neutral-500">working time, excluding time waiting for an approver</p>
    </Section>
  );
}
