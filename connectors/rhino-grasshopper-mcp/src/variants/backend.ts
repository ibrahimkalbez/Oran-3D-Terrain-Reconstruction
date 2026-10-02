import path from "node:path";
import type { BridgeClient } from "../bridge/client.js";
import { BridgeError, RpcCodes } from "../bridge/client.js";
import type { Config } from "../config.js";

/**
 * What the variant engine (variants, design exploration, optimisation) needs from a design
 * application. Grasshopper drives Rhino geometry from a definition's sliders; the Revit backend
 * (revit-dynamo-mcp) drives a Revit model from Dynamo graph inputs and global parameters.
 */
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

/** One design input: a slider, a Dynamo input node, a global parameter… */
export interface DesignInput {
  id: string;
  name: string;
  kind?: string;
  value?: unknown;
  min?: number;
  max?: number;
  step?: number;
  options?: unknown[];
  [key: string]: unknown;
}

export interface SolveSummary {
  duration_ms?: number;
  errors?: unknown[];
  warnings?: unknown[];
}

export interface DesignResults {
  definition: { name?: string; path?: string | null };
  inputs: DesignInput[];
  metrics: Record<string, unknown>;
  outputs: any[];
}

/** Where geometry is read from (same shape as the Fusion connector's sources). */
export interface GeometrySource {
  layer?: string;
  ids?: string[];
  filter?: Record<string, unknown>;
  grasshopper?: { definition?: string; outputs?: string[] };
}

export interface DesignBackend {
  kind: "grasshopper" | "revit";
  /** True when the designed geometry lives in the model (Revit elements), false for Grasshopper previews. */
  designInModel?: boolean;
  /** Where the designed geometry of the current state is (default: the Grasshopper outputs). */
  designSource?(definition: string | undefined, outputs?: string[]): Promise<GeometrySource | undefined>;
  /** Words used in tool descriptions. */
  terms: {
    definition: string; // "Grasshopper definition" | "Dynamo graph (.dyn) or 'globals'"
    engine: string; // "Grasshopper" | "Revit/Dynamo"
    geometryFile: string; // "geometry.3dm" | "geometry.obj"
    definitionFile: string; // "definition.gh" | "definition.dyn"
  };
  /** Inputs with ranges (for sweeps, sampling and restoring values). */
  getInputs(definition?: string): Promise<{ definition: { name?: string; path?: string | null }; inputs: DesignInput[] }>;
  /** Applies the changes (absolute or relative) and recomputes the design. */
  apply(definition: string | undefined, changes: ParameterChangeInput[]): Promise<{ solution?: SolveSummary; raw?: unknown }>;
  /** Recomputes without changes. */
  solve(definition?: string): Promise<SolveSummary | undefined>;
  /** Current input values, metrics and outputs. */
  results(definition: string | undefined, outputs?: string[], maxItems?: number): Promise<DesignResults>;
  /** Image of the current design, saved to `file`. */
  capture(o: { definition?: string; outputs?: string[]; image: ImageOptions; file: string }): Promise<{ data: string; mimeType: string } | undefined>;
  /** Writes the design geometry to `file`; false when there is nothing to export. */
  exportGeometry(o: { definition?: string; outputs?: string[]; file: string; tags: Record<string, string> }): Promise<boolean>;
  /** Copy of the definition with the current values (optional). */
  saveDefinition?(definition: string | undefined, file: string): Promise<void>;
  /** Keeps the variant's geometry in the model (optional). */
  bake?(o: { definition?: string; outputs?: string[]; layer: string; tag: string; userText: Record<string, string> }): Promise<{ layer: string; tag: string; count: number }>;
  /** Folder key grouping the variants of a definition. */
  definitionKey(def: { name?: string; path?: string | null }): string;
}

/** Name of the folder grouping the variants of a Grasshopper definition. */
export function ghDefinitionKey(def: { name?: string; path?: string | null }): string {
  if (def.path) return path.basename(def.path).replace(/\.(gh|ghx)$/i, "");
  return (def.name ?? "definition").replace(/\*$/, "").replace(/\.(gh|ghx)$/i, "");
}

/** Grasshopper definitions in Rhino, through the grasshopper.* and rhino.* methods of the bridge. */
export function grasshopperBackend(bridge: BridgeClient, config: Config): DesignBackend {
  const long = () => ({ timeoutMs: config.longTimeoutMs });
  return {
    kind: "grasshopper",
    terms: { definition: "Grasshopper definition", engine: "Grasshopper", geometryFile: "geometry.3dm", definitionFile: "definition.gh" },
    async getInputs(definition) {
      const p = await bridge.call("grasshopper.get_parameters", { definition, include_outputs: false });
      return { definition: p.definition ?? {}, inputs: p.inputs ?? [] };
    },
    async apply(definition, changes) {
      const res = await bridge.call("grasshopper.set_parameter", { definition, parameters: changes, solve: true }, long());
      return { solution: res.solution, raw: res };
    },
    async solve(definition) {
      return bridge.call("grasshopper.solve", { definition }, long());
    },
    async results(definition, outputs, maxItems = 20) {
      const r = await bridge.call("grasshopper.get_results", { definition, outputs, max_items: maxItems }, long());
      return { definition: r.definition ?? {}, inputs: r.inputs ?? [], metrics: r.metrics ?? {}, outputs: r.outputs ?? [] };
    },
    async capture({ outputs, image, file }) {
      const cap = await bridge.call(
        "rhino.capture_viewport",
        {
          view: image.view,
          width: image.width ?? 1280,
          height: image.height ?? 800,
          direction: image.direction,
          display_mode: image.display_mode,
          format: image.format,
          preview: "auto",
          outputs,
          save_path: file,
          return_image: true,
        },
        long(),
      );
      return cap.image_base64 ? { data: cap.image_base64, mimeType: cap.mime_type ?? "image/png" } : undefined;
    },
    async exportGeometry({ definition, outputs, file, tags }) {
      try {
        await bridge.call("grasshopper.export_geometry", { definition, outputs, file_path: file, user_text: tags }, long());
        return true;
      } catch (err) {
        if (err instanceof BridgeError && err.code === RpcCodes.NotFound) return false;
        throw err;
      }
    },
    async saveDefinition(definition, file) {
      await bridge.call("grasshopper.save_definition", { definition, path: file, copy: true }, long());
    },
    async bake({ definition, outputs, layer, tag, userText }) {
      const baked = await bridge.call(
        "grasshopper.export_geometry",
        { definition, outputs, layer, layer_per_output: false, bake_tag: tag, replace: true, user_text: userText },
        long(),
      );
      return { layer, tag, count: baked.baked_count };
    },
    definitionKey: ghDefinitionKey,
  };
}
