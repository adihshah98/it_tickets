"use client";

// /playbooks/[intent]: with ?run=N, review that run's draft (Save as playbook, or the audit logs); without it,
// the intent's approved playbook, its autonomy and its versions.

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, getPlaybook, getRun } from "@/lib/api";
import { sourceView, SOURCE_TEXT } from "@/lib/playbooks";
import type { PlaybookView, Snapshot } from "@/lib/types";
import { PlaybookVersions } from "./Playbooks";
import { PlaybookReview } from "./PlaybookReview";

export default function PlaybookPage() {
  const intent = decodeURIComponent(useParams<{ intent: string }>().intent);
  const runParam = useSearchParams().get("run");
  const runId = runParam ? Number(runParam) : null;

  const [playbook, setPlaybook] = useState<PlaybookView | null>(null);
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, s] = await Promise.all([
        getPlaybook(intent).catch((e) => {
          if (e instanceof ApiError && e.status === 404) return null; // a new intent: no playbook yet
          throw e;
        }),
        runId ? getRun(runId) : Promise.resolve(null),
      ]);
      setPlaybook(p);
      setSnap(s);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setLoaded(true);
    }
  }, [intent, runId]);

  useEffect(() => {
    // fetch-on-mount; the setState happens after the await
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  if (!loaded) return <p className="text-sm text-neutral-500">Loading…</p>;
  if (error) return <p className="text-sm text-red-700">Couldn&apos;t load {intent}: {error}</p>;

  const draft = snap?.playbook_draft ?? null;
  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-baseline gap-3">
        <Link href="/playbooks" className="text-sm text-sky-700 hover:underline">← Playbooks</Link>
        <h1 className={runId ? "text-lg font-semibold" : "font-mono text-lg font-semibold"}>
          {runId ? `Review the draft from run #${runId}` : intent}
        </h1>
        {playbook && !runId && (
          <span className="text-sm text-neutral-500">v{playbook.version} · {SOURCE_TEXT[playbook.source]} · {playbook.autonomy}</span>
        )}
      </header>

      {runId && snap && !draft && (
        <p className="text-sm">Run #{runId} has no draft yet. Use Save as playbook on <Link href={`/runs/${runId}`} className="text-sky-700 hover:underline">the run</Link>.</p>
      )}
      {runId && snap && draft && (
        <PlaybookReview
          key={`${runId}-${draft.yaml.length}`} intent={draft.intent} yaml={draft.yaml} source={draft.source}
          sourceRunId={runId} sourceView={sourceView(snap, draft.source)}
          drafterProblems={draft.problems}
        />
      )}
      {!runId && playbook && <PlaybookVersions playbook={playbook} onChanged={load} />}
      {!runId && !playbook && (
        <p className="text-sm">No playbook for <span className="font-mono">{intent}</span> yet. <Link href="/playbooks/new" className="text-sky-700 hover:underline">Write one</Link>, or save a finished run as one.</p>
      )}
    </div>
  );
}
