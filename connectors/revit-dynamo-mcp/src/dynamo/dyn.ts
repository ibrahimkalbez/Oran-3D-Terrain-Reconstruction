import fs from "node:fs/promises";
import path from "node:path";

/**
 * Dynamo graphs (.dyn, JSON since Dynamo 2.0). The inputs Dynamo Player shows are the nodes
 * marked "Is Input" (top-level "Inputs" array); their current values and ranges are on the
 * matching entry of "Nodes" (InputValue, MinimumValue, MaximumValue, StepValue, NumberType).
 * Names live in View.NodeViews. Outputs are the nodes marked "Is Output".
 */
export interface DynInput {
  id: string;
  name: string;
  /** slider | number | toggle | text | value_list | path | selection | other */
  kind: string;
  value?: unknown;
  min?: number;
  max?: number;
  step?: number;
  decimals?: number;
  step_type?: string;
  items?: Array<{ name: string }>;
  node_type?: string;
  description?: string;
}

export interface DynOutput {
  id: string;
  name: string;
  type?: string;
  description?: string;
}

export interface DynGraph {
  path: string;
  name: string;
  uuid?: string;
  description?: string;
  dynamo_version?: string;
  inputs: DynInput[];
  outputs: DynOutput[];
  node_count: number;
  /** Packages the graph depends on (must be installed for Dynamo to run it). */
  packages: Array<{ name: string; version?: string }>;
}

/** Dynamo writes GUIDs as 32 hex digits; some tools add dashes. */
export function normId(id: string): string {
  return String(id ?? "").replace(/[{}-]/g, "").toLowerCase();
}

function decimalsOf(step: number | undefined): number | undefined {
  if (step === undefined || !Number.isFinite(step) || step <= 0) return undefined;
  if (step >= 1) return 0;
  return Math.min(6, Math.max(0, Math.ceil(-Math.log10(step) - 1e-9)));
}

function kindOf(node: any, input: any): string {
  const concrete = String(node?.ConcreteType ?? "");
  const nodeType = String(node?.NodeType ?? "");
  const type = String(input?.Type ?? input?.Type2 ?? "").toLowerCase();
  if (/Slider/i.test(concrete)) return "slider";
  if (nodeType === "BooleanInputNode" || /BoolSelector/i.test(concrete) || type === "boolean") return "toggle";
  if (/Filename|Directory/i.test(concrete)) return "path";
  if (nodeType === "NumberInputNode" || type === "number") return "number";
  if (nodeType === "StringInputNode" || type === "string") return "text";
  if (type === "selection" || /Select/i.test(concrete)) return "selection";
  if (type === "dropdownselection" || nodeType === "ExtensionNode" || node?.SelectedIndex !== undefined) return "value_list";
  return "other";
}

function valueOf(kind: string, node: any, input: any): unknown {
  const raw = node?.InputValue ?? input?.Value;
  switch (kind) {
    case "slider":
    case "number": {
      const n = typeof raw === "number" ? raw : Number(String(raw ?? "").replace(",", "."));
      return Number.isFinite(n) ? n : raw;
    }
    case "toggle":
      return typeof raw === "boolean" ? raw : String(raw).toLowerCase() === "true";
    case "value_list":
      return node?.SelectedString ?? input?.Value ?? node?.SelectedIndex;
    default:
      return raw;
  }
}

export function parseDyn(text: string, file = ""): DynGraph {
  let json: any;
  try {
    json = JSON.parse(text.replace(/^﻿/, ""));
  } catch {
    throw new Error(`${path.basename(file) || "graph"} is not a Dynamo 2.x+ graph (JSON). Open and re-save it in Dynamo 2 or later.`);
  }
  const nodes: any[] = Array.isArray(json.Nodes) ? json.Nodes : [];
  const views: any[] = Array.isArray(json.View?.NodeViews) ? json.View.NodeViews : [];
  const nodeById = new Map(nodes.map((n) => [normId(n.Id), n]));
  const viewById = new Map(views.map((v) => [normId(v.Id), v]));

  const declared: any[] = Array.isArray(json.Inputs) ? json.Inputs : [];
  // Older files may only flag inputs on the node views.
  const inputIds = declared.length > 0 ? declared.map((i) => normId(i.Id)) : views.filter((v) => v.IsSetAsInput).map((v) => normId(v.Id));
  const inputs: DynInput[] = inputIds.map((id) => {
    const decl = declared.find((d) => normId(d.Id) === id);
    const node = nodeById.get(id);
    const view = viewById.get(id);
    const kind = kindOf(node, decl);
    const min = node?.MinimumValue ?? decl?.MinimumValue;
    const max = node?.MaximumValue ?? decl?.MaximumValue;
    const step = node?.StepValue ?? decl?.StepValue;
    const integer = String(node?.NumberType ?? decl?.NumberType ?? "").toLowerCase() === "integer" || /IntegerSlider/i.test(String(node?.ConcreteType ?? ""));
    const input: DynInput = {
      id,
      name: String(decl?.Name ?? view?.Name ?? id),
      kind,
      value: valueOf(kind, node, decl),
      node_type: String(node?.ConcreteType ?? "").split(",")[0] || undefined,
    };
    if (kind === "slider") {
      if (typeof min === "number") input.min = min;
      if (typeof max === "number") input.max = max;
      if (typeof step === "number") input.step = step;
      input.step_type = integer ? "integer" : "float";
      input.decimals = integer ? 0 : decimalsOf(step) ?? 2;
    }
    if (kind === "number" && integer) input.step_type = "integer";
    if (kind === "value_list") {
      const choices = decl?.Choices ?? node?.Choices;
      if (Array.isArray(choices)) input.items = choices.map((c: any) => ({ name: String(c?.Name ?? c) }));
    }
    if (decl?.Description) input.description = String(decl.Description);
    return input;
  });

  const declaredOut: any[] = Array.isArray(json.Outputs) ? json.Outputs : [];
  const outputIds = declaredOut.length > 0 ? declaredOut.map((o) => normId(o.Id)) : views.filter((v) => v.IsSetAsOutput).map((v) => normId(v.Id));
  const outputs: DynOutput[] = outputIds.map((id) => {
    const decl = declaredOut.find((d) => normId(d.Id) === id);
    const view = viewById.get(id);
    return { id, name: String(decl?.Name ?? view?.Name ?? id), type: decl?.Type, description: decl?.Description };
  });

  const packages: DynGraph["packages"] = (Array.isArray(json.NodeLibraryDependencies) ? json.NodeLibraryDependencies : [])
    .filter((d: any) => d?.ReferenceType === "Package")
    .map((d: any) => ({ name: String(d.Name), version: d.Version }));

  return {
    path: file,
    name: String(json.Name ?? path.basename(file, ".dyn")),
    uuid: json.Uuid,
    description: json.Description || undefined,
    dynamo_version: json.View?.Dynamo?.Version,
    inputs,
    outputs,
    node_count: nodes.length,
    packages,
  };
}

export async function readDyn(file: string): Promise<DynGraph> {
  return parseDyn(await fs.readFile(file, "utf8"), file);
}

/** Text of the graph with new input values (node InputValue and the Inputs list), e.g. for a variant copy. */
export function writeDynInputs(text: string, values: Record<string, unknown>): string {
  const json = JSON.parse(text.replace(/^﻿/, ""));
  const byId = new Map(Object.entries(values).map(([k, v]) => [normId(k), v]));
  for (const node of Array.isArray(json.Nodes) ? json.Nodes : []) {
    const id = normId(node.Id);
    if (!byId.has(id)) continue;
    const v = byId.get(id);
    if ("InputValue" in node || node.NodeType === "NumberInputNode" || node.NodeType === "BooleanInputNode" || node.NodeType === "StringInputNode") {
      node.InputValue = v;
      if (typeof v === "number" && typeof node.MaximumValue === "number" && v > node.MaximumValue) node.MaximumValue = v;
      if (typeof v === "number" && typeof node.MinimumValue === "number" && v < node.MinimumValue) node.MinimumValue = v;
    } else if ("SelectedString" in node) {
      node.SelectedString = v;
    }
  }
  for (const input of Array.isArray(json.Inputs) ? json.Inputs : []) {
    const id = normId(input.Id);
    if (byId.has(id)) input.Value = typeof byId.get(id) === "string" ? byId.get(id) : JSON.stringify(byId.get(id));
  }
  return JSON.stringify(json, null, 2);
}

/** Finds an input by id or name (case and punctuation ignored). */
export function findInput(graph: DynGraph, key: string): DynInput | undefined {
  const norm = (s: string) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]/g, "");
  const id = normId(key);
  return graph.inputs.find((i) => i.id === id) ?? graph.inputs.find((i) => i.name === key) ?? graph.inputs.find((i) => norm(i.name) === norm(key));
}

/** .dyn files under the folders (recursive, sorted by path). */
export async function listGraphs(folders: string[], maxDepth = 4, limit = 500): Promise<string[]> {
  const found: string[] = [];
  const walk = async (dir: string, depth: number): Promise<void> => {
    if (found.length >= limit || depth > maxDepth) return;
    let entries: import("node:fs").Dirent[];
    try {
      entries = await fs.readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      const full = path.join(dir, e.name);
      if (e.isDirectory() && !e.name.startsWith(".") && e.name !== "backup") await walk(full, depth + 1);
      else if (e.isFile() && e.name.toLowerCase().endsWith(".dyn") && !e.name.startsWith("~mcp")) found.push(full);
      if (found.length >= limit) return;
    }
  };
  for (const f of folders) await walk(f, 0);
  return [...new Set(found)].sort();
}

/** Resolves a graph reference: a path, or a file name / graph name searched in the folders. */
export async function resolveGraph(ref: string, folders: string[]): Promise<string> {
  const direct = path.resolve(ref);
  try {
    await fs.access(direct);
    return direct;
  } catch {
    // search below
  }
  const want = ref.toLowerCase().replace(/\.dyn$/, "");
  const all = await listGraphs(folders);
  const hit = all.find((f) => path.basename(f, ".dyn").toLowerCase() === want) ?? all.find((f) => path.basename(f, ".dyn").toLowerCase().includes(want));
  if (!hit) {
    throw new Error(
      `Dynamo graph '${ref}' not found. Give its full path, or put it in one of: ${folders.join("; ") || "(no graph folder configured)"}.` +
        (all.length ? ` Available: ${all.slice(0, 20).map((f) => path.basename(f)).join(", ")}` : ""),
    );
  }
  return hit;
}
