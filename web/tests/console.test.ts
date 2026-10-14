import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  type CatalogueAction, fields, fileValue, label, type Observed, options, technicianName, toParams,
} from "../lib/console";

// GET /catalogue and GET /runs/20/observed (Sophie's restore, data/tech.db), as the backend serves them.
const read = (name: string) => JSON.parse(readFileSync(join(__dirname, "fixtures", name), "utf8"));
const CATALOGUE: CatalogueAction[] = read("catalogue.json");
const RUN20: Observed[] = read("observed-run20.json");
const action = (name: string) => CATALOGUE.find((a) => a.name === name)!;

const NW_OPS = "619403f7-e261-4048-892e-4f3ee8125caa";
const SOPHIE = "eabb4284-1ebd-4688-9334-fe6a2bf58881";

describe("fields from the catalogue's schemas", () => {
  it("makes a group add two ID pickers; the backend names the group itself", () => {
    expect(fields(action("group.addMember")).map((f) => [f.kind, f.name, f.label, f.required])).toEqual([
      ["id", "groupId", "Group", true], ["id", "userId", "User", true]]);
  });

  it("makes a file restore one file picker and a version picker", () => {
    expect(fields(action("file.restoreVersion")).map((f) => [f.kind, f.name, f.required])).toEqual([
      ["file", "file", true], ["version", "versionId", true]]);
  });

  it("reads optional fields through anyOf-with-null", () => {
    const update = Object.fromEntries(fields(action("user.update")).map((f) => [f.name, [f.kind, f.required]]));
    expect(update).toEqual({
      userId: ["id", true], usageLocation: ["text", false], accountEnabled: ["bool", false],
      department: ["text", false], jobTitle: ["text", false], displayName: ["text", false],
    });
  });

  it("labels fields for people", () => {
    expect([label("jobTitle"), label("userPrincipalName"), label("skuId"), label("userId")]).toEqual([
      "Job title", "User principal name", "Licence (SKU)", "User"]);
  });

  it("offers only console actions to the picker", () => {
    const offered = CATALOGUE.filter((a) => a.console).map((a) => a.name);
    expect(offered).toContain("group.addMember");
    expect(offered).not.toContain("tap.create");
    expect(offered).not.toContain("link.sendOneTime");
  });
});

describe("options from what the run's reads saw", () => {
  const add = fields(action("group.addMember"));
  const [group, user] = add;

  it("splits users from groups", () => {
    expect(options(group, RUN20, {}).map((o) => o.label)).toEqual([
      "Accounting (m365)", "Northwind Operations (m365)", "Northwind Parking (m365)"]);
    expect(options(user, RUN20, {})).toEqual([
      { value: SOPHIE, label: "Sophie Baker (sbaker@NorthwindParking.onmicrosoft.com)" }]);
  });

  it("lists a file's versions once the file is picked, newest marked current", () => {
    const [file, version] = fields(action("file.restoreVersion"));
    const [rates] = options(file, RUN20, {});
    expect(rates.label).toBe("Q3 Parking Rates.xlsx · Northwind Operations");
    expect(options(version, RUN20, {})).toEqual([]);
    const versions = options(version, RUN20, { file: rates.value });
    expect(versions.map((v) => v.value)).toEqual(["3.0", "2.0", "1.0"]);
    expect(versions[0].label).toMatch(/· 12\.6 KB · current$/);
    expect(versions[1].label).not.toMatch(/current/);
  });
});

describe("params from the form", () => {
  it("sends the picked IDs, and nothing for empty optional fields", () => {
    const add = fields(action("group.addMember"));
    expect(toParams(add, { groupId: NW_OPS, userId: SOPHIE })).toEqual({
      params: { groupId: NW_OPS, userId: SOPHIE }, missing: []});
    expect(toParams(add, { groupId: NW_OPS, userId: "  " }).missing).toEqual(["User"]);
  });

  it("splits the file back into drive and item, with its name for the approval card", () => {
    const restore = fields(action("file.restoreVersion"));
    const file = RUN20.find((o) => "driveId" in o) as Extract<Observed, { driveId: string }>;
    const { params, missing } = toParams(restore, { file: fileValue(file.driveId, file.itemId), versionId: "2.0" },
                                         RUN20);
    expect(missing).toEqual([]);
    expect(params).toEqual({ driveId: file.driveId, itemId: file.itemId, fileName: "Q3 Parking Rates.xlsx",
                             versionId: "2.0" });
  });

  it("reads yes/no as booleans, leaves an unset one out, and splits lists", () => {
    const update = fields(action("user.update"));
    expect(toParams(update, { userId: SOPHIE, accountEnabled: "false", jobTitle: "Lot Supervisor" }).params)
      .toEqual({ userId: SOPHIE, accountEnabled: false, jobTitle: "Lot Supervisor" });
    expect(toParams(update, { userId: SOPHIE, accountEnabled: "" }).params).toEqual({ userId: SOPHIE });
    const list = [{ kind: "list", name: "tags", label: "Tags", required: false }] as const;
    expect(toParams([...list], { tags: "a, b ,, c" }).params).toEqual({ tags: ["a", "b", "c"] });
  });

  it("names the technician from the actor", () => {
    expect(technicianName("technician:aditya@northwindparking.onmicrosoft.com")).toBe("aditya");
  });
});
