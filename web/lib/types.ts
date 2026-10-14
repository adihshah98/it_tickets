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

export interface DoneCheck { probe: string; ok: boolean; detail: string }
export interface OutboundMessage { kind: "reply" | "psa_note"; to: string | null; subject: string | null; text: string }
export interface BlockedMessage {
  kind: "reply" | "psa_note"; reason: "secret" | "dod"; hits: { kind: string; start: number }[]; detail?: string;
}
export interface TimeEntry { work_type: string; minutes: number; started_at: string; ended_at: string }

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
  /** MFA reset: whether a credential step needs the subject confirmed out of band first, and the confirmation. */
  oob: { needed: boolean; confirmed: { by: string; contact: string; via: string; at: string } | null };
  outcome: ({ from: RunState; to: RunState; kind?: string; reason?: string } & Record<string, unknown>) | null;
  followups: { id: number; step_id: string; kind: string; run_at: string; status: string }[];
  links: { id: number; step_id: string; to: string; expires_at: string; viewed_at: string | null; url: string | null }[];
  closeout: { checks: DoneCheck[]; messages: OutboundMessage[]; blocked: BlockedMessage[]; time_entry: TimeEntry | null };
  /** "Do it myself" steps (Build-Day20), not in the agent's plan. */
  technician_steps: TechnicianStep[];
  /** "I fixed it in the admin center": the last audit_observed, and when it was recorded. */
  audit: (AuditObserved & { at: string }) | null;
  /** The last playbook drafted from this run, for the review screen. */
  playbook_draft: PlaybookDraft | null;
  can_save_playbook: boolean;
  /** No playbook covered this ticket (null before triage). */
  no_playbook: boolean | null;
  /** The playbook versions the run followed; source is null for a version deleted since (a reset). */
  playbooks_used: PlaybookUsed[];
}

export type PlaybookSource = "shipped" | "agent_run" | "technician_run" | "written" | "audit_log";

export interface PlaybookUsed {
  intent: string;
  version: number;
  source: PlaybookSource | null;
  source_run_id: number | null;
}

export interface TechnicianStep {
  step_id: string;
  action: string;
  /** technician:{email} */
  actor: string;
  decision: Decision;
  rule_id: string;
  reason: string;
  params: Record<string, unknown> | null;
  status: "blocked" | StepStatus;
  output?: Record<string, unknown> | null;
  observed?: Record<string, unknown> | null;
  error?: string;
}

/** A step a run (or the audit logs) showed: backend ObservedStep. */
export interface ObservedStep {
  action: string;
  params: Record<string, unknown>;
  rationale: string | null;
  verified: Record<string, unknown>;
  skipped: string | null;
  actor: string;
}

export interface UnmappedAudit { activity: string; at: string; by: string; reason: string; targets: string[] }

export type AuditObserved =
  | { status: "waiting"; since: string; by: string }
  | { status: "found"; since: string; until: string; mapped: ObservedStep[]; unmapped: UnmappedAudit[]; undone: ObservedStep[] }
  | { status: "timed_out"; since: string; waited_s: number };

/** GET /playbooks: one intent's current version (backend api.playbook_view). */
export interface PlaybookSummary {
  intent: string;
  version: number;
  source: PlaybookSource;
  source_run_id: number | null;
  approved_by: string | null;
  created_at: string;
  autonomy: "A1" | "A2" | "A3";
  ceiling: "A1" | "A2" | "A3";
}

/** GET /playbooks/{intent}: the current YAML and every version, newest last. */
export interface PlaybookView extends PlaybookSummary {
  yaml: string;
  versions: {
    version: number; status: "approved" | "retired"; source: PlaybookSource; source_run_id: number | null;
    approved_by: string | null; created_at: string; yaml: string;
  }[];
}

export interface PlaybookDraft {
  intent: string;
  source: PlaybookSource;
  yaml: string;
  problems: string[];
  ok: boolean;
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
