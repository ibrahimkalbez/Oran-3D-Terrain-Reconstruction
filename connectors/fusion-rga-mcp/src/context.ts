import { z } from "zod";
import type { ToolContext } from "../../rhino-grasshopper-mcp/src/context.js";
import { RuleStore } from "./rules/presets.js";
import { SimulationStore } from "./sim/store.js";

export interface FusionContext extends ToolContext {
  rules: RuleStore;
  sims: SimulationStore;
}

export function extendContext(base: ToolContext): FusionContext {
  return { ...base, rules: new RuleStore(base.config.workspace), sims: new SimulationStore(base.config.workspace) };
}

/** Where a category of objects comes from. */
export const SourceSchema = z
  .object({
    layer: z.string().optional().describe("Layer: a Rhino layer path (sub-layers included), or in Revit a pseudo-layer ('Category::Mass', 'LineStyle::Voirie')"),
    ids: z.array(z.string()).optional(),
    filter: z.record(z.string(), z.any()).optional().describe("Full filter — Rhino: layer, types, name, user_text…; Revit: categories, level, family, type_name, user_text…"),
    grasshopper: z
      .object({ definition: z.string().optional(), outputs: z.array(z.string()).optional() })
      .optional()
      .describe("Rhino only: take the geometry from Grasshopper outputs instead of Rhino objects"),
  })
  .describe("Source: {layer} | {ids} | {filter} | {grasshopper: {outputs}}");

export const SiteSourcesSchema = z
  .object({
    buildings: SourceSchema.optional(),
    plots: SourceSchema.optional(),
    roads: SourceSchema.optional(),
    green: SourceSchema.optional(),
    site_area: z.number().positive().optional().describe("Study area (m²) when there are no plots"),
  })
  .describe("Site layers. Missing categories are detected from layer names (urban_site_detect)");

export const READ = { readOnlyHint: true, openWorldHint: false } as const;
export const WRITE = { readOnlyHint: false, destructiveHint: false, openWorldHint: false } as const;
