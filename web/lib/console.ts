// "Do it myself" (Build-Day21 decisions 4 and 7): a catalogue action's param form, built from its JSON schema
// (GET /catalogue), with ID fields picked from what the run's reads saw (GET /runs/{id}/observed). Pure, so
// vitest can test it; components/ManualStepPanel.tsx renders it. The backend validates and gates everything
// again: this only keeps the form honest.

export interface PropSchema {
  type?: string;
  anyOf?: PropSchema[];
  enum?: string[];
  items?: PropSchema;
  description?: string;
  default?: unknown;
  format?: string;
}

export interface CatalogueAction {
  name: string;
  risk_class: string;
  verify: string;
  compensate: string | null;
  params: { properties: Record<string, PropSchema>; required?: string[] };
  /** Offered in the console; the gate always blocks the others for a technician. */
  console: boolean;
}

/** One entry of GET /runs/{id}/observed: a directory object, or a file with its versions. */
export type Observed =
  | { id: string; displayName: string; userPrincipalName?: string; kind?: string; roleAssignable?: boolean;
      skuPartNumber?: string }
  | { driveId: string; itemId: string; versions: string[] | null; name: string | null; site: string | null;
      versionDetails: { id: string; time: string | null; size: number | null }[] };

export type IdKind = "user" | "group" | "sku";

export type Field =
  | { kind: "id"; name: string; label: string; required: boolean; description?: string; idKind: IdKind }
  /** driveId + itemId as one picker, value `${driveId}|${itemId}` (SharePoint IDs never contain "|"). */
  | { kind: "file"; name: "file"; label: string; required: boolean; description?: string }
  | { kind: "version"; name: string; label: string; required: boolean; description?: string }
  | { kind: "bool"; name: string; label: string; required: boolean; description?: string }
  | { kind: "enum"; name: string; label: string; required: boolean; description?: string; options: string[] }
  | { kind: "list"; name: string; label: string; required: boolean; description?: string }
  | { kind: "text"; name: string; label: string; required: boolean; description?: string };

const ID_KINDS: Record<string, IdKind> = { userId: "user", groupId: "group", skuId: "sku" };

/** Names the console fills in itself: the backend names the group from the reads (decision 5), and the file's
 * name comes from the file picked. */
export const AUTO_FILLED = new Set(["groupName", "fileName"]);

/** "jobTitle" -> "Job title"; ID fields read as the thing picked ("groupId" -> "Group"). */
export function label(name: string): string {
  const words = name.replace(/Id$/, "").replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
  return words === "sku" ? "Licence (SKU)" : words.charAt(0).toUpperCase() + words.slice(1);
}

/** `anyOf: [X, null]` (an optional pydantic field) -> X. */
function base(schema: PropSchema): PropSchema {
  return schema.anyOf?.find((s) => s.type !== "null") ?? schema;
}

export function fields(action: CatalogueAction): Field[] {
  const { properties, required = [] } = action.params;
  const out: Field[] = [];
  for (const [name, raw] of Object.entries(properties)) {
    if (AUTO_FILLED.has(name)) continue;
    const schema = base(raw);
    const common = { name, label: label(name), required: required.includes(name), description: raw.description };
    if (name === "driveId" && "itemId" in properties) {
      out.push({ ...common, kind: "file", name: "file", label: "File" });
    } else if (name === "itemId" && "driveId" in properties) {
      continue; // part of the file picker
    } else if (name === "versionId") {
      out.push({ ...common, kind: "version", label: "Version to restore" });
    } else if (ID_KINDS[name]) {
      out.push({ ...common, kind: "id", idKind: ID_KINDS[name] });
    } else if (schema.type === "boolean") {
      out.push({ ...common, kind: "bool" });
    } else if (schema.enum) {
      out.push({ ...common, kind: "enum", options: schema.enum });
    } else if (schema.type === "array") {
      out.push({ ...common, kind: "list" });
    } else {
      out.push({ ...common, kind: "text" });
    }
  }
  return out;
}

export interface Option { value: string; label: string }

const isFile = (o: Observed): o is Extract<Observed, { driveId: string }> => "driveId" in o;
const isObject = (o: Observed): o is Extract<Observed, { id: string }> => "id" in o;

export const fileValue = (driveId: string, itemId: string) => `${driveId}|${itemId}`;

function kb(size: number | null): string {
  return size === null ? "" : `${(size / 1000).toFixed(1)} KB`;
}

/** The picker's choices for an ID, file or version field. A version list needs the file picked first; its
 * newest version is the current one, so it's marked (restoring it changes nothing). */
export function options(field: Field, observed: Observed[], values: Record<string, string>): Option[] {
  const objects = observed.filter(isObject);
  const files = observed.filter(isFile);
  if (field.kind === "id") {
    const pick = {
      user: (o: (typeof objects)[number]) => o.userPrincipalName !== undefined,
      group: (o: (typeof objects)[number]) => o.kind !== undefined,
      sku: (o: (typeof objects)[number]) => o.skuPartNumber !== undefined,
    }[field.idKind];
    return objects.filter(pick).map((o) => ({
      value: o.id,
      label: field.idKind === "user" ? `${o.displayName} (${o.userPrincipalName})`
        : field.idKind === "group" ? `${o.displayName} (${o.kind}${o.roleAssignable ? ", role-assignable" : ""})`
        : `${o.skuPartNumber}`,
    })).sort((a, b) => a.label.localeCompare(b.label));
  }
  if (field.kind === "file") {
    return files.map((f) => ({
      value: fileValue(f.driveId, f.itemId),
      label: [f.name ?? f.itemId, f.site].filter(Boolean).join(" · "),
    }));
  }
  if (field.kind === "version") {
    const file = files.find((f) => fileValue(f.driveId, f.itemId) === values.file);
    if (!file) return [];
    const details = file.versionDetails.length ? file.versionDetails
      : (file.versions ?? []).map((id) => ({ id, time: null, size: null }));
    return details.map((v, i) => ({
      value: v.id,
      label: [v.id, v.time && new Date(v.time).toLocaleString(), kb(v.size), i === 0 && "current"]
        .filter(Boolean).join(" · "),
    }));
  }
  return [];
}

/** The form's values as the action's params, and the required fields still empty. Empty optional fields are
 * left out, so the backend applies its defaults. */
export function toParams(fs: Field[], values: Record<string, string>,
                         observed: Observed[] = []): { params: Record<string, unknown>; missing: string[] } {
  const params: Record<string, unknown> = {};
  const missing: string[] = [];
  for (const f of fs) {
    const value = (values[f.name] ?? "").trim();
    if (!value) {
      if (f.required) missing.push(f.label);
      continue;
    }
    switch (f.kind) {
      case "file": {
        const [driveId, itemId] = value.split("|");
        params.driveId = driveId;
        params.itemId = itemId;
        const file = observed.filter(isFile).find((o) => o.driveId === driveId && o.itemId === itemId);
        if (file?.name) params.fileName = file.name;
        break;
      }
      case "bool":
        params[f.name] = value === "true";
        break;
      case "list":
        params[f.name] = value.split(",").map((s) => s.trim()).filter(Boolean);
        break;
      default:
        params[f.name] = value;
    }
  }
  return { params, missing };
}

/** "technician:aditya@contoso.com" -> "aditya". */
export function technicianName(actor: string): string {
  return actor.replace(/^technician:/, "").split("@")[0];
}
