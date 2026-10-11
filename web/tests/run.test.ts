import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { answersFor, initialView, reduce, type View } from "../lib/run";
import type { Confirmation, RunEvent } from "../lib/types";

/** Parse a captured `curl -N /runs/{id}/events` body the way EventSource does (id / event / data lines). */
function parseSse(text: string): RunEvent[] {
  const events: RunEvent[] = [];
  let seq = 0;
  for (const block of text.split(/\r?\n\r?\n/)) {
    let type = "message";
    let data: string | null = null;
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("id:")) seq = Number(line.slice(3).trim());
      else if (line.startsWith("event:")) type = line.slice(6).trim();
      else if (line.startsWith("data:")) data = line.slice(5).trim();
    }
    if (data !== null) events.push({ seq, type, data: JSON.parse(data) });
  }
  return events;
}

const replay = (events: RunEvent[], from: View = initialView) => events.reduce(reduce, from);

const fixture = parseSse(readFileSync(join(__dirname, "fixtures/onboard.sse"), "utf8"));

const ev = (seq: number, type: string, data: Record<string, unknown>): RunEvent => ({ seq, type, data });
const executing = replay([ev(1, "state", { from: "AWAITING_APPROVAL", to: "EXECUTING" })]);

describe("replaying a captured onboarding run", () => {
  const view = replay(fixture);

  it("ends DONE with the stream closed", () => {
    expect(view.state).toBe("DONE");
    expect(view.ended).toBe(true);
    expect(view.last).toBe(48);
  });

  it("builds the log in seq order, tool calls and findings interleaved", () => {
    const seqs = view.log.map((i) => i.seq);
    expect(seqs).toEqual([...seqs].sort((a, b) => a - b));
    expect(view.log.filter((i) => i.kind === "tool")).toHaveLength(8);
    expect(view.log.filter((i) => i.kind === "finding")).toHaveLength(4);
    const kinds = view.log.map((i) => i.kind);
    expect(kinds.indexOf("finding")).toBeLessThan(kinds.lastIndexOf("tool"));
    expect(view.investigateSecs).toBeGreaterThan(0);
  });

  it("verifies every plan step once, and marks the run-now step as a follow-up", () => {
    const main = Object.values(view.steps).filter((s) => !s.followup);
    expect(main).toHaveLength(7);
    expect(main.every((s) => s.status === "verified" && s.attempts === 1)).toBe(true);
    expect(view.steps["license_on_start-run"]).toMatchObject({ followup: true, status: "verified" });
    expect(view.executeSecs).toBeGreaterThan(0);
  });

  it("drops events re-delivered after a reconnect", () => {
    expect(replay(fixture.slice(10, 30), view)).toBe(view);
    const half = replay(fixture.slice(0, 30));
    expect(replay(fixture.slice(20), half)).toEqual(view);
  });
});

describe("step attempts", () => {
  const started = (seq: number) => ev(seq, "step_started", { step_id: "add", action: "group.addMember", t: seq });

  it("reads retried when a step starts twice before it verifies", () => {
    const view = replay([started(2), ev(3, "step_failed", { step_id: "add", action: "group.addMember", error: "404" }),
                        started(4), ev(5, "step_verified", { step_id: "add", action: "group.addMember", t: 5 })], executing);
    expect(view.steps.add).toMatchObject({ attempts: 2, status: "verified", error: null, followup: false, t: 5 });
  });

  it("reads skipped when the target state was already true", () => {
    const view = replay([ev(2, "step_verified", { step_id: "add", action: "group.addMember", skipped: "already true" })],
                        executing);
    expect(view.steps.add).toMatchObject({ status: "skipped", skipped: "already true", attempts: 1 });
  });

  it("keeps a failure's error", () => {
    const view = replay([started(2), ev(3, "step_failed", { step_id: "add", action: "group.addMember", error: "verify failed" })],
                        executing);
    expect(view.steps.add).toMatchObject({ status: "failed", error: "verify failed" });
  });
});

it("shows a lint rejection as a retry in the log", () => {
  const view = replay([ev(1, "lint_result", { attempt: 1, ok: false, problems: ["invented group id"], t: 12 })]);
  expect(view.log).toEqual([{ seq: 1, kind: "lint", attempt: 1, ok: false, problems: ["invented group id"], t: 12 }]);
});

it("collects server error events", () => {
  const view = replay([ev(1, "error", { where: "execute", error: "RuntimeError: boom" })]);
  expect(view.errors).toEqual([{ seq: 1, where: "execute", error: "RuntimeError: boom" }]);
});

describe("answersFor", () => {
  const c = (key: Confirmation["key"], proposed: string): Confirmation => ({ key, proposed, question: "?", evidence: "" });
  const confirmations = [c("upn_format", "priya.nair@x.test"), c("license_purchase", "buy 1 SPB"),
                         c("start_date", "2026-10-12; license assigned at 11:00 UTC"), c("other", "something")];

  it("sends nothing when the approver accepts the proposals", () => {
    expect(answersFor(confirmations, {})).toEqual({});
    expect(answersFor(confirmations, { upn_format: " priya.nair@x.test " })).toEqual({});
  });

  it("sends changed fields the API accepts, trimmed, and drops emptied ones", () => {
    expect(answersFor(confirmations, { upn_format: "  pnair ", start_date: " ", license_purchase: "no", other: "x" }))
      .toEqual({ upn_format: "pnair" });
    expect(answersFor(confirmations, { start_date: "2026-10-13" })).toEqual({ start_date: "2026-10-13" });
  });
});
