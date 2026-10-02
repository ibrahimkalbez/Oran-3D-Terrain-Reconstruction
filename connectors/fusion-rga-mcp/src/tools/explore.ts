import fsp from "node:fs/promises";
import path from "node:path";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import { isFinished, jobView, type Job } from "../../../rhino-grasshopper-mcp/src/util/jobs.js";
import { guarded, ok } from "../../../rhino-grasshopper-mcp/src/util/result.js";
import { createVariant, summarize } from "../../../rhino-grasshopper-mcp/src/variants/workflow.js";
import { SiteSourcesSchema, WRITE, type FusionContext } from "../context.js";
import { gridDesign, lhsDesign, paretoFront, randomDesign, resolveDimensions, unique, type DimensionSpec } from "../explore/doe.js";
import { optimize, scoreAll, type Evaluation } from "../explore/optimize.js";
import { evaluateRules, type RuleSet, type SiteModel } from "../rules/engine.js";
import { loadBuildings, loadSite, type SiteSources } from "../site.js";
import { resolveSources, RuleSchema } from "./urban.js";

const DimensionSchema = z.object({
  min: z.number().optional(),
  max: z.number().optional(),
  steps: z.number().int().min(1).optional(),
  step: z.number().positive().optional(),
  values: z.array(z.any()).optional(),
  integer: z.boolean().optional(),
});

const RulesOption = z
  .object({
    rule_set: z.string().optional(),
    rules: z.array(RuleSchema).optional(),
    sources: SiteSourcesSchema.optional().describe("Default: buildings = Grasshopper outputs, other layers detected"),
    building_outputs: z.array(z.string()).optional().describe("Grasshopper outputs holding the buildings"),
  })
  .optional()
  .describe("Urban rules each design must satisfy (feasibility)");

const Objectives = z.record(z.string(), z.enum(["max", "min"])).describe("Metrics to maximise/minimise, e.g. {\"gfa\": \"max\", \"height_max\": \"min\"}");

/** Evaluates designs in Grasshopper: set parameters → solve → metrics (+ urban rules). */
async function makeEvaluator(ctx: FusionContext, definition: string | undefined, rules: z.infer<typeof RulesOption>) {
  const long = { timeoutMs: ctx.config.longTimeoutMs };
  let ruleSet: RuleSet | undefined;
  let staticSite: SiteModel | undefined;
  let buildingSource: SiteSources["buildings"];
  if (rules) {
    ruleSet = rules.rules?.length ? { name: "inline", rules: rules.rules as RuleSet["rules"] } : await ctx.rules.get(rules.rule_set ?? "exemple_zone_urbaine");
    const given = (rules.sources ?? {}) as SiteSources;
    buildingSource = given.buildings ?? { grasshopper: { definition, outputs: rules.building_outputs } };
    const { sources } = await resolveSources(ctx, { ...given, buildings: buildingSource });
    staticSite = await loadSite(ctx.bridge, { ...sources, buildings: undefined }, ctx.config.longTimeoutMs);
  }
  return async (params: Record<string, unknown>): Promise<Evaluation> => {
    const changes = Object.entries(params).map(([parameter, value]) => ({ parameter, value }));
    const set = await ctx.bridge.call("grasshopper.set_parameter", { definition, parameters: changes, solve: true }, long);
    const res = await ctx.bridge.call("grasshopper.get_results", { definition, max_items: 0 }, long);
    const metrics: Record<string, number> = {};
    for (const [k, v] of Object.entries(res.metrics ?? {})) if (typeof v === "number") metrics[k] = v;
    let feasible = (set.solution?.errors?.length ?? 0) === 0;
    let violations = feasible ? 0 : 100;
    if (ruleSet && staticSite) {
      const buildings = await loadBuildings(ctx.bridge, buildingSource!, ctx.config.longTimeoutMs);
      const report = evaluateRules({ ...staticSite, buildings }, metrics, ruleSet);
      for (const [k, v] of Object.entries(report.variables)) if (!(k in metrics)) metrics[k] = v;
      metrics["rules.score"] = report.score;
      feasible = feasible && report.compliant;
      violations += report.results.filter((r) => r.status === "fail" && r.severity === "error").length;
    }
    return { params, metrics, feasible, violations };
  };
}

async function saveExploration(ctx: FusionContext, kind: string, data: unknown, rows: Array<Record<string, unknown>>): Promise<string> {
  const dir = path.join(ctx.config.workspace, "explorations");
  await fsp.mkdir(dir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[-:]/g, "").slice(0, 15);
  const base = path.join(dir, `${kind}-${stamp}`);
  await fsp.writeFile(base + ".json", JSON.stringify(data, null, 2));
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  const esc = (v: unknown) => (v === undefined || v === null ? "" : typeof v === "string" && /[,;"\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : String(v));
  await fsp.writeFile(base + ".csv", [cols.join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\n"));
  return base + ".json";
}

function table(rows: Array<Record<string, unknown>>, columns: string[], max = 30): string {
  const fmt = (v: unknown) => (typeof v === "number" ? (Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("en-US") : String(Math.round(v * 1000) / 1000)) : v === undefined ? "–" : String(v));
  const head = `| ${columns.join(" | ")} |\n|${columns.map(() => "---").join("|")}|`;
  return [head, ...rows.slice(0, max).map((r) => `| ${columns.map((c) => fmt(r[c])).join(" | ")} |`)].join("\n") + (rows.length > max ? `\n… ${rows.length - max} more rows in the CSV` : "");
}

export function registerExploreTools(ctx: FusionContext): void {
  const { server, bridge, jobs } = ctx;

  const finish = async (job: Job, waitSeconds: number, render: (job: Job) => CallToolResult): Promise<CallToolResult> => {
    const done = await jobs.waitFor(job.id, waitSeconds * 1000);
    if (!isFinished(done)) return ok(jobView(done), `Job ${job.id} running (${done.progress.done}/${done.progress.total}): follow it with job_status.`);
    return render(done);
  };

  const snapshot = async (definition: string | undefined, names: string[]) => {
    const p = await bridge.call("grasshopper.get_parameters", { definition, include_outputs: false });
    return { inputs: p.inputs ?? [], original: Object.fromEntries((p.inputs ?? []).filter((i: any) => names.includes(i.name)).map((i: any) => [i.name, i.value])) };
  };

  server.registerTool(
    "design_explore",
    {
      title: "Explore the design space",
      description:
        "Design of experiments on Grasshopper parameters: full grid, random or Latin hypercube sampling of the ranges (default: slider ranges). " +
        "Each design is solved and measured; with 'rules' it is also checked against urban rules (feasible or not). Results: table, Pareto front " +
        "for the objectives, best design, CSV/JSON in the workspace 'explorations' folder; save='best'|'pareto'|'all' also stores them as variants " +
        "with images. Runs as a background job (job_status).",
      inputSchema: {
        definition: z.string().optional(),
        space: z.record(z.string(), DimensionSchema).describe("Parameters to vary: {\"Building_Height\": {min: 12, max: 30, steps: 4}, \"Floors\": {}}"),
        method: z.enum(["grid", "random", "lhs"]).optional().describe("Default grid (3 levels) for ≤ 3 parameters, otherwise lhs"),
        samples: z.number().int().positive().optional().describe("random/lhs: number of designs (default 20)"),
        seed: z.number().int().optional(),
        objectives: Objectives.optional(),
        weights: z.record(z.string(), z.number()).optional(),
        rules: RulesOption,
        save: z.enum(["none", "best", "pareto", "all"]).optional().describe("Designs stored as variants (default best)"),
        restore: z.boolean().optional().describe("Restore the original parameter values (default true)"),
        max_evaluations: z.number().int().positive().max(2000).optional().describe("Safety limit (default 200)"),
        wait_seconds: z.number().int().min(0).max(240).optional(),
      },
      annotations: WRITE,
    },
    guarded(async (args) => {
      const names = Object.keys(args.space);
      const { inputs, original } = await snapshot(args.definition, names);
      const dims = resolveDimensions(args.space as Record<string, DimensionSpec>, inputs);
      const method = args.method ?? (dims.length <= 3 ? "grid" : "lhs");
      const designs = unique(method === "grid" ? gridDesign(dims) : method === "random" ? randomDesign(dims, args.samples ?? 20, args.seed) : lhsDesign(dims, args.samples ?? 20, args.seed));
      const max = args.max_evaluations ?? 200;
      if (designs.length > max) throw new Error(`${designs.length} designs (> max_evaluations=${max}): fewer steps, lhs sampling, or raise max_evaluations.`);

      const job = jobs.start("explore", `${designs.length} designs (${method})`, designs.length, async (h) => {
        const evaluate = await makeEvaluator(ctx, args.definition, args.rules);
        const evals: Evaluation[] = [];
        try {
          for (let i = 0; i < designs.length; i++) {
            h.checkCancelled();
            h.update(i, `design ${i + 1}/${designs.length}`);
            try {
              evals.push(await evaluate(designs[i]));
            } catch (err) {
              evals.push({ params: designs[i], metrics: {}, feasible: false, violations: 999, error: (err as Error).message });
            }
          }
        } finally {
          if (args.restore !== false && Object.keys(original).length) {
            await bridge
              .call("grasshopper.set_parameter", { definition: args.definition, parameters: Object.entries(original).map(([parameter, value]) => ({ parameter, value })), solve: true }, { timeoutMs: ctx.config.longTimeoutMs })
              .catch(() => undefined);
          }
        }
        const objectives = (args.objectives ?? {}) as Record<string, "max" | "min">;
        const scores = Object.keys(objectives).length ? scoreAll(evals, objectives, args.weights) : evals.map(() => 0);
        const rows = evals.map((e, i) => ({ design: i + 1, ...e.params, ...e.metrics, feasible: e.feasible, score: scores[i], ...(e.error ? { error: e.error } : {}) }));
        const feasibleIdx = rows.map((r, i) => (r.feasible ? i : -1)).filter((i) => i >= 0);
        const pareto = Object.keys(objectives).length ? paretoFront(feasibleIdx.map((i) => rows[i]), objectives).map((k) => feasibleIdx[k]) : [];
        for (const i of pareto) (rows[i] as any).pareto = true;
        const ranked = [...feasibleIdx].sort((a, b) => scores[b] - scores[a]);
        const best = ranked[0];

        const toSave = args.save === "none" ? [] : args.save === "all" ? rows.map((_, i) => i) : args.save === "pareto" ? pareto : best !== undefined ? [best] : [];
        const saved = [];
        for (const i of toSave) {
          h.checkCancelled();
          const { record } = await createVariant(bridge, ctx.variants, ctx.config, {
            definition: args.definition,
            name: `explore ${i + 1}${i === best ? " best" : ""}`,
            parameters: evals[i].params,
            image: { width: 960, height: 600 },
            extra: { exploration: { design: i + 1, feasible: evals[i].feasible, score: scores[i], pareto: pareto.includes(i) } },
          });
          saved.push(summarize(record));
        }
        if (toSave.length && args.restore !== false && Object.keys(original).length) {
          await bridge
            .call("grasshopper.set_parameter", { definition: args.definition, parameters: Object.entries(original).map(([parameter, value]) => ({ parameter, value })), solve: true }, { timeoutMs: ctx.config.longTimeoutMs })
            .catch(() => undefined);
        }
        const file = await saveExploration(ctx, "explore", { method, dims, objectives, rows, pareto: pareto.map((i) => i + 1), best: best !== undefined ? best + 1 : null }, rows);
        const columns = ["design", ...dims.map((d) => d.name), ...Object.keys(objectives), ...(args.rules ? ["feasible"] : []), ...(Object.keys(objectives).length ? ["score", "pareto"] : [])];
        return {
          method,
          evaluated: rows.length,
          feasible: feasibleIdx.length,
          best: best !== undefined ? rows[best] : null,
          pareto: pareto.map((i) => rows[i]),
          saved_variants: saved,
          file,
          table: table(rows, columns),
        };
      });
      return finish(job, args.wait_seconds ?? 50, (done) => {
        if (done.status !== "done") return ok(jobView(done), `Exploration ${done.status}: ${done.error ?? ""}`);
        const r = done.result as any;
        return ok({ ...r, table: undefined }, `${r.table}\n\n${r.feasible}/${r.evaluated} feasible designs.${r.best ? ` Best: design ${r.best.design}.` : ""}`);
      });
    }),
  );

  server.registerTool(
    "design_optimize",
    {
      title: "Optimise the design",
      description:
        "Genetic optimisation (Galapagos-like) of Grasshopper parameters towards objectives, with urban rules as constraints " +
        "(infeasible designs are ranked last). Each generation solves the definition 'population' times. The best design is applied " +
        "to the definition and saved as a variant with its image. Background job: follow with job_status.",
      inputSchema: {
        definition: z.string().optional(),
        space: z.record(z.string(), DimensionSchema),
        objectives: Objectives,
        weights: z.record(z.string(), z.number()).optional(),
        rules: RulesOption,
        population: z.number().int().min(4).max(50).optional().describe("Default 8"),
        generations: z.number().int().min(1).max(50).optional().describe("Default 5"),
        mutation: z.number().min(0).max(1).optional(),
        seed: z.number().int().optional(),
        apply_best: z.boolean().optional().describe("Leave the best design in Grasshopper (default true)"),
        wait_seconds: z.number().int().min(0).max(240).optional(),
      },
      annotations: WRITE,
    },
    guarded(async (args) => {
      const names = Object.keys(args.space);
      const { inputs, original } = await snapshot(args.definition, names);
      const dims = resolveDimensions(args.space as Record<string, DimensionSpec>, inputs);
      const pop = args.population ?? 8;
      const gens = args.generations ?? 5;
      const job = jobs.start("optimize", `GA ${pop}×${gens}`, pop * gens, async (h) => {
        const evaluate = await makeEvaluator(ctx, args.definition, args.rules);
        const result = await optimize(dims, evaluate, { objectives: args.objectives as Record<string, "max" | "min">, weights: args.weights, population: pop, generations: gens, mutation: args.mutation, seed: args.seed }, {
          progress: (d, t, m) => h.update(d, m),
          checkCancelled: () => h.checkCancelled(),
        });
        let variant = null;
        if (result.best) {
          const { record } = await createVariant(bridge, ctx.variants, ctx.config, {
            definition: args.definition,
            name: "optimum",
            parameters: result.best.params,
            extra: { optimization: { score: result.best.score, feasible: result.best.feasible, generation: result.best.generation } },
          });
          variant = summarize(record);
        }
        if (args.apply_best === false && Object.keys(original).length) {
          await bridge.call("grasshopper.set_parameter", { definition: args.definition, parameters: Object.entries(original).map(([parameter, value]) => ({ parameter, value })), solve: true }, { timeoutMs: ctx.config.longTimeoutMs }).catch(() => undefined);
        }
        const rows = result.evaluations.map((e, i) => ({ evaluation: i + 1, generation: e.generation + 1, ...e.params, ...e.metrics, feasible: e.feasible, score: e.score }));
        const file = await saveExploration(ctx, "optimize", { dims, objectives: args.objectives, history: result.history, best: result.best }, rows);
        const top = [...rows].sort((a, b) => Number(b.feasible) - Number(a.feasible) || b.score - a.score).slice(0, 8);
        return {
          evaluations: rows.length,
          history: result.history,
          best: result.best,
          variant,
          file,
          table: table(top, ["evaluation", "generation", ...dims.map((d) => d.name), ...Object.keys(args.objectives), "feasible", "score"]),
        };
      });
      return finish(job, args.wait_seconds ?? 50, (done) => {
        if (done.status !== "done") return ok(jobView(done), `Optimisation ${done.status}: ${done.error ?? ""}`);
        const r = done.result as any;
        return ok({ ...r, table: undefined }, `Top designs:\n${r.table}\n\nBest saved as ${r.variant?.id ?? "—"}.`);
      });
    }),
  );
}

