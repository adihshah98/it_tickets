"use client";

// The ticket workspace: the run's sections, in the order they happen, each appearing as its events arrive.

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { formatSecs } from "@/lib/run";
import { senderName } from "@/lib/samples";
import { TERMINAL, type Snapshot } from "@/lib/types";
import { useRun } from "@/lib/useRun";
import { ApprovalCard, ApprovalSummary } from "./ApprovalCard";
import { Followups, Links, NeedsTechnician, Outcome } from "./Closeout";
import { Execution } from "./Execution";
import { InvestigationLog } from "./InvestigationLog";
import { PlanSteps } from "./PlanSteps";
import { Understood } from "./Understood";
import { Section, StateBadge } from "./ui";

const BAR_SECS = 180;

function Elapsed({ snap }: { snap: Snapshot }) {
  const done = TERMINAL.has(snap.run.state);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (done) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [done]);
  const end = done ? Date.parse(snap.run.updated_at) : now;
  const secs = (end - Date.parse(snap.run.created_at)) / 1000;
  return (
    <span className={`font-mono text-sm ${secs > BAR_SECS ? "text-amber-700" : "text-neutral-600"}`} title="wall clock, including time waiting for approval">
      {formatSecs(secs)}
    </span>
  );
}

export default function Workspace() {
  const { id } = useParams<{ id: string }>();
  const { snap, view, error, live, refresh, reopen } = useRun(Number(id));

  if (!snap) {
    return <p className="text-sm text-neutral-500">{error ? `Couldn't load run ${id}: ${error}` : "Loading…"}</p>;
  }
  const state = view.state ?? snap.run.state;
  const steps = snap.steps ?? [];
  const deciding = (state === "AWAITING_APPROVAL" || state === "HELD") && snap.plan && !snap.plan.escalate;
  const executed = Object.values(view.steps).some((r) => !r.followup) || steps.some((s) => s.status !== "pending");

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">Run #{snap.run.id}</h1>
        <StateBadge state={state} />
        <span className="text-sm text-neutral-600">from {senderName(snap.ticket.from)}</span>
        <span className="ml-auto flex items-center gap-3 text-xs text-neutral-500">
          {view.investigateSecs !== null && <span>investigate {view.investigateSecs.toFixed(0)} s</span>}
          {view.executeSecs !== null && <span>execute {view.executeSecs.toFixed(0)} s</span>}
          <Elapsed snap={{ ...snap, run: { ...snap.run, state } }} />
          <span title={live ? "streaming" : "not streaming"} className={live ? "text-emerald-600" : "text-neutral-300"}>●</span>
        </span>
      </header>
      {error && <p className="text-sm text-red-700">{error}</p>}

      <Section title="Ticket">
        <p className="whitespace-pre-wrap text-sm">{snap.ticket.text}</p>
      </Section>

      {snap.triage && <Understood triage={snap.triage} approved={snap.requester_approved} />}

      {(view.log.length > 0 || state === "INVESTIGATING") && (
        <InvestigationLog log={view.log} active={state === "INVESTIGATING"} secs={view.investigateSecs} />
      )}

      {snap.plan && <PlanSteps plan={snap.plan} steps={steps} />}

      {deciding && <ApprovalCard snap={snap} onDecided={refresh} />}
      {snap.approval && <ApprovalSummary approval={snap.approval} />}

      {executed && <Execution steps={steps} runs={view.steps} secs={view.executeSecs} />}

      {snap.followups.length > 0 && <Followups snap={snap} runs={view.steps} onStarted={reopen} />}
      {snap.links.length > 0 && <Links links={snap.links} />}
      {snap.plan && snap.plan.needs_technician.length > 0 && <NeedsTechnician items={snap.plan.needs_technician} />}
      {snap.outcome && (state === "ESCALATED" || !deciding) && <Outcome outcome={snap.outcome} />}

      {view.errors.map((e) => (
        <p key={e.seq} className="rounded bg-red-50 p-2 font-mono text-xs text-red-900">{e.where}: {e.error}</p>
      ))}
    </div>
  );
}
