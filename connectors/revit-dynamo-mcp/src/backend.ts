import fs from "node:fs/promises";
import path from "node:path";
import { BridgeError, RpcCodes, type BridgeClient } from "../../rhino-grasshopper-mcp/src/bridge/client.js";
import type { DesignBackend, DesignInput, GeometrySource, ParameterChangeInput, SolveSummary } from "../../rhino-grasshopper-mcp/src/variants/backend.js";
import { findInput, readDyn, resolveGraph, writeDynInputs, type DynGraph } from "./dynamo/dyn.js";
import type { RevitConfig } from "./settings.js";

/**
 * Design backend for Revit: a design is the Revit model driven by
 *   - the inputs of a Dynamo graph (run like Dynamo Player: no UI, element bindings kept, so
 *     each run updates the same elements instead of adding new ones), and/or
 *   - the project's global parameters.
 * "definition" = path or name of a .dyn graph, or "globals" for global parameters only.
 * Metrics = numeric outputs of the graph + model quantities (revit.metrics) + the quantities of
 * the elements the graph outputs ("design.*").
 */
export interface RevitDesign extends DesignBackend {
  /** Last dynamo.run answer for a graph (outputs, errors, warnings). */
  lastRun(graph: string): any | undefined;
  /** Resolves a definition to a graph path (undefined = global parameters only). */
  graphOf(definition?: string): Promise<string | undefined>;
  /** Current input values of a graph, as tracked by the connector. */
  values(graph: string): Record<string, unknown>;
  /** Makes the next run of the graph re-read the .dyn from disk (after it was edited). */
  markReload(graph: string): void;
}

const GLOBALS = new Set(["globals", "global", "global_parameters", "parametres_globaux", "paramètres globaux"]);

export function numberValue(v: unknown): number | undefined {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v.replace(",", ".")))) return Number(v.replace(",", "."));
  return undefined;
}

/** New absolute value of an input for set / add / multiply / percent / toggle. */
export function combine(input: DesignInput, change: ParameterChangeInput): { value: unknown; note?: string } {
  const mode = (change.mode ?? "set").toLowerCase();
  if (input.kind === "toggle") {
    if (mode === "toggle") return { value: !(input.value === true || String(input.value).toLowerCase() === "true") };
    const v = change.value;
    return { value: typeof v === "boolean" ? v : ["true", "1", "yes", "oui"].includes(String(v).toLowerCase()) };
  }
  if (input.kind === "slider" || input.kind === "number") {
    const current = numberValue(input.value) ?? 0;
    const amount = numberValue(change.value);
    if (amount === undefined) throw new Error(`'${input.name}' needs a number (got ${JSON.stringify(change.value)}).`);
    let next: number;
    switch (mode) {
      case "set": next = amount; break;
      case "add": next = current + amount; break;
      case "multiply": next = current * amount; break;
      case "percent": next = current * (1 + amount / 100); break;
      default: throw new Error(`Unknown mode '${mode}' (set, add, multiply, percent, toggle).`);
    }
    const decimals = typeof input.decimals === "number" ? input.decimals : undefined;
    if (input.step_type === "integer") next = Math.round(next);
    else if (decimals !== undefined) next = Math.round(next * 10 ** decimals) / 10 ** decimals;
    let note: string | undefined;
    if ((input.min !== undefined && next < input.min) || (input.max !== undefined && next > input.max)) {
      const policy = change.on_out_of_range ?? "extend";
      if (policy === "error") throw new Error(`${input.name} = ${next} is outside [${input.min}, ${input.max}].`);
      if (policy === "clamp") {
        next = Math.min(input.max ?? next, Math.max(input.min ?? next, next));
        note = "clamped to the slider range";
      } else note = "outside the slider range: Dynamo extends the range";
    }
    return { value: next, note };
  }
  if (mode !== "set") throw new Error(`'${input.name}' (${input.kind}) only accepts mode 'set'.`);
  return { value: change.value };
}

/** Revit element ids found in Dynamo output values ({element_id: "…"} anywhere in the tree). */
export function elementIds(value: unknown, out: Set<string> = new Set()): Set<string> {
  if (Array.isArray(value)) for (const v of value) elementIds(v, out);
  else if (value && typeof value === "object") {
    const o = value as Record<string, unknown>;
    if (o.element_id !== undefined && o.element_id !== null) out.add(String(o.element_id));
    else for (const v of Object.values(o)) elementIds(v, out);
  }
  return out;
}

/** Metric-friendly name: "Generic Models" → "Generic_Models". */
const key = (s: string) => s.replace(/[^\p{L}\p{N}_.]+/gu, "_").replace(/^_+|_+$/g, "");

export function outputMetrics(outputs: any[]): Record<string, number> {
  const metrics: Record<string, number> = {};
  for (const o of outputs ?? []) {
    const name = key(String(o.name ?? o.id));
    const v = o.value;
    const n = numberValue(v);
    if (n !== undefined) metrics[name] = n;
    else if (Array.isArray(v)) {
      const nums = v.map(numberValue).filter((x): x is number => x !== undefined);
      if (nums.length > 0) {
        metrics[`${name}.sum`] = nums.reduce((a, b) => a + b, 0);
        metrics[`${name}.count`] = nums.length;
        metrics[`${name}.max`] = Math.max(...nums);
      } else if (v.length > 0) metrics[`${name}.count`] = v.length;
    } else if (typeof v === "boolean") metrics[name] = v ? 1 : 0;
  }
  return metrics;
}

export function modelMetrics(m: any, prefix = "revit"): Record<string, number> {
  const out: Record<string, number> = {};
  if (!m) return out;
  const t = m.totals ?? {};
  out[`${prefix}.elements`] = t.count ?? 0;
  out[`${prefix}.volume_m3`] = t.volume_m3 ?? 0;
  if (t.height_m !== undefined) out[`${prefix}.height_m`] = t.height_m;
  if (t.max_z !== undefined) out[`${prefix}.max_z`] = t.max_z;
  if (m.floor_area) out[`${prefix}.floor_area_m2`] = m.floor_area.total_m2 ?? 0;
  if (m.mass_floor_area_m2 !== undefined) out[`${prefix}.mass_floor_area_m2`] = m.mass_floor_area_m2;
  if (m.rooms) out[`${prefix}.rooms_area_m2`] = m.rooms.area_m2 ?? 0;
  for (const g of m.groups ?? []) {
    const k = `${prefix}.${key(String(g.key))}`;
    out[`${k}.count`] = g.count;
    if (g.area_m2) out[`${k}.area_m2`] = g.area_m2;
    if (g.volume_m3) out[`${k}.volume_m3`] = g.volume_m3;
  }
  return out;
}

export function revitBackend(bridge: BridgeClient, config: RevitConfig): RevitDesign {
  const long = () => ({ timeoutMs: config.longTimeoutMs });
  const tracked = new Map<string, Record<string, unknown>>();
  const runs = new Map<string, any>();
  const reloads = new Set<string>();
  let lastGraph: string | undefined;

  const graphOf = async (definition?: string): Promise<string | undefined> => {
    if (definition === undefined || definition.trim() === "") return lastGraph;
    if (GLOBALS.has(definition.trim().toLowerCase())) return undefined;
    return resolveGraph(definition, config.graphFolders);
  };

  const globals = async (): Promise<DesignInput[]> => {
    try {
      const res = await bridge.call("revit.get_global_parameters", {});
      return (res.parameters ?? []).map((g: any): DesignInput => ({
        id: `global:${g.name}`,
        name: g.name,
        kind: typeof g.value === "boolean" ? "toggle" : typeof g.value === "number" ? "number" : "text",
        value: g.value,
        source: "global_parameter",
        ...(g.unit ? { unit: g.unit } : {}),
        ...(g.formula ? { formula: g.formula, read_only: true } : {}),
      }));
    } catch (err) {
      if (err instanceof BridgeError && err.kind === "rpc") return [];
      throw err;
    }
  };

  const dynInputs = (graph: DynGraph): DesignInput[] => {
    const values = tracked.get(graph.path) ?? {};
    return graph.inputs.map((i) => ({ ...i, value: i.id in values ? values[i.id] : i.value, source: "dynamo" }));
  };

  const inputsOf = async (definition?: string) => {
    const graphPath = await graphOf(definition);
    const graph = graphPath ? await readDyn(graphPath) : undefined;
    const inputs = [...(graph ? dynInputs(graph) : []), ...(await globals())];
    const def = graph ? { name: graph.name || path.basename(graph.path, ".dyn"), path: graph.path } : { name: "Global parameters", path: null };
    return { graph, inputs, definition: def };
  };

  const pick = (inputs: DesignInput[], name: string, graph?: DynGraph): DesignInput => {
    const norm = (s: string) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]/g, "");
    if (name.toLowerCase().startsWith("global:")) {
      const g = inputs.find((i) => i.source === "global_parameter" && norm(i.name) === norm(name.slice(7)));
      if (g) return g;
    }
    const fromGraph = graph ? findInput(graph, name) : undefined;
    const hit = (fromGraph && inputs.find((i) => i.source === "dynamo" && i.id === fromGraph.id)) ??
      inputs.find((i) => i.name === name) ??
      inputs.find((i) => norm(i.name) === norm(name));
    if (!hit) {
      throw new BridgeError(`No input named '${name}'.`, "rpc", RpcCodes.NotFound, {
        dynamo_inputs: inputs.filter((i) => i.source === "dynamo").map((i) => i.name),
        global_parameters: inputs.filter((i) => i.source === "global_parameter").map((i) => i.name),
      });
    }
    return hit;
  };

  const run = async (graphPath: string, values: Record<string, unknown>) => {
    const reload = reloads.delete(graphPath);
    const res = await bridge.call("dynamo.run", { path: graphPath, inputs: values, max_items: 50, reload }, long());
    tracked.set(graphPath, values);
    runs.set(graphPath, res);
    lastGraph = graphPath;
    return res;
  };

  const summary = (res: any): SolveSummary | undefined =>
    res ? { duration_ms: Math.round((res.duration_s ?? 0) * 1000), errors: res.errors ?? [], warnings: res.warnings ?? [] } : undefined;

  const designIds = (graphPath: string | undefined, outputs?: string[]): string[] => {
    const res = graphPath ? runs.get(graphPath) : undefined;
    if (!res) return [];
    const wanted = outputs?.length ? new Set(outputs.map((o) => o.toLowerCase())) : undefined;
    const ids = new Set<string>();
    for (const o of res.outputs ?? []) if (!wanted || wanted.has(String(o.name).toLowerCase())) elementIds(o.value, ids);
    return [...ids];
  };

  return {
    kind: "revit",
    designInModel: true,
    terms: {
      definition: "Dynamo graph (.dyn path or name) and/or 'globals' (global parameters)",
      engine: "Revit/Dynamo",
      geometryFile: "geometry.obj",
      definitionFile: "definition.dyn",
    },
    lastRun: (graph) => runs.get(graph),
    graphOf,
    values: (graph) => ({ ...(tracked.get(graph) ?? {}) }),
    markReload: (graph) => void reloads.add(graph),

    async getInputs(definition) {
      const { inputs, definition: def } = await inputsOf(definition);
      return { definition: def, inputs };
    },

    async apply(definition, changes) {
      const { graph, inputs } = await inputsOf(definition);
      const dynValues: Record<string, unknown> = { ...(graph ? tracked.get(graph.path) ?? {} : {}) };
      const globalChanges: Array<Record<string, unknown>> = [];
      const notes: Record<string, string> = {};
      for (const c of changes) {
        const input = pick(inputs, c.parameter, graph);
        if (input.source === "global_parameter") {
          if (input.read_only) throw new Error(`Global parameter '${input.name}' is driven by a formula (${input.formula}).`);
          const mode = (c.mode ?? "set").toLowerCase();
          globalChanges.push(mode === "toggle" ? { name: input.name, value: !(input.value === true) } : { name: input.name, value: c.value, mode });
        } else {
          const { value, note } = combine(input, c);
          dynValues[input.id] = value;
          if (note) notes[input.name] = note;
        }
      }
      let globalsResult: any;
      if (globalChanges.length) globalsResult = await bridge.call("revit.set_global_parameters", { changes: globalChanges }, long());
      let res: any;
      if (graph) res = await run(graph.path, dynValues);
      return {
        solution: summary(res),
        raw: {
          ...(globalsResult ? { global_parameters: globalsResult.parameters } : {}),
          ...(res ? { dynamo: { evaluated: res.evaluated, duration_s: res.duration_s, inputs_applied: res.inputs_applied, errors: res.errors, warnings: res.warnings } } : {}),
          ...(Object.keys(notes).length ? { notes } : {}),
        },
      };
    },

    async solve(definition) {
      const graphPath = await graphOf(definition);
      if (!graphPath) return undefined;
      return summary(await run(graphPath, { ...(tracked.get(graphPath) ?? {}) }));
    },

    async results(definition, outputs, maxItems = 20) {
      const { graph, inputs, definition: def } = await inputsOf(definition);
      const res = graph ? runs.get(graph.path) : undefined;
      const wanted = outputs?.length ? new Set(outputs.map((o) => o.toLowerCase())) : undefined;
      const outs = (res?.outputs ?? [])
        .filter((o: any) => !wanted || wanted.has(String(o.name).toLowerCase()))
        .map((o: any) => ({ ...o, value: Array.isArray(o.value) ? o.value.slice(0, Math.max(maxItems, 0)) : o.value }));
      const metrics: Record<string, unknown> = outputMetrics(res?.outputs ?? []);
      try {
        Object.assign(metrics, modelMetrics(await bridge.call("revit.metrics", {}, long())));
        const ids = designIds(graph?.path, outputs);
        if (ids.length) Object.assign(metrics, modelMetrics(await bridge.call("revit.metrics", { ids, group_by: "none" }, long()), "design"));
      } catch (err) {
        metrics["metrics_error"] = (err as Error).message;
      }
      return { definition: def, inputs, metrics, outputs: outs };
    },

    async capture({ image, file }) {
      const cap = await bridge.call(
        "revit.capture_viewport",
        {
          view: image.view,
          direction: image.view ? undefined : image.direction ?? "iso_sw",
          display_mode: image.display_mode ?? "shaded_edges",
          width: image.width ?? 1280,
          format: image.format,
          save_path: file,
          return_image: true,
        },
        long(),
      );
      return cap.image_base64 ? { data: cap.image_base64, mimeType: cap.mime_type ?? "image/png" } : undefined;
    },

    async exportGeometry({ definition, outputs, file }) {
      const ids = designIds(await graphOf(definition), outputs);
      try {
        await bridge.call("revit.export", { path: file, ...(ids.length ? { ids } : {}) }, long());
        return true;
      } catch (err) {
        if (err instanceof BridgeError && err.code === RpcCodes.NotFound) return false;
        throw err;
      }
    },

    async saveDefinition(definition, file) {
      const graphPath = await graphOf(definition);
      if (!graphPath) throw new Error("Only Dynamo graphs can be copied: global parameter values are stored in the variant itself.");
      const text = await fs.readFile(graphPath, "utf8");
      await fs.mkdir(path.dirname(file), { recursive: true });
      await fs.writeFile(file, writeDynInputs(text, tracked.get(graphPath) ?? {}));
    },

    async designSource(definition, outputs): Promise<GeometrySource> {
      const ids = designIds(await graphOf(definition), outputs);
      // Without element outputs, urban massing in Revit is usually modelled with masses.
      return ids.length ? { ids } : { filter: { categories: ["Mass"] } };
    },

    definitionKey(def) {
      if (def.path) return path.basename(def.path).replace(/\.dyn$/i, "");
      return "global_parameters";
    },
  };
}

