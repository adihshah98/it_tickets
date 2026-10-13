import type { StepRun } from "@/lib/run";
import type { Step } from "@/lib/types";
import { KeyValues, Section } from "./ui";

const WHY: Record<string, string> = {
  replication: "waiting for the new account to replicate", verify: "waiting for the change to show up",
  throttled: "Graph asked us to slow down", server_error: "Graph had a transient error",
};

const ICON = { pending: "○", running: "◌", verified: "✔", skipped: "⤼", failed: "✖" } as const;
const COLOR = {
  pending: "text-neutral-400", running: "text-indigo-600 animate-pulse", verified: "text-emerald-600",
  skipped: "text-emerald-600", failed: "text-red-600",
} as const;

/** The plan's steps as they run. Live status comes from the stream; the snapshot fills in after a reload. */
export function Execution({ steps, runs, secs }: { steps: Step[]; runs: Record<string, StepRun>; secs: number | null }) {
  return (
    <Section title="Live execution" aside={secs !== null && <span className="text-xs text-neutral-500">{secs.toFixed(1)} s</span>}>
      <ol className="space-y-1.5">
        {steps.map((s) => {
          const run = runs[s.id];
          const status = run?.status ?? s.status;
          const retries = (run?.retries ?? 0) + Math.max(0, (run?.attempts ?? 0) - 1);
          return (
            <li key={s.id} className="text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`w-4 text-center ${COLOR[status]}`}>{ICON[status]}</span>
                <span className="font-mono">{s.action}</span>
                <span className="font-mono text-xs text-neutral-500">{s.id}</span>
                <span className="text-xs text-neutral-500">
                  {status === "verified" && "verified"}
                  {status === "skipped" && `already done (${run?.skipped ?? "skipped"})`}
                  {status === "running" && "running…"}
                  {status === "failed" && "failed"}
                </span>
                {retries > 0 && (
                  <span className="text-xs font-semibold text-amber-700" title={run!.retryReasons.map((r) => WHY[r] ?? r).join("; ")}>
                    ↻ retried ×{retries}
                    {run!.waited > 0 && (
                      <span className="font-normal"> · {run!.retryReasons.map((r) => WHY[r] ?? r).join(", ")} ({run!.waited.toFixed(0)} s)</span>
                    )}
                  </span>
                )}
                {run?.t != null && <span className="ml-auto font-mono text-xs text-neutral-400">{run.t.toFixed(1)}s</span>}
              </div>
              {status === "failed" && (
                <div className="ml-6 mt-1 rounded bg-red-50 p-2 text-xs text-red-900">
                  {run?.error ?? s.error}
                  {s.observed && <div className="mt-1"><KeyValues values={s.observed} /></div>}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </Section>
  );
}
