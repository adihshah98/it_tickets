import type { Plan, Step } from "@/lib/types";
import { DecisionLabel, KeyValues, Section } from "./ui";

export function PlanSteps({ plan, steps }: { plan: Plan; steps: Step[] }) {
  const counts = (["auto", "approve", "block"] as const).map((d) => [d, steps.filter((s) => s.decision === d).length] as const);
  return (
    <Section
      title="Plan"
      tone={plan.escalate ? "bad" : "plain"}
      aside={<span className="text-xs text-neutral-500">{counts.filter(([, n]) => n).map(([d, n]) => `${n} ${d}`).join(" · ")}</span>}
    >
      <p className="text-sm">{plan.summary}</p>
      {plan.escalate && (
        <div className="mt-2 rounded bg-red-50 p-2 text-sm text-red-950">
          <p className="font-semibold text-red-700">Escalating, nothing will be changed</p>
          <p className="mt-1 whitespace-pre-wrap">{plan.escalation_reason}</p>
        </div>
      )}
      {plan.assumptions.length > 0 && (
        <details className="mt-2 text-sm">
          <summary className="cursor-pointer text-neutral-500">Assumptions ({plan.assumptions.length})</summary>
          <ul className="ml-5 list-disc">{plan.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
        </details>
      )}
      <ol className="mt-3 space-y-2">
        {steps.map((s, i) => (
          <li key={s.id} className={`rounded border p-2 ${
            s.decision === "approve" ? "border-amber-300 bg-amber-50" : s.decision === "block" ? "border-red-300 bg-red-50" : "border-neutral-200"}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-neutral-400">{i + 1}</span>
              <span className="font-mono text-sm font-semibold">{s.action}</span>
              <span className="font-mono text-xs text-neutral-500">{s.id}</span>
              <DecisionLabel decision={s.decision} />
              {s.rule_id && <span className="font-mono text-xs text-neutral-500" title={s.reason ?? ""}>{s.rule_id}</span>}
              {s.depends_on.length > 0 && <span className="text-xs text-neutral-400">after {s.depends_on.join(", ")}</span>}
            </div>
            <p className="mt-1 text-sm">{s.rationale}</p>
            {s.decision === "block" && <p className="text-sm font-semibold text-red-700">{s.reason}</p>}
            <details className="mt-1">
              <summary className="cursor-pointer text-xs text-neutral-500">params and expected result</summary>
              <div className="mt-1 grid gap-2 md:grid-cols-2">
                <KeyValues values={s.params} />
                <KeyValues values={s.expected} />
              </div>
            </details>
          </li>
        ))}
      </ol>
    </Section>
  );
}
