import fsp from "node:fs/promises";
import path from "node:path";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import { isFinished, jobView, type Job, type JobHandle } from "../../../rhino-grasshopper-mcp/src/util/jobs.js";
import { guarded, ok } from "../../../rhino-grasshopper-mcp/src/util/result.js";
import { createVariant, summarize } from "../../../rhino-grasshopper-mcp/src/variants/workflow.js";
import { SiteSourcesSchema, WRITE, type FusionContext } from "../context.js";
import { gridDesign, lhsDesign, paretoFront, randomDesign, resolveDimensions, unique, type DimensionSpec } from "../explore/doe.js";
import { optimize, scoreAll, type Evaluation } from "../explore/optimize.js";
import { evaluateRules, type Rule, type RuleSet, type SiteModel } from "../rules/engine.js";
import { adapter } from "../sim/registry.js";
import { loadBuildings, loadSite, type SiteSources, type Source } from "../site.js";
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
    sources: SiteSourcesSchema.optional().describe("Default: buildings = the designed geometry (Grasshopper outputs, or the elements produced by the Dynamo graph), other layers detected"),
    building_outputs: z.array(z.string()).optional().describe("Outputs holding the buildings (Grasshopper outputs or Dynamo output nodes)"),
  })
  .optional()
  .describe("Urban rules each design must satisfy (feasibility)");

const Objectives = z.record(z.string(), z.enum(["max", "min"])).describe("Metrics to maximise/minimise, e.g. {\"gfa\": \"max\", \"solar.sun_hours_mean\": \"max\"}");

const SimulationSpec = z.object({
  solver: z.string().describe("solar | ansys_workbench | command (see sim_solvers)"),
  name: z.string().optional().describe("Prefix of its metrics (default: the solver id), e.g. 'wind' → wind.max_velocity"),
  settings: z.record(z.string(), z.any()).optional().describe("Solver settings; the design geometry and parameters are filled in automatically"),
});

const SimulationConstraint = z.object({
  metric: z.string().describe("Simulation metric, e.g. 'solar.area_pct_above_threshold' or 'wind.wind_pct_uncomfortable'"),
  min: z.number().optional(),
  max: z.number().optional(),
  label: z.string().optional(),
});

/** Options shared by design_explore and design_optimize: physical simulations on every design. */
const SimulationOptions = {
  design_outputs: z.array(z.string()).optional().describe("Outputs holding the designed buildings — Grasshopper outputs or Dynamo output nodes (used by the rules and the simulations)"),
  simulations: z
    .array(SimulationSpec)
    .optional()
    .describe("Physical simulations run on every design that passes the urban rules: sun hours (solar), ANSYS Workbench project, PyAnsys/journal script (command)"),
  simulation_constraints: z.array(SimulationConstraint).optional().describe("Limits on simulation results: designs outside them are infeasible"),
  simulate_infeasible: z.boolean().optional().describe("Also simulate designs that already break an urban rule (default false)"),
};

interface EvaluatorOptions {
  definition?: string;
  rules?: z.infer<typeof RulesOption>;
  design_outputs?: string[];
  simulations?: Array<z.infer<typeof SimulationSpec>>;
  simulation_constraints?: Array<z.infer<typeof SimulationConstraint>>;
  simulate_infeasible?: boolean;
}

export function constraintRules(constraints: EvaluatorOptions["simulation_constraints"] = []): Rule[] {
  return constraints.map((c, i) => ({
    id: `SIM${i + 1}`,
    type: c.min !== undefined && c.max !== undefined ? "metric_range" : c.min !== undefined ? "metric_min" : "metric_max",
    metric: c.metric,
    value: c.min !== undefined && c.max !== undefined ? undefined : c.min ?? c.max,
    min: c.min,
    max: c.max,
    label: c.label ?? `${c.metric}${c.min !== undefined ? ` ≥ ${c.min}` : ""}${c.max !== undefined ? ` ≤ ${c.max}` : ""}`,
  }));
}

/**
 * Evaluates designs: set parameters → recompute (Grasshopper or Revit/Dynamo) → metrics → urban
 * rules → physical simulations (only for designs that pass the rules) → simulation constraints.
 */
async function makeEvaluator(ctx: FusionContext, o: EvaluatorOptions, h: JobHandle) {
  const definition = o.definition;
  const outputs = o.design_outputs ?? o.rules?.building_outputs;
  const backend = ctx.backend;
  // Where the designed geometry is: Grasshopper outputs (not in the Rhino document), or the
  // Revit elements produced by the graph (already in the model, so they also cast shadows).
  const designSource = async (): Promise<Source> =>
    (backend.designSource ? await backend.designSource(definition, outputs) : undefined) ?? { grasshopper: { definition, outputs } };
  let ruleSet: RuleSet | undefined;
  let staticSite: SiteModel | undefined;
  let givenBuildings: SiteSources["buildings"];
  if (o.rules) {
    ruleSet = o.rules.rules?.length ? { name: "inline", rules: o.rules.rules as RuleSet["rules"] } : await ctx.rules.get(o.rules.rule_set ?? "exemple_zone_urbaine");
    const given = (o.rules.sources ?? {}) as SiteSources;
    givenBuildings = given.buildings;
    const { sources } = await resolveSources(ctx, { ...given, buildings: givenBuildings ?? (await designSource()) });
    staticSite = await loadSite(ctx.bridge, { ...sources, buildings: undefined }, ctx.config.longTimeoutMs);
  }
  const constraints = constraintRules(o.simulation_constraints);
  const sims = (o.simulations ?? []).map((spec) => ({ spec, solver: adapter(spec.solver) }));
  let designNumber = 0;

  const simulate = async (spec: z.infer<typeof SimulationSpec>, solver: ReturnType<typeof adapter>, params: Record<string, unknown>, design: Source) => {
    const settings: Record<string, any> = { ...(spec.settings ?? {}) };
    // Grasshopper designs are not in the Rhino document: give them to the solver as obstacles.
    // Revit designs are model elements: the default obstacles (whole model) already include them.
    if (solver.id === "solar") {
      if (!backend.designInModel) settings.obstacles ??= design;
      settings.visualize ??= false;
    }
    if (solver.id === "ansys_workbench" && settings.geometry === undefined) settings.geometry = design;
    if (settings.geometry === false) delete settings.geometry;
    settings._variant_parameters = params;
    const prefix = spec.name ?? solver.id;
    const c = await ctx.sims.create(solver.id, `${prefix} design ${designNumber}`, settings);
    try {
      await solver.prepare({ bridge: ctx.bridge, config: ctx.config }, c);
      c.status = "running";
      await ctx.sims.save(c);
      if (solver.run) await solver.run({ bridge: ctx.bridge, config: ctx.config }, c, h);
      await solver.collect({ bridge: ctx.bridge, config: ctx.config }, c);
      c.status = "done";
    } catch (err) {
      c.status = h.job.cancelRequested ? "cancelled" : "failed";
      c.error = (err as Error).message;
    } finally {
      await ctx.sims.save(c);
    }
    h.checkCancelled();
    const metrics: Record<string, number> = {};
    for (const [k, v] of Object.entries(c.metrics ?? {})) if (typeof v === "number") metrics[`${prefix}.${k}`] = v;
    return { id: c.id, metrics, error: c.status === "done" ? undefined : `${prefix}: ${c.error}` };
  };

  return async (params: Record<string, unknown>): Promise<Evaluation> => {
    designNumber++;
    const changes = Object.entries(params).map(([parameter, value]) => ({ parameter, value }));
    const set = await backend.apply(definition, changes);
    const res = await backend.results(definition, undefined, 0);
    const metrics: Record<string, number> = {};
    for (const [k, v] of Object.entries(res.metrics ?? {})) if (typeof v === "number") metrics[k] = v;
    let feasible = (set.solution?.errors?.length ?? 0) === 0;
    let violations = feasible ? 0 : 100;
    const errors: string[] = [];
    const design = await designSource();
    if (ruleSet && staticSite) {
      const buildings = await loadBuildings(ctx.bridge, givenBuildings ?? design, ctx.config.longTimeoutMs);
      const report = evaluateRules({ ...staticSite, buildings }, metrics, ruleSet);
      for (const [k, v] of Object.entries(report.variables)) if (!(k in metrics)) metrics[k] = v;
      metrics["rules.score"] = report.score;
      feasible = feasible && report.compliant;
      violations += report.results.filter((r) => r.status === "fail" && r.severity === "error").length;
    }
    const caseIds: string[] = [];
    if (sims.length > 0 && (feasible || o.simulate_infeasible)) {
      for (const { spec, solver } of sims) {
        h.log(`design ${designNumber}: ${spec.name ?? solver.id}`);
        const r = await simulate(spec, solver, params, design);
        caseIds.push(r.id);
        Object.assign(metrics, r.metrics);
        if (r.error) {
          errors.push(r.error);
          feasible = false;
          violations += 1;
        }
      }
    }
    if (constraints.length > 0) {
      const report = evaluateRules({ buildings: [], plots: [], roads: [], green: [] }, metrics, { name: "simulation constraints", rules: constraints });
      const failed = report.results.filter((r) => r.status === "fail").length;
      metrics["constraints.failed"] = failed;
      if (failed > 0) {
        feasible = false;
        violations += failed;
      }
    }
    return { params, metrics, feasible, violations, simulations: caseIds, ...(errors.length ? { error: errors.join("; ") } : {}) };
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
  const { server, jobs, backend } = ctx;
  const engine = backend.terms.engine;
  const restore = (definition: string | undefined, original: Record<string, unknown>) =>
    backend.apply(definition, Object.entries(original).map(([parameter, value]) => ({ parameter, value }))).catch(() => undefined);

  const finish = async (job: Job, waitSeconds: number, render: (job: Job) => CallToolResult): Promise<CallToolResult> => {
    const done = await jobs.waitFor(job.id, waitSeconds * 1000);
    if (!isFinished(done)) return ok(jobView(done), `Job ${job.id} running (${done.progress.done}/${done.progress.total}): follow it with job_status.`);
    return render(done);
  };

  const snapshot = async (definition: string | undefined, names: string[]) => {
    const p = await backend.getInputs(definition);
    return { inputs: p.inputs ?? [], original: Object.fromEntries((p.inputs ?? []).filter((i: any) => names.includes(i.name)).map((i: any) => [i.name, i.value])) };
  };

  server.registerTool(
    "design_explore",
    {
      title: "Explore the design space",
      description:
        `Generate variants under urban constraints and physical simulations. Samples ${engine} parameters (full grid, random or Latin ` +
        "hypercube over the input ranges); each design is recomputed and measured, checked against the urban rules ('rules'), then — if it " +
        "passes — simulated ('simulations': sun hours, ANSYS Workbench project, PyAnsys/journal script) and checked against " +
        `'simulation_constraints'. Results: table, Pareto front on the objectives (${engine}, rule and simulation metrics), best design, ` +
        "CSV/JSON in 'explorations'; save='best'|'pareto'|'all' stores them as variants with image and simulation results. Background job.",
      inputSchema: {
        definition: z.string().optional().describe(backend.terms.definition),
        space: z.record(z.string(), DimensionSchema).describe("Parameters to vary: {\"Building_Height\": {min: 12, max: 30, steps: 4}, \"Floors\": {}}"),
        method: z.enum(["grid", "random", "lhs"]).optional().describe("Default grid (3 levels) for ≤ 3 parameters, otherwise lhs"),
        samples: z.number().int().positive().optional().describe("random/lhs: number of designs (default 20)"),
        seed: z.number().int().optional(),
        objectives: Objectives.optional(),
        weights: z.record(z.string(), z.number()).optional(),
        rules: RulesOption,
        ...SimulationOptions,
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
        const evaluate = await makeEvaluator(ctx, args, h);
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
          if (args.restore !== false && Object.keys(original).length) await restore(args.definition, original);
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
          const { record } = await createVariant(backend, ctx.variants, ctx.config, {
            definition: args.definition,
            name: `explore ${i + 1}${i === best ? " best" : ""}`,
            parameters: evals[i].params,
            image: { width: 960, height: 600 },
            extra: { exploration: { design: i + 1, feasible: evals[i].feasible, score: scores[i], pareto: pareto.includes(i) }, simulations: evals[i].simulations ?? [] },
          });
          record.metrics = { ...record.metrics, ...evals[i].metrics };
          await ctx.variants.save(record);
          saved.push(summarize(record));
        }
        if (toSave.length && args.restore !== false && Object.keys(original).length) await restore(args.definition, original);
        const file = await saveExploration(ctx, "explore", { method, dims, objectives, simulations: args.simulations ?? [], simulation_constraints: args.simulation_constraints ?? [], rows, pareto: pareto.map((i) => i + 1), best: best !== undefined ? best + 1 : null }, rows);
        const constrained = (args.simulation_constraints ?? []).map((c) => c.metric).filter((m) => !(m in objectives));
        const constrainedOrRules = args.rules || args.simulations?.length || constrained.length;
        const columns = ["design", ...dims.map((d) => d.name), ...Object.keys(objectives), ...constrained, ...(constrainedOrRules ? ["feasible"] : []), ...(Object.keys(objectives).length ? ["score", "pareto"] : [])];
        return {
          method,
          evaluated: rows.length,
          feasible: feasibleIdx.length,
          simulated: evals.filter((e) => e.simulations?.length).length,
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
        return ok({ ...r, table: undefined }, `${r.table}\n\n${r.feasible}/${r.evaluated} feasible designs${r.simulated ? `, ${r.simulated} simulated` : ""}.${r.best ? ` Best: design ${r.best.design}.` : ""}`);
      });
    }),
  );

  server.registerTool(
    "design_optimize",
    {
      title: "Optimise the design",
      description:
        `Genetic optimisation (Galapagos-like) of ${engine} parameters towards objectives, with urban rules and physical simulations ` +
        "as constraints (infeasible designs are ranked last); objectives may use simulation metrics (e.g. maximise solar.sun_hours_mean). " +
        "Each generation recomputes (and simulates) the design 'population' times. The best design is applied and saved as a variant " +
        "with its image and simulation results. Background job: follow with job_status.",
      inputSchema: {
        definition: z.string().optional().describe(backend.terms.definition),
        space: z.record(z.string(), DimensionSchema),
        objectives: Objectives,
        weights: z.record(z.string(), z.number()).optional(),
        rules: RulesOption,
        ...SimulationOptions,
        population: z.number().int().min(4).max(50).optional().describe("Default 8"),
        generations: z.number().int().min(1).max(50).optional().describe("Default 5"),
        mutation: z.number().min(0).max(1).optional(),
        seed: z.number().int().optional(),
        apply_best: z.boolean().optional().describe(`Leave the best design applied in ${engine} (default true)`),
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
        const evaluate = await makeEvaluator(ctx, args, h);
        const result = await optimize(dims, evaluate, { objectives: args.objectives as Record<string, "max" | "min">, weights: args.weights, population: pop, generations: gens, mutation: args.mutation, seed: args.seed }, {
          progress: (d, t, m) => h.update(d, m),
          checkCancelled: () => h.checkCancelled(),
        });
        let variant = null;
        if (result.best) {
          const { record } = await createVariant(backend, ctx.variants, ctx.config, {
            definition: args.definition,
            name: "optimum",
            parameters: result.best.params,
            extra: { optimization: { score: result.best.score, feasible: result.best.feasible, generation: result.best.generation }, simulations: result.best.simulations ?? [] },
          });
          record.metrics = { ...record.metrics, ...result.best.metrics };
          await ctx.variants.save(record);
          variant = summarize(record);
        }
        if (args.apply_best === false && Object.keys(original).length) await restore(args.definition, original);
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

