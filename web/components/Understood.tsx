import type { Triage } from "@/lib/types";
import { Section } from "./ui";

export function Understood({ triage, approved }: { triage: Triage; approved: boolean | null }) {
  const { entities: e } = triage;
  const rows: [string, string | null][] = [
    ["Intent", triage.intents.join(", ")],
    ["New user", e.new_user && [e.new_user.display_name, e.new_user.department, e.new_user.job_title,
                                e.new_user.start_date && `starts ${e.new_user.start_date}`].filter(Boolean).join(" · ")],
    ["Same setup as", e.peer_user_hint],
    ["About", e.subject_user_hint],
    ["Apps", e.apps.join(", ") || null],
  ];
  return (
    <Section title="Understood">
      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
        {rows.filter(([, v]) => v).map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-neutral-500">{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
        <dt className="text-neutral-500">Requester</dt>
        <dd>
          {triage.requester.email}{" "}
          {approved
            ? <span className="text-emerald-700">✔ approved requester</span>
            : <span className="font-semibold text-red-700">✖ not an approved requester</span>}
        </dd>
      </dl>
      {triage.out_of_scope_asks.length > 0 && (
        <div className="mt-3 rounded border border-red-300 bg-red-50 p-2 text-sm text-red-900">
          <span className="font-semibold">Out of scope, won&apos;t act on:</span>
          <ul className="ml-5 list-disc">{triage.out_of_scope_asks.map((a) => <li key={a}>{a}</li>)}</ul>
        </div>
      )}
      {triage.missing_info.length > 0 && (
        <p className="mt-2 text-xs text-neutral-500">Missing from the ticket: {triage.missing_info.join("; ")}</p>
      )}
    </Section>
  );
}
