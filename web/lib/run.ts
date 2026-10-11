// The workspace's view of a run's event stream. Pure, so it can be tested on a captured stream.
//
// The snapshot (GET /runs/{id}) stays the source for anything derived on the server: effective params,
// gate decisions, follow-ups, links, outcome. This reducer keeps only what the snapshot lacks: the
// investigation log with timings, lint retries, per-step attempts, and where the stream has got to.

import type { Confirmation, RunEvent, RunState, StepStatus } from "./types";

export type LogItem =
  | { seq: number; kind: "tool"; tool: string; args: Record<string, unknown>; ok: boolean; error: string | null; result: unknown; t: number }
  | { seq: number; kind: "finding"; text: string; t: number }
  | { seq: number; kind: "lint"; attempt: number; ok: boolean; problems: string[]; t: number };

export interface StepRun {
  stepId: string;
  action: string;
  attempts: number;
  status: Exclude<StepStatus, "pending">;
  error: string | null;
  skipped: string | null;
  t: number | null;
  /** Ran outside the main execution, i.e. a follow-up's "run now". */
  followup: boolean;
}

export interface View {
  last: number;
  state: RunState | null;
  log: LogItem[];
  steps: Record<string, StepRun>;
  stepOrder: string[];
  investigateSecs: number | null;
  executeSecs: number | null;
  errors: { seq: number; where: string; error: string }[];
  ended: boolean;
}

export const initialView: View = {
  last: 0, state: null, log: [], steps: {}, stepOrder: [], investigateSecs: null, executeSecs: null, errors: [],
  ended: false,
};

/** Event types after which the snapshot is stale. */
export const REFRESH_ON: ReadonlySet<string> = new Set([
  "state", "plan", "gate_decision", "approval", "step_verified", "step_failed", "link_viewed", "error", "end",
  "done_check", "message_sent", "message_blocked", "time_entry",
]);

type P = Record<string, unknown>;

const num = (v: unknown): number => (typeof v === "number" ? v : 0);

function step(view: View, p: P, change: (prev: StepRun | undefined) => Partial<StepRun>): View {
  const id = String(p.step_id);
  const prev = view.steps[id];
  const followup = prev?.followup ?? view.state !== "EXECUTING";
  const fresh: StepRun = {
    stepId: id, action: String(p.action), attempts: 0, status: "running", error: null, skipped: null, t: null, followup,
  };
  const next: StepRun = { ...(prev ?? fresh), followup, ...change(prev) };
  if (typeof p.t === "number") next.t = p.t;
  return {
    ...view,
    steps: { ...view.steps, [id]: next },
    stepOrder: prev ? view.stepOrder : [...view.stepOrder, id],
    executeSecs: !followup && typeof p.t === "number" ? Math.max(view.executeSecs ?? 0, p.t) : view.executeSecs,
  };
}

export function reduce(view: View, event: RunEvent): View {
  if (event.type === "end") return { ...view, ended: true, state: (event.data.state as RunState) ?? view.state };
  if (event.seq <= view.last) return view; // re-delivered after a reconnect
  const p = event.data;
  const base = { ...view, last: event.seq, ended: false };
  const investigated = (t: unknown) => Math.max(view.investigateSecs ?? 0, num(t));

  switch (event.type) {
    case "state":
      return { ...base, state: p.to as RunState };
    case "tool_call":
      return {
        ...base, investigateSecs: investigated(p.t),
        log: [...view.log, { seq: event.seq, kind: "tool", tool: String(p.tool), args: (p.args as P) ?? {}, ok: Boolean(p.ok),
                             error: (p.error as string) ?? null, result: p.result, t: num(p.t) }],
      };
    case "finding":
      return {
        ...base, investigateSecs: investigated(p.t),
        log: [...view.log, { seq: event.seq, kind: "finding", text: String(p.text), t: num(p.t) }],
      };
    case "lint_result":
      return {
        ...base, investigateSecs: investigated(p.t),
        log: [...view.log, { seq: event.seq, kind: "lint", attempt: num(p.attempt), ok: Boolean(p.ok),
                             problems: (p.problems as string[]) ?? [], t: num(p.t) }],
      };
    case "step_started":
      return step(base, p, (prev) => ({ status: "running", attempts: (prev?.attempts ?? 0) + 1, error: null }));
    case "step_verified":
      return step(base, p, (prev) => ({
        status: p.skipped ? "skipped" : "verified", skipped: (p.skipped as string) ?? null,
        attempts: Math.max(prev?.attempts ?? 0, 1), error: null,
      }));
    case "step_failed":
      return step(base, p, (prev) => ({
        status: "failed", error: String(p.error), attempts: Math.max(prev?.attempts ?? 0, 1),
      }));
    case "error":
      return { ...base, errors: [...view.errors, { seq: event.seq, where: String(p.where), error: String(p.error) }] };
    default:
      return base;
  }
}

/** The answers the API accepts (its ANSWER_KEYS), from the approver's edits to the confirm fields.
 * Only changed fields are sent: the plan already carries the proposal, and a proposal is the model's
 * prose (e.g. "2026-10-12; license assigned at 11:00 UTC"), which the API would refuse as an answer. */
export const ANSWER_KEYS = ["upn_format", "start_date"] as const;

export function answersFor(confirmations: Confirmation[], edits: Record<string, string>): Record<string, string> {
  const answers: Record<string, string> = {};
  for (const c of confirmations) {
    if (!(ANSWER_KEYS as readonly string[]).includes(c.key) || edits[c.key] === undefined) continue;
    const value = edits[c.key].trim();
    if (value && value !== c.proposed.trim()) answers[c.key] = value;
  }
  return answers;
}

export function formatSecs(secs: number): string {
  const s = Math.max(0, Math.round(secs));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
