// Small shared pieces: section card, state badge, decision label, key/value list, lazy JSON.

"use client";

import { useState, type ReactNode } from "react";
import type { Decision, RunState } from "@/lib/types";

export function Section({ title, aside, children, tone = "plain" }: {
  title: string; aside?: ReactNode; children: ReactNode; tone?: "plain" | "warn" | "bad" | "good";
}) {
  const border = { plain: "border-neutral-200", warn: "border-amber-400", bad: "border-red-400", good: "border-emerald-400" }[tone];
  return (
    <section className={`rounded-lg border ${border} bg-white p-4 shadow-sm`}>
      <div className="mb-3 flex items-baseline justify-between gap-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

const STATE_COLOR: Record<RunState, string> = {
  RECEIVED: "bg-neutral-100 text-neutral-700",
  TRIAGED: "bg-sky-100 text-sky-800",
  INVESTIGATING: "bg-sky-100 text-sky-800",
  PLAN_PROPOSED: "bg-sky-100 text-sky-800",
  PLAN_CHECKED: "bg-sky-100 text-sky-800",
  AWAITING_APPROVAL: "bg-amber-100 text-amber-900",
  HELD: "bg-amber-100 text-amber-900",
  EXECUTING: "bg-indigo-100 text-indigo-800",
  REPLANNING: "bg-indigo-100 text-indigo-800",
  CLOSING: "bg-indigo-100 text-indigo-800",
  DONE: "bg-emerald-100 text-emerald-800",
  ESCALATED: "bg-red-100 text-red-800",
};

export function StateBadge({ state }: { state: RunState }) {
  return (
    <span className={`whitespace-nowrap rounded px-2 py-0.5 font-mono text-xs font-semibold ${STATE_COLOR[state] ?? ""}`}>
      {state.replaceAll("_", " ")}
    </span>
  );
}

export function DecisionLabel({ decision }: { decision: Decision | null }) {
  if (!decision) return null;
  const style = { auto: "bg-emerald-100 text-emerald-800", approve: "bg-amber-100 text-amber-900", block: "bg-red-100 text-red-800" }[decision];
  const text = { auto: "auto", approve: "needs approval", block: "blocked" }[decision];
  return <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${style}`}>{text}</span>;
}

export function short(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

export function KeyValues({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values).filter(([, v]) => v !== null && v !== undefined);
  if (!entries.length) return null;
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 font-mono text-xs">
      {entries.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-neutral-500">{k}</dt>
          <dd className="break-all">{short(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A collapsed JSON block that renders its content only when opened (tool results can be large). */
export function LazyJson({ label, value }: { label: string; value: unknown }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" onClick={() => setOpen(!open)} className="text-xs text-sky-700 hover:underline">
        {open ? "▾" : "▸"} {label}
      </button>
      {open && (
        <pre className="mt-1 max-h-72 overflow-auto rounded bg-neutral-50 p-2 text-xs">{JSON.stringify(value, null, 2)}</pre>
      )}
    </div>
  );
}

export const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
