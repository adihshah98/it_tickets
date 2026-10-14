"use client";

// /playbooks/new (Build-Day21 part 6, learning case L3): a technician writes a playbook in plain English and
// picks the catalogue actions it may use; the drafter turns it into a playbook, which goes through the same
// review. A written playbook is capped at A2 until a run on it succeeds.

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, catalogue, type DraftView, writtenPlaybook } from "@/lib/api";
import type { CatalogueAction } from "@/lib/console";
import { INTENT_RE } from "@/lib/learning";
import { TECHNICIANS } from "@/lib/samples";
import { PlaybookReview } from "./PlaybookReview";
import { Section } from "./ui";

export default function NewPlaybook() {
  const [actions, setActions] = useState<CatalogueAction[]>([]);
  const [intent, setIntent] = useState("");
  const [text, setText] = useState("");
  const [allowed, setAllowed] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ view: DraftView; text: string; allowed: string[] } | null>(null);

  useEffect(() => {
    let alive = true;
    catalogue()
      .then((c) => alive && setActions(c))
      .catch((e) => alive && setRefusal(e instanceof ApiError ? e.detail : String(e)));
    return () => {
      alive = false;
    };
  }, []);

  const ready = INTENT_RE.test(intent) && text.trim().length > 0 && allowed.length > 0;

  async function submit() {
    setBusy(true);
    setRefusal(null);
    try {
      const view = await writtenPlaybook(intent, text.trim(), allowed, TECHNICIANS[0]);
      setDraft({ view, text: text.trim(), allowed });
    } catch (e) {
      setRefusal(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  const toggle = (name: string) =>
    setAllowed((a) => (a.includes(name) ? a.filter((n) => n !== name) : [...a, name]));

  if (draft) {
    return (
      <div className="space-y-4">
        <button type="button" onClick={() => setDraft(null)} className="text-sm text-sky-700 hover:underline">
          ← Back to what you wrote
        </button>
        <PlaybookReview
          intent={draft.view.intent} yaml={draft.view.yaml} source="written" sourceRunId={null}
          sourceView={{ kind: "written", text: draft.text, allowedActions: draft.allowed }}
          drafterProblems={draft.view.problems}
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <header className="flex items-baseline gap-3">
        <Link href="/playbooks" className="text-sm text-sky-700 hover:underline">← Playbooks</Link>
        <h1 className="text-lg font-semibold">New playbook</h1>
      </header>
      <Section title="Write a playbook">
        <p className="text-sm text-neutral-600">
          Describe the job the way you&apos;d explain it to a new technician: what to check first, what to change, and what
          never to do. The drafter turns it into a playbook you review before it&apos;s used. A written playbook asks for
          approval on every run until one succeeds.
        </p>
        <div className="mt-4 space-y-4">
          <label className="block text-sm">
            <span className="font-medium">Intent</span>
            <input className="mt-0.5 w-full rounded border border-neutral-300 px-2 py-1 font-mono text-sm"
                   placeholder="e.g. site.grant_access" value={intent} onChange={(e) => setIntent(e.target.value.trim())} />
            <span className="text-xs text-neutral-500">
              {intent && !INTENT_RE.test(intent) ? "dot-separated lowercase words, e.g. user.update_title" : "the job this playbook is for"}
            </span>
          </label>
          <label className="block text-sm">
            <span className="font-medium">Steps, in plain English</span>
            <textarea
              className="mt-0.5 w-full rounded border border-neutral-300 px-2 py-1 text-sm" rows={8}
              placeholder={"e.g. Find the person and the team site's Microsoft 365 group. Only add them if their manager or a " +
                "peer in the same role is already a member. Add them as a member, never as an owner, and never to an admin group."}
              value={text} onChange={(e) => setText(e.target.value)}
            />
          </label>
          <fieldset className="text-sm">
            <legend className="font-medium">Actions it may use</legend>
            <p className="text-xs text-neutral-500">only these; the gate blocks any other change on this kind of ticket</p>
            <div className="mt-1 grid gap-1 sm:grid-cols-2">
              {actions.map((a) => (
                <label key={a.name} className="flex items-baseline gap-2">
                  <input type="checkbox" checked={allowed.includes(a.name)} onChange={() => toggle(a.name)} />
                  <span className="font-mono">{a.name}</span>
                  <span className="text-xs text-neutral-500">{a.risk_class}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <button
            type="button" disabled={!ready || busy} onClick={submit}
            className="rounded bg-indigo-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-40"
          >
            {busy ? "Drafting… (about a minute)" : "Draft"}
          </button>
          {refusal && <p className="text-sm font-medium text-red-700">Refused: {refusal}</p>}
        </div>
      </Section>
    </div>
  );
}
