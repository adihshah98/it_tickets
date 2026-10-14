import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { allowedLevels, defaultLevel, renameIntent, sourceView } from "../lib/playbooks";
import type { Snapshot } from "../lib/types";

// GET /runs/21 (Jordan, finished in the console) and /runs/24 (Jordan's title, fixed in the admin center).
const read = (name: string): Snapshot => JSON.parse(readFileSync(join(__dirname, "fixtures", name), "utf8"));
const RUN21 = read("snapshot-run21.json");
const RUN24 = read("snapshot-run24.json");

describe("the autonomy picker", () => {
  it("allows up to the ceiling, and nothing while the draft fails its checks", () => {
    expect(allowedLevels("A3")).toEqual(["A1", "A2", "A3"]);
    expect(allowedLevels("A2")).toEqual(["A1", "A2"]);
    expect(allowedLevels(null)).toEqual([]);
  });

  it("starts at A2", () => {
    expect(defaultLevel("A3")).toBe("A2");
    expect(defaultLevel(null)).toBe("A2");
  });
});

describe("renaming a draft", () => {
  it("rewrites the intent the YAML declares, and nothing else", () => {
    const yaml = "intent: unknown\ngoal: Jordan can open the site\nnever:\n  - intent: is not a step\n";
    expect(renameIntent(yaml, "site.grant_access")).toBe(
      "intent: site.grant_access\ngoal: Jordan can open the site\nnever:\n  - intent: is not a step\n");
  });
});

describe("what a draft was learned from", () => {
  it("shows a console run's verified steps, by the technician", () => {
    const view = sourceView(RUN21, "technician_run");
    expect(view.kind).toBe("run");
    if (view.kind !== "run") return;
    expect(view.ticket).toBe("Please give Jordan Lee access to the Northwind Operations site.");
    expect(view.steps.map((s) => [s.action, s.by, s.status])).toEqual([["group.addMember", "aditya", "verified"]]);
    expect(view.steps[0].params.groupId).toBe("619403f7-e261-4048-892e-4f3ee8125caa");
    expect(view.findings.length).toBeGreaterThan(0);
  });

  it("shows an audit draft's changes, not the rejected plan", () => {
    const view = sourceView(RUN24, "audit_log");
    expect(view.kind).toBe("audit");
    if (view.kind !== "audit") return;
    expect(view.learned.map((s) => [s.action, s.params.jobTitle])).toEqual([["user.update", "Lot Supervisor"]]);
    expect([view.undone, view.cantLearn]).toEqual([[], []]);
  });
});
