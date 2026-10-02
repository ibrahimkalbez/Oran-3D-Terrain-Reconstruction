import fs from "node:fs/promises";
import path from "node:path";
import type { Config } from "../config.js";
import { ghDefinitionKey, type DesignBackend, type ImageOptions, type ParameterChangeInput } from "./backend.js";
import type { VariantRecord, VariantStore } from "./store.js";

export type { ImageOptions, ParameterChangeInput } from "./backend.js";

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

/** Name of the folder grouping the variants of a Grasshopper definition. */
export const definitionKey = ghDefinitionKey;

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
  backend: DesignBackend,
  store: VariantStore,
  config: Config,
  o: CreateVariantOptions,
): Promise<CreatedVariant> {
  const changes = normalizeChanges(o.parameters);
  const solution = changes.length > 0 ? (await backend.apply(o.definition, changes)).solution : await backend.solve(o.definition);

  const results = await backend.results(o.definition, o.outputs, 20);
  const key = backend.definitionKey(results.definition);
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
      image = await backend.capture({ definition: o.definition, outputs: o.outputs, image: img, file });
      record.files.preview = path.basename(file);
    } catch (err) {
      warnings.push(`capture failed: ${(err as Error).message}`);
    }
  }

  if (o.save_geometry !== false) {
    const fileName = backend.terms.geometryFile;
    try {
      const written = await backend.exportGeometry({
        definition: o.definition,
        outputs: o.outputs,
        file: path.join(dir, fileName),
        tags: { variant: `${key}/${id}`, variant_name: name },
      });
      record.files.geometry = written ? fileName : null;
    } catch (err) {
      warnings.push(`geometry export failed: ${(err as Error).message}`);
      record.files.geometry = null;
    }
  }

  if (o.save_definition) {
    if (!backend.saveDefinition) warnings.push(`saving a copy of the ${backend.terms.definition} is not supported`);
    else {
      try {
        const fileName = backend.terms.definitionFile;
        await backend.saveDefinition(o.definition, path.join(dir, fileName));
        record.files.definition = fileName;
      } catch (err) {
        warnings.push(`definition copy failed: ${(err as Error).message}`);
      }
    }
  }

  if (o.bake) {
    if (!backend.bake) warnings.push(`bake is not needed with ${backend.terms.engine}: the design is already in the model`);
    else {
      const layer = o.bake_layer ?? `Variants::${key}::${id} ${name}`.replace(/[^\w:. -]/g, "_");
      const tag = `variant:${key}:${id}`;
      try {
        record.baked = await backend.bake({
          definition: o.definition,
          outputs: o.outputs,
          layer,
          tag,
          userText: { variant: `${key}/${id}`, variant_name: name, ...flatParams(parameters) },
        });
      } catch (err) {
        warnings.push(`bake failed: ${(err as Error).message}`);
      }
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
