"use client";

// /playbooks (Build-Day21 decision 14): shipped and learned playbooks together, with where each came from, its
// autonomy, and one-click demote to A2 (the next run asks for approval again).

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ApiError, listPlaybooks, setAutonomy } from "@/lib/api";
import { SOURCE_TEXT } from "@/lib/playbooks";
import { TECHNICIANS } from "@/lib/samples";
import type { PlaybookSummary } from "@/lib/types";
import { when } from "./ui";

export default function PlaybookLibrary() {
  const [rows, setRows] = useState<PlaybookSummary[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await listPlaybooks());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }, []);

  useEffect(() => {
    // fetch-on-mount; the setState happens after the await
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  async function demote(intent: string) {
    setBusy(intent);
    try {
      await setAutonomy(intent, "A2", TECHNICIANS[0]);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4">
      <header className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">Playbooks</h1>
        <span className="text-sm text-neutral-500">what the agent may do for each kind of ticket, and how freely</span>
        <Link href="/playbooks/new"
              className="ml-auto rounded bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-indigo-700">
          New playbook
        </Link>
      </header>
      {error && <p className="text-sm text-red-700">{error}</p>}
      {!rows && !error && <p className="text-sm text-neutral-500">Loading…</p>}
      {rows && (
        <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
          <table className="w-full text-sm">
            <thead className="border-b text-left text-xs uppercase tracking-wide text-neutral-500">
              <tr>
                <th className="px-3 py-2">Intent</th>
                <th className="px-3 py-2">Version</th>
                <th className="px-3 py-2">Source</th>
                <th className="px-3 py-2">Autonomy</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.intent} className="border-b last:border-0">
                  <td className="px-3 py-2">
                    <Link href={`/playbooks/${encodeURIComponent(p.intent)}`} className="font-mono text-sky-700 hover:underline">
                      {p.intent}
                    </Link>
                  </td>
                  <td className="px-3 py-2 font-mono">v{p.version}</td>
                  <td className="px-3 py-2">
                    <span className={p.source === "shipped" ? "text-neutral-600" : "rounded bg-violet-100 px-1.5 py-0.5 text-violet-900"}>
                      {SOURCE_TEXT[p.source]}
                    </span>
                    {p.source_run_id && (
                      <> · <Link href={`/runs/${p.source_run_id}`} className="text-sky-700 hover:underline">from run #{p.source_run_id}</Link></>
                    )}
                    {p.approved_by && (
                      <div className="text-xs text-neutral-500">approved by {p.approved_by} · {when(p.created_at)}</div>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <span className="font-mono font-semibold">{p.autonomy}</span>
                    {p.ceiling !== "A3" && <span className="text-xs text-neutral-500"> (A3 once a run on it succeeds)</span>}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {p.autonomy === "A3" && (
                      <button
                        type="button" disabled={busy !== null} onClick={() => demote(p.intent)}
                        className="rounded border border-amber-400 px-2 py-1 text-xs font-semibold text-amber-800 hover:bg-amber-50 disabled:opacity-40"
                      >
                        {busy === p.intent ? "Demoting…" : "Demote to A2"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
