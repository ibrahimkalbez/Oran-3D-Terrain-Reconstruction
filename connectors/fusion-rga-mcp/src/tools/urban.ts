import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import { hostMethod } from "../../../rhino-grasshopper-mcp/src/host.js";
import { guarded, ok, toText } from "../../../rhino-grasshopper-mcp/src/util/result.js";
import { READ, SiteSourcesSchema, WRITE, type FusionContext } from "../context.js";
import { evaluateRules, RULE_TYPES, type RuleReport, type RuleSet } from "../rules/engine.js";
import { detectSources, loadSite, siteSummary, type SiteSources } from "../site.js";

export const RuleSchema = z.object({
  id: z.string(),
  type: z.enum(RULE_TYPES),
  label: z.string().optional(),
  severity: z.enum(["error", "warning"]).optional(),
  value: z.number().optional(),
  min: z.number().optional(),
  max: z.number().optional(),
  ratio: z.number().optional().describe("× building height (setbacks, spacing)"),
  metric: z.string().optional().describe("metric_* rules: metric name (design output — Grasshopper or Dynamo/Revit — or site variable)"),
  expression: z.string().optional().describe("expression rule, e.g. 'gfa / site_area <= 2.5 and height_max <= 30'"),
  allow_contiguous: z.boolean().optional(),
  scope: z.object({ layer: z.string().optional(), zone: z.string().optional(), use: z.string().optional() }).optional(),
});

const RULES_HELP = `Rule types:
- max_height / min_height {value m} · max_floors {value} (floors from user text 'floors'/'niveaux' or height ÷ floor_height) · max_footprint {value m²}
- max_coverage {value} = CES (footprint ÷ plot area) · max_far {value} = COS (floor area ÷ plot area) · min_green_ratio {value}
- min_boundary_setback {ratio×H, min, value, allow_contiguous} = recul sur limites séparatives
- min_street_setback {value} = recul par rapport aux voies · min_building_spacing {ratio×H, min, allow_contiguous} = prospect entre bâtiments
- metric_max / metric_min / metric_range {metric, value | min, max} on Grasshopper metrics or site variables
- expression {expression} e.g. "far <= 3 and coverage <= 0.6"
Site variables: building_count, footprint_area, gfa, height_max, height_mean, floors_max, site_area, coverage, far, green_area, green_ratio, road_length.`;

/** Fills missing site sources from the layer names of the document. */
export async function resolveSources(ctx: FusionContext, given: SiteSources | undefined): Promise<{ sources: SiteSources; detected: string[] }> {
  const sources: SiteSources = { ...(given ?? {}) };
  const detected: string[] = [];
  const missing = (["buildings", "plots", "roads", "green"] as const).filter((k) => !sources[k]);
  if (missing.length > 0) {
    const auto = await detectSources(ctx.bridge, ctx.config.profile);
    for (const k of missing) {
      if (auto.sources[k]) {
        sources[k] = auto.sources[k];
        detected.push(`${k} ← ${auto.sources[k]!.layer}`);
      }
    }
  }
  return { sources, detected };
}

export function reportMarkdown(r: RuleReport): string {
  const icon = (s: string, sev: string) => (s === "pass" ? "✅" : s === "fail" ? (sev === "warning" ? "⚠️" : "❌") : s === "error" ? "⛔" : "➖");
  const lines = [
    `**${r.rule_set}** — ${r.compliant ? "conforme" : "non conforme"} · ${r.passed} respectée(s), ${r.failed} non respectée(s), ${r.warnings} avertissement(s), ${r.not_applicable} non applicable(s)`,
    "",
    "| | Règle | Limite | Mesure | Écarts |",
    "|---|---|---|---|---|",
  ];
  for (const x of r.results) {
    lines.push(`| ${icon(x.status, x.severity)} | ${x.label} | ${x.limit ?? "–"} | ${x.measured ?? "–"} | ${x.violations.length || (x.message ?? "")} |`);
  }
  return lines.join("\n");
}

export function registerUrbanTools(ctx: FusionContext): void {
  const { server, bridge, config } = ctx;
  const long = config.longTimeoutMs;

  server.registerTool(
    "urban_site_detect",
    {
      title: "Detect the site layers",
      description:
        "Find which layers (Rhino layers; in Revit: categories such as Mass / Property Lines and line styles) hold buildings, plots (parcelles), roads (voirie) and green areas (espaces verts) from their names, " +
        "and optionally load them to report counts, areas and heights. Confirm the result with the user before checking rules.",
      inputSchema: { load: z.boolean().optional().describe("Load the geometry and summarise it (default true)"), sources: SiteSourcesSchema.optional() },
      annotations: READ,
    },
    guarded(async (args) => {
      const auto = await detectSources(bridge, config.profile);
      const sources: SiteSources = { ...auto.sources, ...(args.sources ?? {}) };
      const result: Record<string, unknown> = { sources, candidates: auto.candidates };
      if (args.load !== false) result.summary = siteSummary(await loadSite(bridge, sources, long));
      return ok(result);
    }),
  );

  server.registerTool(
    "urban_rules_list",
    {
      title: "List urban rule sets",
      description: "Rule sets available (built-in examples and the user's saved sets) with their rules.\n" + RULES_HELP,
      inputSchema: {},
      annotations: READ,
    },
    guarded(async () => ok({ rule_sets: await ctx.rules.list(), folder: ctx.rules.dir }, "Built-in sets are EXAMPLES: replace their values with the applicable POS/PDAU.")),
  );

  server.registerTool(
    "urban_rules_save",
    {
      title: "Save a rule set",
      description: "Create or replace a rule set (e.g. the POS of a zone) in the workspace 'rules' folder.\n" + RULES_HELP,
      inputSchema: {
        name: z.string(),
        description: z.string().optional(),
        reference: z.string().optional().describe("Source document (POS, PDAU, article…)"),
        floor_height: z.number().positive().optional().describe("Storey height for floors from heights (default 3 m)"),
        rules: z.array(RuleSchema).min(1),
      },
      annotations: WRITE,
    },
    guarded(async (args) => {
      const file = await ctx.rules.save(args as RuleSet);
      return ok({ saved: file, rules: args.rules.length }, `Rule set '${args.name}' saved.`);
    }),
  );

  server.registerTool(
    "urban_rules_check",
    {
      title: "Check urban rules",
      description:
        "Check buildings against a rule set: heights, floors, CES, COS, green ratio, setbacks to plot limits and streets, spacing " +
        "between buildings, and rules on design metrics. Buildings come from a layer or category (Rhino layer, Revit masses…), from " +
        "Grasshopper outputs, or from the elements a Dynamo graph created. " +
        "Returns a table of rules (✅/❌), the violating buildings, and an image with them in red. Pass 'variant' to store the result on a saved variant.",
      inputSchema: {
        rule_set: z.string().optional().describe("Saved/built-in rule set name"),
        rules: z.array(RuleSchema).optional().describe("Inline rules instead of a rule set"),
        floor_height: z.number().positive().optional(),
        sources: SiteSourcesSchema.optional(),
        design_metrics: z.boolean().optional().describe("Add the design metrics to the variables: Grasshopper results, or Dynamo outputs + Revit quantities (default true)"),
        grasshopper_metrics: z.boolean().optional().describe("Same as design_metrics (kept for compatibility)"),
        highlight: z.boolean().optional().describe("Capture with violating buildings in red (default true)"),
        variant: z.string().optional(),
      },
      annotations: READ,
    },
    guarded(async (args) => {
      const ruleSet: RuleSet = args.rules?.length
        ? { name: "inline", floor_height: args.floor_height, rules: args.rules as RuleSet["rules"] }
        : await ctx.rules.get(args.rule_set ?? "exemple_zone_urbaine");
      if (args.floor_height) ruleSet.floor_height = args.floor_height;
      const { sources, detected } = await resolveSources(ctx, args.sources as SiteSources);
      const site = await loadSite(bridge, sources, long);
      let metrics: Record<string, unknown> = {};
      if ((args.design_metrics ?? args.grasshopper_metrics) !== false) {
        try {
          metrics = (await ctx.backend.results(undefined, undefined, 0)).metrics ?? {};
        } catch {
          metrics = {};
        }
      }
      const report = evaluateRules(site, metrics, ruleSet);
      const content: CallToolResult["content"] = [];
      if (args.highlight !== false && report.violating_ids.length > 0 && site.buildings.every((b) => !b.id.includes("#"))) {
        try {
          const cap = await bridge.call(
            hostMethod(config.profile, "capture_viewport"),
            { direction: "aerial", display_mode: "Shaded", width: 1280, height: 800, zoom: "extents", highlight: { ids: report.violating_ids, color: "#E30613" } },
            { timeoutMs: long },
          );
          content.push({ type: "image", data: cap.image_base64, mimeType: cap.mime_type });
        } catch {
          // image is optional
        }
      }
      if (args.variant) {
        const record = await ctx.variants.find(args.variant);
        (record as any).rules = { rule_set: report.rule_set, compliant: report.compliant, score: report.score, failed: report.results.filter((r) => r.status === "fail").map((r) => r.id) };
        record.metrics = { ...record.metrics, "rules.score": report.score, "rules.failed": report.failed };
        await ctx.variants.save(record);
      }
      const header = reportMarkdown(report) + (detected.length ? `\n\nLayers detected automatically: ${detected.join("; ")}` : "");
      content.push({ type: "text", text: `${header}\n\n${toText({ site: siteSummary(site), sources, report })}` });
      return { content };
    }),
  );
}
