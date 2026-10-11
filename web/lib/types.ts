// Shapes served by the API. They mirror `snapshot()` in backend/tech/api.py and the models in
// backend/tech/core/models.py, by hand: the snapshot is a dict on the Python side, so OpenAPI can't help.

export type RunState =
  | "RECEIVED" | "TRIAGED" | "INVESTIGATING" | "PLAN_PROPOSED" | "PLAN_CHECKED" | "AWAITING_APPROVAL"
  | "EXECUTING" | "REPLANNING" | "CLOSING" | "DONE" | "ESCALATED" | "HELD";

export const TERMINAL: ReadonlySet<RunState> = new Set(["DONE", "ESCALATED"]);

export type Decision = "auto" | "approve" | "block";

export interface Triage {
  intents: string[];
  entities: {
    new_user: { display_name: string; department: string | null; job_title: string | null; start_date: string | null } | null;
    peer_user_hint: string | null;
    subject_user_hint: string | null;
    apps: string[];
  };
  requester: { name: string; email: string };
  out_of_scope_asks: string[];
  missing_info: string[];
}

export interface Confirmation {
  key: "upn_format" | "license_purchase" | "start_date" | "other";
  question: string;
  proposed: string;
  evidence: string;
}

export interface Plan {
  intent: string;
  summary: string;
  assumptions: string[];
  needs_technician: { item: string; reason: string }[];
  confirm_on_approval: Confirmation[];
  questions_for_requester: string[];
  out_of_scope_requests: string[];
  escalate: boolean;
  escalation_reason: string | null;
}

export type StepStatus = "pending" | "running" | "verified" | "skipped" | "failed";

export interface Step {
  id: string;
  action: string;
  params: Record<string, unknown>;
  rationale: string;
  depends_on: string[];
  expected: Record<string, unknown>;
  risk_class: string;
  decision: Decision | null;
  rule_id: string | null;
  reason: string | null;
  status: StepStatus;
  output?: Record<string, unknown> | null;
  observed?: Record<string, unknown> | null;
  error?: string;
}

export interface Snapshot {
  run: { id: number; state: RunState; created_at: string; updated_at: string };
  ticket: { id: number; text: string; from: string };
  last_seq: number;
  triage: Triage | null;
  requester_approved: boolean | null;
  findings: string[];
  plan: Plan | null;
  steps: Step[] | null;
  approval: { by: string; decision: "approve" | "reject"; answers: Record<string, string>; released_hold: boolean } | null;
  outcome: ({ from: RunState; to: RunState; kind?: string; reason?: string } & Record<string, unknown>) | null;
  followups: { id: number; step_id: string; kind: string; run_at: string; status: string }[];
  links: { id: number; step_id: string; to: string; expires_at: string; viewed_at: string | null; url: string | null }[];
}

export interface RunSummary {
  id: number;
  state: RunState;
  from: string;
  subject: string;
  created_at: string;
  updated_at: string;
}

export interface LinkView {
  to: string;
  userPrincipalName: string | null;
  expiresAt: string;
  viewed: boolean;
  expired: boolean;
  includes: string[];
  note: string | null;
}

/** One event off the stream: `id:` is the seq, `event:` the type, `data:` the payload. */
export interface RunEvent {
  seq: number;
  type: string;
  data: Record<string, unknown>;
}
