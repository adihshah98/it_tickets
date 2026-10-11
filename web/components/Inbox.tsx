"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, createTicket, listRuns } from "@/lib/api";
import { SAMPLES, SENDERS, senderName } from "@/lib/samples";
import type { RunSummary } from "@/lib/types";
import { Section, StateBadge, when } from "./ui";

export default function Inbox() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [from, setFrom] = useState<string>(SENDERS[0].alias);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunSummary[] | null>(null);

  useEffect(() => {
    let stop = false;
    const load = () => listRuns().then((r) => !stop && setRuns(r)).catch(() => {});
    load();
    const timer = setInterval(load, 3000);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, []);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const { run_id } = await createTicket(text, from);
      router.push(`/runs/${run_id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <Section title="New ticket">
        <div className="mb-2 flex flex-wrap gap-2">
          {SAMPLES.map((s) => (
            <button
              key={s.label} type="button" onClick={() => { setText(s.text); setFrom(s.from); }}
              className="rounded border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-100"
            >
              {s.label}
            </button>
          ))}
        </div>
        <textarea
          className="h-32 w-full rounded border border-neutral-300 p-2 text-sm"
          placeholder="Paste the requester's email…" value={text} onChange={(e) => setText(e.target.value)}
        />
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <label className="text-sm text-neutral-600">
            From{" "}
            <select className="rounded border border-neutral-300 px-1 py-1" value={from} onChange={(e) => setFrom(e.target.value)}>
              {SENDERS.map((s) => <option key={s.alias} value={s.alias}>{s.name}{s.note && ` (${s.note})`}</option>)}
            </select>
          </label>
          <button
            type="button" disabled={busy || !text.trim()} onClick={submit}
            className="ml-auto rounded bg-neutral-900 px-4 py-1.5 text-sm font-semibold text-white disabled:opacity-40"
          >
            {busy ? "Starting…" : "Start"}
          </button>
        </div>
        {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
      </Section>

      <Section title="Recent runs">
        {runs === null && <p className="text-sm text-neutral-500">Loading…</p>}
        {runs?.length === 0 && <p className="text-sm text-neutral-500">No runs yet.</p>}
        <ul className="divide-y divide-neutral-100">
          {runs?.map((r) => (
            <li key={r.id}>
              <Link href={`/runs/${r.id}`} className="flex items-center gap-3 py-2 text-sm hover:bg-neutral-50">
                <span className="w-10 font-mono text-xs text-neutral-400">#{r.id}</span>
                <StateBadge state={r.state} />
                <span className="w-32 shrink-0 truncate">{senderName(r.from)}</span>
                <span className="truncate text-neutral-600">{r.subject}</span>
                <span className="ml-auto shrink-0 text-xs text-neutral-400">{when(r.created_at)}</span>
              </Link>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}
