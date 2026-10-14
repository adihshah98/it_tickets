"use client";

// Pieces of the playbook screens (Build-Day21 parts 5-6): the autonomy picker, a draft's source, and an
// approved playbook with its version history.

import Link from "next/link";
import { useState } from "react";
import { ApiError, setAutonomy } from "@/lib/api";
import { allowedLevels, type Level, LEVEL_TEXT, LEVELS, SOURCE_TEXT, type SourceView } from "@/lib/playbooks";
import { TECHNICIANS } from "@/lib/samples";
import type { PlaybookView } from "@/lib/types";
import { KeyValues, Section, when } from "./ui";

export function AutonomyPicker({ value, ceiling, onChange, disabled }: {
  value: Level; ceiling: Level | null; onChange: (level: Level) => void; disabled?: boolean;
}) {
  const allowed = allowedLevels(ceiling);
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap gap-2">
        {LEVELS.map((level) => {
          const ok = allowed.includes(level) && !disabled;
          return (
            <button
              key={level} type="button" disabled={!ok} onClick={() => onChange(level)}
              title={!allowed.includes(level) && ceiling ? "needs a successful approved run on this playbook first" : ""}
              className={`rounded border px-3 py-1 text-sm ${value === level
                ? "border-indigo-600 bg-indigo-600 font-semibold text-white" : "border-neutral-300 hover:bg-neutral-100"}
                disabled:cursor-not-allowed disabled:opacity-40`}
            >
              {LEVEL_TEXT[level].name}
            </button>
          );
        })}
      </div>
      <p className="text-xs text-neutral-500">{LEVEL_TEXT[value].detail}</p>
      {ceiling === "A2" && (
        <p className="text-xs text-neutral-500">
          A3 needs a successful approved run on this playbook first (a written or audit-log playbook, until one runs).
        </p>
      )}
    </div>
  );
}

export function SourcePanel({ view, runId }: { view: SourceView; runId: number | null }) {
  return (
    <div className="space-y-3 text-sm">
      {view.kind !== "written" && (
        <div>
          <h3 className="font-semibold">
            Ticket{runId && <> · <Link href={`/runs/${runId}`} className="font-normal text-sky-700 hover:underline">run #{runId}</Link></>}
          </h3>
          <p className="whitespace-pre-wrap text-neutral-700">{view.ticket}</p>
        </div>
      )}
      {view.kind === "run" && (
        <>
          <div>
            <h3 className="font-semibold">What was done, verified</h3>
            <ul className="mt-1 space-y-1.5">
              {view.steps.map((s, i) => (
                <li key={i} className="rounded border border-neutral-200 p-2">
                  <p className="flex flex-wrap items-center gap-2">
                    <span className="text-emerald-600">✔</span>
                    <span className="font-mono">{s.action}</span>
                    <span className="rounded bg-sky-100 px-1.5 text-xs text-sky-900">by {s.by}</span>
                    {s.status === "skipped" && <span className="text-xs text-neutral-500">already true</span>}
                  </p>
                  <KeyValues values={s.params} />
                </li>
              ))}
            </ul>
          </div>
          {view.findings.length > 0 && (
            <div>
              <h3 className="font-semibold">What the investigation found</h3>
              <ul className="ml-5 list-disc text-neutral-700">{view.findings.map((f) => <li key={f}>{f}</li>)}</ul>
            </div>
          )}
        </>
      )}
      {view.kind === "audit" && (
        <>
          <div>
            <h3 className="font-semibold">Changes in the audit logs</h3>
            <ul className="mt-1 space-y-1.5">
              {view.learned.map((s, i) => (
                <li key={i} className="rounded border border-neutral-200 p-2">
                  <p><span className="font-mono">{s.action}</span> <span className="text-xs text-neutral-500">{s.rationale}</span></p>
                  <KeyValues values={s.params} />
                </li>
              ))}
            </ul>
          </div>
          {view.undone.length > 0 && (
            <p className="text-neutral-600">Already undone, not learned: {view.undone.map((s) => s.action).join(", ")}</p>
          )}
          {view.cantLearn.length > 0 && (
            <p className="text-neutral-600">Can&apos;t learn: {view.cantLearn.map((u) => u.activity).join(", ")}</p>
          )}
        </>
      )}
      {view.kind === "written" && (
        <>
          <div>
            <h3 className="font-semibold">As written</h3>
            <p className="whitespace-pre-wrap text-neutral-700">{view.text}</p>
          </div>
          <p className="text-neutral-600">Allowed actions: <span className="font-mono">{view.allowedActions.join(", ")}</span></p>
        </>
      )}
    </div>
  );
}

/** An approved playbook: its autonomy (changed within the ceiling), its YAML, and every version. */
export function PlaybookVersions({ playbook, onChanged }: { playbook: PlaybookView; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);

  async function change(level: Level) {
    setBusy(true);
    setRefusal(null);
    try {
      await setAutonomy(playbook.intent, level, TECHNICIANS[0]);
      onChanged();
    } catch (e) {
      setRefusal(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  const newestFirst = [...playbook.versions].reverse();
  return (
    <div className="space-y-4">
      <Section title="Autonomy" aside={<span className="text-xs text-neutral-500">for this client</span>}>
        <AutonomyPicker value={playbook.autonomy} ceiling={playbook.ceiling} onChange={change} disabled={busy} />
        {refusal && <p className="mt-2 text-sm font-medium text-red-700">Refused: {refusal}</p>}
      </Section>
      <Section title={`Current: v${playbook.version}`} aside={
        <span className="text-xs text-neutral-500">
          {SOURCE_TEXT[playbook.source]}
          {playbook.source_run_id && <> (<Link href={`/runs/${playbook.source_run_id}`} className="text-sky-700 hover:underline">run #{playbook.source_run_id}</Link>)</>}
          {playbook.approved_by && <> · approved by {playbook.approved_by}</>}
        </span>
      }>
        <pre className="max-h-[32rem] overflow-auto rounded bg-neutral-50 p-3 text-xs">{playbook.yaml}</pre>
      </Section>
      <Section title="Versions">
        <ol className="space-y-2">
          {newestFirst.map((v) => (
            <li key={v.version} className="text-sm">
              <details>
                <summary className="cursor-pointer">
                  <span className="font-mono font-semibold">v{v.version}</span>{" "}
                  <span className={v.status === "approved" ? "text-emerald-700" : "text-neutral-500"}>{v.status}</span>
                  <span className="text-neutral-500"> · {SOURCE_TEXT[v.source]}</span>
                  {v.source_run_id && <span className="text-neutral-500"> (run #{v.source_run_id})</span>}
                  {v.approved_by && <span className="text-neutral-500"> · {v.approved_by}</span>}
                  <span className="text-neutral-500"> · {when(v.created_at)}</span>
                </summary>
                <pre className="mt-1 max-h-80 overflow-auto rounded bg-neutral-50 p-2 text-xs">{v.yaml}</pre>
              </details>
            </li>
          ))}
        </ol>
      </Section>
    </div>
  );
}
