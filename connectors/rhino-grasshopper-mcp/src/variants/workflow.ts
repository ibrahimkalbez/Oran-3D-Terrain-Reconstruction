import fs from "node:fs/promises";
import path from "node:path";
import type { BridgeClient } from "../bridge/client.js";
import { BridgeError, RpcCodes } from "../bridge/client.js";
import type { Config } from "../config.js";
import type { VariantRecord, VariantStore } from "./store.js";

export interface ParameterChangeInput {
  parameter: string;
  value?: unknown;
  mode?: string;
  on_out_of_range?: string;
}

export interface ImageOptions {
  width?: number;
  height?: number;
  direction?: string;
  display_mode?: string;
  format?: "png" | "jpg";
  view?: string;
}

export interface CreateVariantOptions {
  definition?: string;
  name?: string;
  description?: string;
  parameters?: ParameterChangeInput[] | Record<string, unknown>;
  outputs?: string[];
  capture?: boolean;
  image?: ImageOptions;
  save_geometry?: boolean;
  save_definition?: boolean;
  bake?: boolean;
  bake_layer?: string;
  /** Extra fields stored on the record (used by the fusion connector: rules, simulations…). */
  extra?: Record<string, unknown>;
}

export interface CreatedVariant {
  record: VariantRecord;
  image?: { data: string; mimeType: string };
}

export function normalizeChanges(parameters: CreateVariantOptions["parameters"]): ParameterChangeInput[] {
  if (!parameters) return [];
  if (Array.isArray(parameters)) return parameters;
  return Object.entries(parameters).map(([parameter, value]) => {
    // {"Height": {"value": 10, "mode": "percent"}} is accepted too.
    if (value && typeof value === "object" && !Array.isArray(value) && "value" in (value as object)) {
      const v = value as { value: unknown; mode?: string };
      return { parameter, value: v.value, mode: v.mode };
    }
    return { parameter, value };
  });
}

/** Name of the folder grouping the variants of a definition. */
export function definitionKey(def: { name?: string; path?: string | null }): string {
  if (def.path) return path.basename(def.path).replace(/\.(gh|ghx)$/i, "");
  return (def.name ?? "definition").replace(/\*$/, "").replace(/\.(gh|ghx)$/i, "");
}

function trimOutputs(outputs: any[]): unknown[] {
  return (outputs ?? []).map((o) => {
    const { values, items, texts, tree, ...rest } = o;
    return rest;
  });
}

/**
 * Applies the parameters, solves, measures, captures and stores one variant.
 * Each step that fails is reported on the record instead of losing the variant.
 */
export async function createVariant(
  bridge: BridgeClient,
  store: VariantStore,
  config: Config,
  o: CreateVariantOptions,
): Promise<CreatedVariant> {
  const long = { timeoutMs: config.longTimeoutMs };
  const changes = normalizeChanges(o.parameters);
  let solution: any;
  if (changes.length > 0) {
    const res = await bridge.call("grasshopper.set_parameter", { definition: o.definition, parameters: changes, solve: true }, long);
    solution = res.solution;
  } else {
    solution = await bridge.call("grasshopper.solve", { definition: o.definition }, long);
  }

  const results = await bridge.call("grasshopper.get_results", { definition: o.definition, outputs: o.outputs, max_items: 20 }, long);
  const key = definitionKey(results.definition);
  const name = o.name?.trim() || describeChanges(changes) || "variant";
  const { id, number, dir } = await store.allocate(key, name);

  const parameters: Record<string, unknown> = {};
  for (const input of results.inputs ?? []) parameters[input.name] = input.value;

  const record: VariantRecord = {
    id,
    number,
    name,
    description: o.description,
    definition: { key, name: results.definition?.name, path: results.definition?.path ?? null },
    created_at: new Date().toISOString(),
    parameters,
    changes: changes.map((c) => ({ parameter: c.parameter, value: c.value, mode: c.mode })),
    metrics: results.metrics ?? {},
    outputs: trimOutputs(results.outputs),
    solution: solution
      ? { duration_ms: solution.duration_ms, errors: solution.errors, warnings: solution.warnings }
      : undefined,
    dir,
    files: {},
    baked: null,
    kept: false,
    ...(o.extra ?? {}),
  };

  const warnings: string[] = [];
  let image: CreatedVariant["image"];
  if (o.capture !== false) {
    try {
      const img = o.image ?? {};
      const file = path.join(dir, `preview.${img.format === "jpg" ? "jpg" : "png"}`);
      const cap = await bridge.call(
        "rhino.capture_viewport",
        {
          view: img.view,
          width: img.width ?? 1280,
          height: img.height ?? 800,
          direction: img.direction,
          display_mode: img.display_mode,
          format: img.format,
          preview: "auto",
          outputs: o.outputs,
          save_path: file,
          return_image: true,
        },
        long,
      );
      record.files.preview = path.basename(file);
      if (cap.image_base64) image = { data: cap.image_base64, mimeType: cap.mime_type ?? "image/png" };
    } catch (err) {
      warnings.push(`capture failed: ${(err as Error).message}`);
    }
  }

  if (o.save_geometry !== false) {
    try {
      const file = path.join(dir, "geometry.3dm");
      await bridge.call(
        "grasshopper.export_geometry",
        { definition: o.definition, outputs: o.outputs, file_path: file, user_text: { variant: `${key}/${id}`, variant_name: name } },
        long,
      );
      record.files.geometry = "geometry.3dm";
    } catch (err) {
      if (!(err instanceof BridgeError && err.code === RpcCodes.NotFound)) warnings.push(`geometry export failed: ${(err as Error).message}`);
      record.files.geometry = null;
    }
  }

  if (o.save_definition) {
    try {
      const file = path.join(dir, "definition.gh");
      await bridge.call("grasshopper.save_definition", { definition: o.definition, path: file, copy: true }, long);
      record.files.definition = "definition.gh";
    } catch (err) {
      warnings.push(`definition copy failed: ${(err as Error).message}`);
    }
  }

  if (o.bake) {
    const layer = o.bake_layer ?? `Variants::${key}::${id} ${name}`.replace(/[^\w:. -]/g, "_");
    const tag = `variant:${key}:${id}`;
    try {
      const baked = await bridge.call(
        "grasshopper.export_geometry",
        {
          definition: o.definition,
          outputs: o.outputs,
          layer,
          layer_per_output: false,
          bake_tag: tag,
          replace: true,
          user_text: { variant: `${key}/${id}`, variant_name: name, ...flatParams(parameters) },
        },
        long,
      );
      record.baked = { layer, tag, count: baked.baked_count };
    } catch (err) {
      warnings.push(`bake failed: ${(err as Error).message}`);
    }
  }

  if (warnings.length) (record as any).warnings = warnings;
  await store.save(record);
  return { record, image };
}

function flatParams(params: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(params)) out[`param.${k}`] = typeof v === "string" ? v : JSON.stringify(v);
  return out;
}

export function describeChanges(changes: ParameterChangeInput[]): string {
  return changes
    .map((c) => {
      const v = typeof c.value === "number" ? c.value : JSON.stringify(c.value);
      switch (c.mode) {
        case "percent":
          return `${c.parameter}${Number(c.value) >= 0 ? "+" : ""}${v}%`;
        case "add":
          return `${c.parameter}${Number(c.value) >= 0 ? "+" : ""}${v}`;
        case "multiply":
          return `${c.parameter}x${v}`;
        default:
          return `${c.parameter}=${v}`;
      }
    })
    .join(" ")
    .slice(0, 60);
}

/** Short view of a record for lists and job results. */
export function summarize(record: VariantRecord) {
  return {
    id: record.id,
    name: record.name,
    definition: record.definition.key,
    created_at: record.created_at,
    kept: record.kept,
    changes: record.changes,
    metrics: record.metrics,
    errors: record.solution?.errors?.length ?? 0,
    folder: record.dir,
    files: record.files,
    ...((record as any).warnings ? { warnings: (record as any).warnings } : {}),
  };
}

export async function readPreview(record: VariantRecord): Promise<{ data: string; mimeType: string } | undefined> {
  if (!record.files.preview) return undefined;
  try {
    const buf = await fs.readFile(path.join(record.dir, record.files.preview));
    return { data: buf.toString("base64"), mimeType: record.files.preview.endsWith(".jpg") ? "image/jpeg" : "image/png" };
  } catch {
    return undefined;
  }
}
