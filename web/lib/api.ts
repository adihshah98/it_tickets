// Typed calls to the backend (backend/tech/api.py). The browser calls it directly; CORS allows this origin.

import type { CatalogueAction, Observed } from "./console";
import type { LinkView, RunSummary, Snapshot } from "./types";

export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(readonly status: number, readonly detail: string) {
    super(detail);
  }
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
    throw new ApiError(res.status, typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : res.statusText);
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

export const viewLink =(token: string) => call<LinkView>(`/l/${encodeURIComponent(token)}`);

export const revealLink = (token: string) =>
  call<LinkView & { temporaryAccessPass: string }>(`/l/${encodeURIComponent(token)}/reveal`, {});

export const eventsUrl = (id: number, after: number) => `${API}/runs/${id}/events?after=${after}`;
