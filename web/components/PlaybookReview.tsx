"use client";

// The review screen (Build-Day21 part 5): a draft's YAML, editable, side by side with what it was learned
// from; the code checks re-run as you edit; an autonomy level within the ceiling; then Approve. Everything is
// checked again on the server when approving.

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, approvePlaybook, checkPlaybook, getPlaybook } from "@/lib/api";
import { INTENT_RE } from "@/lib/learning";
import { allowedLevels, defaultLevel, type Level, renameIntent, SOURCE_TEXT, type SourceView } from "@/lib/playbooks";
import { TECHNICIANS } from "@/lib/samples";
import type { PlaybookSource, PlaybookSummary } from "@/lib/types";
import { AutonomyPicker, SourcePanel } from "./Playbooks";
import { Section } from "./ui";

type Check = { problems: string[]; ceiling: Level | null } | null;

export function PlaybookReview({ intent: initialIntent, yaml: initialYaml, source, sourceRunId, sourceView,
                                 drafterProblems = [] }: {
  intent: string; yaml: string; source: PlaybookSource; sourceRunId: number | null; sourceView: SourceView;
  drafterProblems?: string[];
}) {
  const router = useRouter();
  const [intent, setIntent] = useState(initialIntent);
  const [yaml, setYaml] = useState(initialYaml);
  const [check, setCheck] = useState<Check>(null);
  // the intent's approved playbook, if it has one (approving makes the next version); follows a rename
  const [current, setCurrent] = useState<PlaybookSummary | null>(null);
  const [level, setLevel] = useState<Level | null>(null);
  const [by, setBy] = useState<string>(TECHNICIANS[0]);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string[] | null>(null);
  const slugOk = INTENT_RE.test(intent);

  // the checks are code only (no model call), so re-run them shortly after each edit
  useEffect(() => {
    let alive = true;
    const timer = setTimeout(async () => {
      if (!slugOk) {
        setCheck({ problems: ["name the job: dot-separated lowercase words, e.g. site.grant_access"], ceiling: null });
        return;
      }
      try {
        const [result, existing] = await Promise.all([
          checkPlaybook(intent, yaml, source, sourceRunId),
          getPlaybook(intent).catch((e) => {
            if (e instanceof ApiError && e.status === 404) return null;
            throw e;
          }),
        ]);
        if (alive) {
          setCheck(result);
          setCurrent(existing);
        }
      } catch (e) {
        if (alive) setCheck({ problems: [e instanceof ApiError ? e.detail : String(e)], ceiling: null });
      }
    }, 500);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [intent, yaml, source, sourceRunId, slugOk]);

  const ceiling = check?.ceiling ?? null;
  // an edit can lower the ceiling under a level picked earlier: fall back to the default then
  const chosen = level && allowedLevels(ceiling).includes(level) ? level : defaultLevel(ceiling);
  const passes = check !== null && check.problems.length === 0;

  function rename(next: string) {
    setIntent(next);
    if (INTENT_RE.test(next)) setYaml((y) => renameIntent(y, next));
  }

  async function approve() {
    setBusy(true);
    setRefusal(null);
    try {
      await approvePlaybook(intent, { yaml, autonomy: chosen, by, source, source_run_id: sourceRunId });
      router.push(`/playbooks/${encodeURIComponent(intent)}`);
    } catch (e) {
      setRefusal(e instanceof ApiError && e.problems.length ? e.problems : [e instanceof ApiError ? e.detail : String(e)]);
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-neutral-600">
        Draft {SOURCE_TEXT[source]}. Nothing is saved until you approve it
        {current ? <>; it becomes <span className="font-mono">v{current.version + 1}</span> of {current.intent} (now v{current.version}, {current.autonomy})</> : ""}.
      </p>

      <div className="grid gap-4 md:grid-cols-2">
        <Section title="Playbook" tone={check === null ? "plain" : passes ? "good" : "bad"}>
          <label className="block text-sm">
            <span className="font-medium">Intent</span>
            <input className="mt-0.5 w-full rounded border border-neutral-300 px-2 py-1 font-mono text-sm"
                   value={intent} onChange={(e) => rename(e.target.value.trim())} />
          </label>
          <textarea
            className="mt-3 h-[32rem] w-full rounded border border-neutral-300 p-2 font-mono text-xs leading-relaxed"
            spellCheck={false} wrap="off" value={yaml} onChange={(e) => setYaml(e.target.value)}
          />
          <div className="mt-2 text-sm">
            {check === null ? (
              <p className="text-neutral-500">Checking…</p>
            ) : passes ? (
              <p className="text-emerald-700">✔ Passes every check</p>
            ) : (
              <ul className="space-y-1 text-red-800">
                {check.problems.map((p) => <li key={p}>✖ {p}</li>)}
              </ul>
            )}
            {drafterProblems.length > 0 && yaml === initialYaml && (
              <p className="mt-1 text-xs text-neutral-500">The drafter couldn&apos;t make it pass; edit it here.</p>
            )}
          </div>
        </Section>
        <Section title="Learned from">
          <SourcePanel view={sourceView} runId={sourceRunId} />
        </Section>
      </div>

      <Section title="Approve">
        <AutonomyPicker value={chosen} ceiling={ceiling} onChange={setLevel} disabled={!passes || busy} />
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t pt-3">
          <label className="text-sm text-neutral-600">
            Approving as{" "}
            <select className="rounded border border-neutral-300 px-1 py-1" value={by} onChange={(e) => setBy(e.target.value)}>
              {TECHNICIANS.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <button
            type="button" disabled={!passes || busy} onClick={approve}
            className="rounded bg-emerald-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-40"
          >
            {busy ? "Approving…" : `Approve at ${chosen}`}
          </button>
        </div>
        {refusal && (
          <ul className="mt-2 space-y-1 text-sm font-medium text-red-700">
            {refusal.map((r) => <li key={r}>Refused: {r}</li>)}
          </ul>
        )}
      </Section>
    </div>
  );
}
