// Typed calls to the backend (backend/tech/api.py). The browser calls it directly; CORS allows this origin.

import type { CatalogueAction, Observed } from "./console";
import type { Level } from "./playbooks";
import type { LinkView, PlaybookSource, PlaybookSummary, PlaybookView, RunSummary, Snapshot } from "./types";

export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  /** `problems`: a 422's list (a playbook that fails its checks), one per item. */
  constructor(readonly status: number, readonly detail: string, readonly problems: string[] = []) {
    super(detail);
  }
}

/** A list `detail`: the playbook checks' problems (strings), or FastAPI's validation errors (objects with `msg`). */
function problemsOf(detail: unknown): string[] {
  if (!Array.isArray(detail)) return [];
  return detail.map((d) => (typeof d === "string" ? d : typeof d?.msg === "string" ? d.msg : JSON.stringify(d)));
}

async function call<T>(path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    // also how a 500 arrives: the server's error response carries no CORS headers
    throw new ApiError(0, `no response from the API at ${API} (not running, or it failed with a server error)`);
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail;
    const problems = problemsOf(detail);
    throw new ApiError(res.status,
                       typeof detail === "string" ? detail : problems.length ? problems.join(" ") : res.statusText,
                       problems);
  }
  return data as T;
}

export const createTicket = (text: string, from: string) =>
  call<{ run_id: number; ticket_id: number }>("/tickets", { text, from });

export const listRuns = (limit = 50) => call<RunSummary[]>(`/runs?limit=${limit}`);

export const getRun = (id: number) => call<Snapshot>(`/runs/${id}`);

export const decide = (id: number, by: string, decision: "approve" | "reject", answers: Record<string, string>) =>
  call<{ state: string }>(`/runs/${id}/approval`, { by, decision, answers });

/** The demo's "Manager confirmed via SMS" (mocked requester.confirmOutOfBand). */
export const confirmOob = (id: number, by: string) => call<{ state: string }>(`/runs/${id}/oob`, { by });

export const runNow = (followupId: number) =>
  call<{ followup_id: number; run_id: number }>(`/followups/${followupId}/run-now`, {});

// "Do it myself" (Build-Day20): one catalogue action by a technician on an ESCALATED run, then close it.

export const catalogue = () => call<CatalogueAction[]>("/catalogue");

export const observed = (id: number) => call<Observed[]>(`/runs/${id}/observed`);

/** Awaited until the step is verified: a gate block or a failed verify is `ok: false` with the reason. */
export const manualStep = (id: number, by: string, action: string, params: Record<string, unknown>) =>
  call<{ ok: boolean; step_id: string; error: string | null; state: string }>(
    `/runs/${id}/manual-step`, { by, action, params });

/** Starts the close-out in the background (DoD, reply, PSA note); follow it on the event stream. */
export const closeRun = (id: number, by: string, note: string | null) =>
  call<{ state: string }>(`/runs/${id}/close`, { by, note });

// Learning a playbook (Build-Day19/20): drafts are recorded on the run as `playbook_drafted`.

export interface DraftView {
  intent: string;
  yaml: string;
  problems: string[];
  ok: boolean;
  source: PlaybookSource;
  source_run_id: number | null;
}

/** "Save as playbook": one Opus call, awaited (about a minute). Nothing is saved until it's approved. */
export const draftPlaybook = (id: number, intent: string | null, notes: string[]) =>
  call<DraftView>(`/runs/${id}/playbook-draft`, { intent, notes });

/** "I fixed it in the admin center": polls the audit logs in the background (up to 10 minutes), then drafts. */
export const auditDraft = (id: number, by: string) => call<{ state: string }>(`/runs/${id}/audit-draft`, { by });

// The playbook library (Build-Day19): review, check, approve, autonomy.

export const getPlaybook = (intent: string) => call<PlaybookView>(`/playbooks/${encodeURIComponent(intent)}`);

export const listPlaybooks = () => call<PlaybookSummary[]>("/playbooks");

/** /playbooks/new: a playbook written in plain English (or YAML) -> a draft. One Opus call; nothing saved, and not
 * recorded anywhere (no run), so the page keeps it. */
export const writtenPlaybook = (intent: string, text: string, allowedActions: string[], by: string) =>
  call<DraftView>("/playbooks", { intent, text, allowed_actions: allowedActions, by });

/** Approve's checks without saving: the problems, and the highest autonomy it could be approved at. */
export const checkPlaybook = (intent: string, yaml: string, source: PlaybookSource, sourceRunId: number | null) =>
  call<{ problems: string[]; ceiling: Level | null }>(`/playbooks/${encodeURIComponent(intent)}/check`,
                                                       { yaml, source, source_run_id: sourceRunId });

/** Saves the next version; a 422's `problems` say why not. */
export const approvePlaybook = (intent: string, body: {
  yaml: string; autonomy: Level; by: string; source: PlaybookSource; source_run_id: number | null;
}) => call<PlaybookSummary>(`/playbooks/${encodeURIComponent(intent)}/approve`, body);

/** Within the ceiling (a 422 above it). The library's "Demote to A2" is this with A2. */
export const setAutonomy = (intent: string, level: Level, by: string) =>
  call<PlaybookSummary>(`/playbooks/${encodeURIComponent(intent)}/autonomy`, { level, by });

export const viewLink = (token: string) => call<LinkView>(`/l/${encodeURIComponent(token)}`);

export const revealLink = (token: string) =>
  call<LinkView & { temporaryAccessPass: string }>(`/l/${encodeURIComponent(token)}/reveal`, {});

export const eventsUrl = (id: number, after: number) => `${API}/runs/${id}/events?after=${after}`;
