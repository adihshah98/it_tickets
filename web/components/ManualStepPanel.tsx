"use client";

// "Do it myself" (Build-Day20, Build-Day21 part 3): on an escalated run, the technician runs catalogue actions
// themselves. Each is gated (never and client rules still apply), run and verified like the agent's, and
// tagged with their name. Then they close the ticket: the definition of done, the reply and the PSA note.

import { useEffect, useMemo, useState } from "react";
import { ApiError, catalogue, closeRun, manualStep, observed as getObserved } from "@/lib/api";
import {
  type CatalogueAction, type Field, fields as fieldsOf, type Observed, options, technicianName, toParams,
} from "@/lib/console";
import { TECHNICIANS } from "@/lib/samples";
import type { Snapshot, TechnicianStep } from "@/lib/types";
import { KeyValues, Section } from "./ui";

const TYPED = "__typed__";

const message = (e: unknown) => (e instanceof ApiError ? e.detail : String(e));

const ICON = { blocked: "⊘", pending: "○", running: "◌", verified: "✔", skipped: "⤼", failed: "✖" } as const;
const COLOR = {
  blocked: "text-red-600", pending: "text-neutral-400", running: "text-indigo-600 animate-pulse",
  verified: "text-emerald-600", skipped: "text-emerald-600", failed: "text-red-600",
} as const;

function ConsoleSteps({ steps }: { steps: TechnicianStep[] }) {
  return (
    <ol className="space-y-1.5">
      {steps.map((s) => (
        <li key={s.step_id} className="text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`w-4 text-center ${COLOR[s.status]}`}>{ICON[s.status]}</span>
            <span className="font-mono">{s.action}</span>
            <span className="font-mono text-xs text-neutral-500">{s.step_id}</span>
            <span className="text-xs text-neutral-500">
              {s.status === "verified" && "verified"}
              {s.status === "skipped" && "already done"}
              {s.status === "blocked" && "blocked"}
              {s.status === "failed" && "failed"}
            </span>
            <span className="rounded bg-sky-100 px-1.5 py-0.5 text-xs text-sky-900">by {technicianName(s.actor)}</span>
            <span className="font-mono text-xs text-neutral-400">{s.rule_id}</span>
          </div>
          {s.params && (
            <details className="ml-6">
              <summary className="cursor-pointer text-xs text-neutral-500">params</summary>
              <div className="mt-1"><KeyValues values={s.params} /></div>
            </details>
          )}
          {s.status === "blocked" && <p className="ml-6 text-xs text-red-800">{s.reason}</p>}
          {s.status === "failed" && <p className="ml-6 rounded bg-red-50 p-1 text-xs text-red-900">{s.error}</p>}
        </li>
      ))}
    </ol>
  );
}

function FieldInput({ field, observed, values, set }: {
  field: Field; observed: Observed[]; values: Record<string, string>; set: (name: string, value: string) => void;
}) {
  const [typing, setTyping] = useState(false);
  const value = values[field.name] ?? "";
  const input = "w-full rounded border border-neutral-300 px-2 py-1 text-sm";
  const picked = ["id", "file", "version"].includes(field.kind);
  const choices = picked ? options(field, observed, values) : [];

  let control;
  if (field.kind === "id" && (typing || !choices.length)) {
    control = (
      <div className="flex gap-2">
        <input className={`${input} font-mono`} placeholder="object ID (GUID): checked with one read before it runs"
               value={value} onChange={(e) => set(field.name, e.target.value)} />
        {choices.length > 0 && (
          <button type="button" className="text-xs text-sky-700 hover:underline" onClick={() => { setTyping(false); set(field.name, ""); }}>
            pick
          </button>
        )}
      </div>
    );
  } else if (picked) {
    control = choices.length ? (
      <select className={input} value={value} onChange={(e) => {
        if (e.target.value === TYPED) {
          setTyping(true);
          set(field.name, "");
        } else {
          set(field.name, e.target.value);
        }
      }}>
        <option value="">Choose…</option>
        {choices.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        {field.kind === "id" && <option value={TYPED}>Type an ID…</option>}
      </select>
    ) : (
      <p className="text-xs text-neutral-500">
        {field.kind === "version" ? "Pick the file first." : "This run's reads didn't see one."}
      </p>
    );
  } else if (field.kind === "bool") {
    control = (
      <select className={input} value={value} onChange={(e) => set(field.name, e.target.value)}>
        <option value="">{field.required ? "Choose…" : "Leave as is"}</option>
        <option value="true">Yes</option>
        <option value="false">No</option>
      </select>
    );
  } else if (field.kind === "enum") {
    control = (
      <select className={input} value={value} onChange={(e) => set(field.name, e.target.value)}>
        <option value="">Choose…</option>
        {field.options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  } else {
    control = (
      <input className={input} value={value} placeholder={field.kind === "list" ? "comma-separated" : undefined}
             onChange={(e) => set(field.name, e.target.value)} />
    );
  }
  return (
    <label className="block text-sm">
      <span className="font-medium">{field.label}</span>
      {!field.required && <span className="text-xs text-neutral-400"> optional</span>}
      <div className="mt-0.5">{control}</div>
    </label>
  );
}

export function ManualStepPanel({ snap, onChanged, onClosing }: {
  snap: Snapshot; onChanged: () => void; onClosing: () => void;
}) {
  const runId = snap.run.id;
  const escalated = snap.run.state === "ESCALATED";
  const steps = snap.technician_steps;
  const closable = escalated && steps.some((s) => s.status === "verified" || s.status === "skipped");

  const [by, setBy] = useState<string>(TECHNICIANS[0]);
  const [actions, setActions] = useState<CatalogueAction[]>([]);
  const [observed, setObserved] = useState<Observed[]>([]);
  const [actionName, setActionName] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<"step" | "close" | null>(null);
  const [answer, setAnswer] = useState<{ ok: boolean; text: string } | null>(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    if (!escalated) return;
    let alive = true;
    Promise.all([catalogue(), getObserved(runId)])
      .then(([c, o]) => {
        if (!alive) return;
        setActions(c.filter((a) => a.console));
        setObserved(o);
      })
      .catch((e) => alive && setAnswer({ ok: false, text: message(e) }));
    return () => {
      alive = false;
    };
    // the observed list grows with each typed ID's confirming read: re-read it after every step
  }, [escalated, runId, steps.length]);

  const action = actions.find((a) => a.name === actionName);
  const form = useMemo(() => (action ? fieldsOf(action) : []), [action]);
  const { params, missing } = toParams(form, values, observed);

  async function run() {
    if (!action) return;
    setBusy("step");
    setAnswer(null);
    try {
      const r = await manualStep(runId, by, action.name, params);
      setAnswer(r.ok
        ? { ok: true, text: `${r.step_id}: ${action.name} verified.` }
        : { ok: false, text: `${r.step_id}: not done. ${r.error ?? ""}` });
      if (r.ok) setValues({});
      onChanged();
    } catch (e) {
      setAnswer({ ok: false, text: `Refused: ${message(e)}` });
    } finally {
      setBusy(null);
    }
  }

  async function close() {
    setBusy("close");
    setAnswer(null);
    try {
      await closeRun(runId, by, note.trim() || null);
      onClosing();
    } catch (e) {
      setAnswer({ ok: false, text: `Refused: ${message(e)}` });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Section
      title="Do it myself"
      aside={escalated && (
        <label className="text-xs text-neutral-600">
          Working as{" "}
          <select className="rounded border border-neutral-300 px-1 py-0.5" value={by} onChange={(e) => setBy(e.target.value)}>
            {TECHNICIANS.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
      )}
    >
      {escalated && (
        <p className="mb-3 text-sm text-neutral-600">
          Make the change yourself from the catalogue. It&apos;s checked by the same gate (never and client rules
          still apply), then run and verified like the agent&apos;s, under your name.
        </p>
      )}

      {steps.length > 0 && <div className="mb-3"><ConsoleSteps steps={steps} /></div>}

      {escalated && (
        <div className="space-y-3 rounded border border-neutral-200 bg-neutral-50 p-3">
          <label className="block text-sm">
            <span className="font-medium">Action</span>
            <select className="mt-0.5 w-full rounded border border-neutral-300 px-2 py-1 text-sm" value={actionName}
                    onChange={(e) => { setActionName(e.target.value); setValues({}); setAnswer(null); }}>
              <option value="">Choose an action…</option>
              {actions.map((a) => <option key={a.name} value={a.name}>{a.name} ({a.risk_class})</option>)}
            </select>
          </label>
          {action && (
            <>
              <p className="text-xs text-neutral-500">Verified by: {action.verify}</p>
              {form.map((f) => (
                <FieldInput key={`${action.name}-${f.name}`} field={f} observed={observed} values={values}
                            set={(name, value) => setValues((v) => ({ ...v, [name]: value }))} />
              ))}
              <div className="flex items-center gap-3">
                <button
                  type="button" disabled={busy !== null || missing.length > 0} onClick={run}
                  className="rounded bg-indigo-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-40"
                >
                  {busy === "step" ? "Running… (gate, change, verify)" : "Run"}
                </button>
                {missing.length > 0 && <span className="text-xs text-neutral-500">Needs: {missing.join(", ")}</span>}
              </div>
            </>
          )}
        </div>
      )}

      {answer && (
        <p className={`mt-2 text-sm ${answer.ok ? "text-emerald-700" : "font-medium text-red-700"}`}>{answer.text}</p>
      )}

      {closable && (
        <div className="mt-4 border-t pt-3">
          <h3 className="text-sm font-semibold">Close the ticket</h3>
          <p className="text-xs text-neutral-500">
            Checks the definition of done over your verified steps, then sends the reply and the PSA note.
          </p>
          <textarea
            className="mt-2 w-full rounded border border-neutral-300 px-2 py-1 text-sm" rows={2}
            placeholder="Note for the PSA (and for the playbook, if this run is saved as one), e.g. Taylor confirmed by phone"
            value={note} onChange={(e) => setNote(e.target.value)}
          />
          <button
            type="button" disabled={busy !== null} onClick={close}
            className="mt-2 rounded bg-emerald-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-40"
          >
            {busy === "close" ? "Closing…" : "Close ticket"}
          </button>
        </div>
      )}
    </Section>
  );
}
