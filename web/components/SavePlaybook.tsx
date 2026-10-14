"use client";

// "Save as playbook" (Build-Day19, Build-Day21 decision 10): draft a playbook from a finished run (one Opus
// call), then review and approve it on /playbooks/[intent]. Nothing is saved until it's approved there.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError, draftPlaybook } from "@/lib/api";
import { defaultIntent, INTENT_RE, reviewHref } from "@/lib/learning";
import type { Snapshot } from "@/lib/types";
import { Section } from "./ui";

export function SavePlaybook({ snap }: { snap: Snapshot }) {
  const router = useRouter();
  const [intent, setIntent] = useState(() => defaultIntent(snap));
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const valid = INTENT_RE.test(intent.trim());
  const drafted = snap.playbook_draft && snap.playbook_draft.source !== "audit_log" ? snap.playbook_draft : null;

  async function save() {
    setBusy(true);
    setRefusal(null);
    try {
      const lines = notes.split("\n").map((l) => l.trim()).filter(Boolean);
      const draft = await draftPlaybook(snap.run.id, intent.trim(), lines);
      router.push(reviewHref(draft.intent, snap.run.id));
    } catch (e) {
      setRefusal(e instanceof ApiError ? e.detail : String(e));
      setBusy(false);
    }
  }

  return (
    <Section title="Save as playbook" tone="good">
      <p className="text-sm text-neutral-600">
        No playbook covered this{snap.technician_steps.length > 0 ? ", and you did the work yourself" : ""}. Save what
        was done as a playbook, and the next ticket like it follows it. You review and approve it before it&apos;s used.
      </p>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <label className="block text-sm">
          <span className="font-medium">Intent</span>
          <input className="mt-0.5 w-full rounded border border-neutral-300 px-2 py-1 font-mono text-sm"
                 placeholder="e.g. site.grant_access" value={intent} onChange={(e) => setIntent(e.target.value)} />
          <span className="text-xs text-neutral-500">
            {valid ? "the job this playbook is for" : "name the job: dot-separated lowercase words"}
          </span>
        </label>
        <label className="block text-sm">
          <span className="font-medium">Notes for the drafter</span>{" "}
          <span className="text-xs text-neutral-400">optional, one per line</span>
          <textarea className="mt-0.5 w-full rounded border border-neutral-300 px-2 py-1 text-sm" rows={2}
                    placeholder="e.g. only for sites the requester's own team uses"
                    value={notes} onChange={(e) => setNotes(e.target.value)} />
        </label>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <button
          type="button" disabled={busy || !valid} onClick={save}
          className="rounded bg-emerald-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-40"
        >
          {busy ? "Drafting… (about a minute)" : drafted ? "Draft again" : "Save as playbook"}
        </button>
        {drafted && (
          <Link href={reviewHref(drafted.intent, snap.run.id)} className="text-sm text-sky-700 hover:underline">
            Review the last draft ({drafted.intent}) →
          </Link>
        )}
      </div>
      {refusal && <p className="mt-2 text-sm font-medium text-red-700">Refused: {refusal}</p>}
    </Section>
  );
}
