import type { LogItem } from "@/lib/run";
import { LazyJson, Section, short } from "./ui";

function args(values: Record<string, unknown>) {
  return Object.entries(values).map(([k, v]) => `${k}=${short(v)}`).join(", ");
}

function summary(result: unknown) {
  if (Array.isArray(result)) return `${result.length} result${result.length === 1 ? "" : "s"}`;
  return result && typeof result === "object" ? "1 object" : short(result);
}

export function InvestigationLog({ log, active, secs }: { log: LogItem[]; active: boolean; secs: number | null }) {
  return (
    <Section
      title="Investigation log"
      aside={<span className="text-xs text-neutral-500">{active ? "investigating…" : secs !== null && `${secs.toFixed(1)} s`}</span>}
    >
      {log.length === 0 && <p className="text-sm text-neutral-500">Waiting for the first read…</p>}
      <ol className="space-y-1.5">
        {log.map((item) => (
          <li key={item.seq} className="grid grid-cols-[3.5rem_1fr] gap-2 text-sm">
            <span className="pt-0.5 text-right font-mono text-xs text-neutral-400">{item.t.toFixed(1)}s</span>
            {item.kind === "tool" && (
              <div>
                <span className={item.ok ? "text-neutral-400" : "text-red-600"}>{item.ok ? "↳" : "✖"}</span>{" "}
                <span className="font-mono text-xs">{item.tool}</span>
                <span className="font-mono text-xs text-neutral-500">({args(item.args)})</span>{" "}
                {item.ok
                  ? <LazyJson label={summary(item.result)} value={item.result} />
                  : <span className="text-xs text-red-700">{item.error}</span>}
              </div>
            )}
            {item.kind === "finding" && <p className="font-medium">● {item.text}</p>}
            {item.kind === "lint" && (
              <div className={item.ok ? "text-emerald-700" : "text-amber-800"}>
                {item.ok ? "Plan passed the linter" : `Plan rejected by the linter (attempt ${item.attempt}), retrying:`}
                {!item.ok && <ul className="ml-5 list-disc text-xs">{item.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
              </div>
            )}
          </li>
        ))}
      </ol>
    </Section>
  );
}
