// The playbook review screen and library (Build-Day21 parts 5-6). Pure, so vitest can test it.

import { technicianName } from "./console";
import type { ObservedStep, PlaybookSource, Snapshot, UnmappedAudit } from "./types";

export type Level = "A1" | "A2" | "A3";
export const LEVELS: readonly Level[] = ["A1", "A2", "A3"];

export const LEVEL_TEXT: Record<Level, { name: string; detail: string }> = {
  A1: { name: "A1 · plan only", detail: "the agent investigates and plans; a technician makes every change" },
  A2: { name: "A2 · ask first", detail: "every run waits for a person to approve its changes" },
  A3: { name: "A3 · hands-free", detail: "reversible changes run without an approval; anything else still asks" },
};

export const SOURCE_TEXT: Record<PlaybookSource, string> = {
  shipped: "shipped", agent_run: "learned from an agent run", technician_run: "learned from a technician's run",
  written: "written by a technician", audit_log: "learned from the audit logs",
};

/** The levels the picker allows: up to the ceiling; none while the draft has problems (ceiling null). */
export function allowedLevels(ceiling: Level | null): Level[] {
  if (!ceiling) return [];
  return LEVELS.slice(0, LEVELS.indexOf(ceiling) + 1) as Level[];
}

/** The level the picker starts at: A2 (Build-Day21 decision 13), or the ceiling if it's lower. */
export function defaultLevel(ceiling: Level | null): Level {
  return ceiling === "A1" ? "A1" : "A2";
}

/** A playbook declares the intent it's saved under (loader.parse_playbook), so renaming one rewrites its
 * `intent:` line too. A YAML without one is left alone: the check says what's missing. */
export function renameIntent(yaml: string, intent: string): string {
  return yaml.replace(/^intent:.*$/m, `intent: ${intent}`);
}

export interface SourceStep {
  action: string;
  params: Record<string, unknown>;
  by: string;
  status: string;
}

/** What a draft was learned from, rebuilt from its run's snapshot (Build-Day21 decision 12): the drafter's
 * input isn't stored, but its parts are on the run. */
export type SourceView =
  | { kind: "run"; ticket: string; steps: SourceStep[]; findings: string[] }
  | { kind: "audit"; ticket: string; learned: ObservedStep[]; undone: ObservedStep[]; cantLearn: UnmappedAudit[] }
  | { kind: "written"; text: string; allowedActions: string[] };

const DONE = new Set(["verified", "skipped"]);

export function sourceView(snap: Snapshot, source: PlaybookSource): SourceView {
  const ticket = snap.ticket.text;
  if (source === "audit_log") {
    const audit = snap.audit?.status === "found" ? snap.audit : null;
    return { kind: "audit", ticket, learned: audit?.mapped ?? [], undone: audit?.undone ?? [],
             cantLearn: audit?.unmapped ?? [] };
  }
  const agent = (snap.steps ?? []).filter((s) => DONE.has(s.status))
    .map((s) => ({ action: s.action, params: s.params, by: "agent", status: s.status }));
  const technician = snap.technician_steps.filter((s) => DONE.has(s.status))
    .map((s) => ({ action: s.action, params: s.params ?? {}, by: technicianName(s.actor), status: s.status }));
  return { kind: "run", ticket, steps: [...agent, ...technician], findings: snap.findings };
}
