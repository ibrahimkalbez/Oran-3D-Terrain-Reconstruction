import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import type { ToolContext } from "../context.js";
import { isFinished, jobView } from "../util/jobs.js";
import { guarded, ok, progress, toText } from "../util/result.js";
import { compareVariants, range, sweepCombinations } from "../variants/compare.js";
import type { VariantRecord } from "../variants/store.js";
import { createVariant, normalizeChanges, readPreview, summarize, type CreateVariantOptions } from "../variants/workflow.js";
import { ParameterChange } from "./grasshopper.js";

const READ = { readOnlyHint: true, openWorldHint: false } as const;
const WRITE = { readOnlyHint: false, destructiveHint: false, openWorldHint: false } as const;


const ImageOptions = z
  .object({
    width: z.number().int().min(64).max(4096).optional(),
    height: z.number().int().min(64).max(4096).optional(),
    direction: z.string().optional().describe("Camera (top, iso_sw, perspective…). Default: keep the current view so all variants share the same framing"),
    display_mode: z.string().optional(),
    format: z.enum(["png", "jpg"]).optional(),
    view: z.string().optional(),
  })
  .optional();

const Parameters = z
  .union([z.array(ParameterChange), z.record(z.string(), z.any())])
  .describe("Changes: {\"Building_Height\": 15} or [{parameter, value, mode}]");

const Objectives = z
  .record(z.string(), z.enum(["max", "min"]))
  .optional()
  .describe("Ranking objectives on metric names, e.g. {\"Floor_Area\": \"max\", \"Shadow_Area\": \"min\"}");

const SweepValue = z.union([
  z.array(z.any()).min(1),
  z.object({ min: z.number(), max: z.number(), steps: z.number().int().min(1) }),
  z.object({ start: z.number(), stop: z.number(), step: z.number() }),
]);

export function registerVariantTools(ctx: ToolContext): void {
  const { server, config, variants: store, jobs, backend } = ctx;
  const T = backend.terms;
  const Definition = z
    .string()
    .optional()
    .describe(backend.kind === "revit" ? "Dynamo graph (.dyn path or name) and/or 'globals' (global parameters). Default: the last graph used" : "Definition (file name, path or id). Default: the active one");

  const variantResult = (record: VariantRecord, image: { data: string; mimeType: string } | undefined, summary: string): CallToolResult => {
    const content: CallToolResult["content"] = [];
    if (image) content.push({ type: "image", data: image.data, mimeType: image.mimeType });
    content.push({ type: "text", text: `${summary}\n\n${toText(record)}` });
    return { content };
  };

  server.registerTool(
    "variant_create",
    {
      title: "Create and save a variant",
      description:
        `Apply parameter changes (optional), recompute with ${T.engine}, then save the variant: all input values, metrics ` +
        `(numeric outputs plus model quantities: areas, volumes, counts), an image, the geometry (${T.geometryFile}) and ` +
        `optionally a copy of the ${T.definition}${backend.bake ? ", or a bake into the model on 'Variants::<definition>::<id>'" : ""}. ` +
        "Variants are numbered V01, V02… per definition and stored in the workspace folder. Returns the image so you can show it.",
      inputSchema: {
        definition: Definition,
        name: z.string().optional().describe("Short name (default: built from the changes)"),
        description: z.string().optional(),
        parameters: Parameters.optional(),
        outputs: z.array(z.string()).optional().describe("Outputs to measure/export (default: the definition outputs)"),
        capture: z.boolean().optional().describe("Viewport image (default true)"),
        image: ImageOptions,
        save_geometry: z.boolean().optional().describe(`Write ${T.geometryFile} (default true)`),
        save_definition: z.boolean().optional().describe(`Save a copy of the ${T.definition} with these values (default false)`),
        bake: z.boolean().optional().describe("Also keep the geometry in the model (Rhino only, default false)"),
      },
      annotations: WRITE,
    },
    guarded(async (args) => {
      const { record, image } = await createVariant(backend, store, config, args as CreateVariantOptions);
      const errors = record.solution?.errors?.length ?? 0;
      return variantResult(record, image, `Saved ${record.definition.key}/${record.id} "${record.name}"${errors ? ` — ${errors} ${T.engine} error(s)` : ""}. Folder: ${record.dir}`);
    }),
  );

  server.registerTool(
    "variant_generate",
    {
      title: "Generate a series of variants",
      description:
        "Create several variants in one go, from an explicit list (variants: [{name, parameters}]) or a sweep " +
        "(sweep: {\"Height\": [12, 15, 18]} or {\"Height\": {min, max, steps}}; several parameters = every combination). " +
        "Each variant is solved, measured, captured and saved; then all are compared (and ranked if objectives are given). " +
        "By default the original parameter values are restored at the end. Runs as a background job: if it takes longer " +
        "than wait_seconds you get a job id — call job_status until it is done.",
      inputSchema: {
        definition: Definition,
        variants: z.array(z.object({ name: z.string().optional(), parameters: Parameters })).optional(),
        sweep: z.record(z.string(), SweepValue).optional(),
        name_prefix: z.string().optional(),
        outputs: z.array(z.string()).optional(),
        capture: z.boolean().optional(),
        image: ImageOptions,
        save_geometry: z.boolean().optional(),
        save_definition: z.boolean().optional(),
        bake: z.boolean().optional(),
        restore: z.boolean().optional().describe("Restore the original parameter values at the end (default true)"),
        objectives: Objectives,
        max_variants: z.number().int().positive().max(500).optional().describe("Safety limit (default 50)"),
        wait_seconds: z.number().int().min(0).max(240).optional().describe("How long to wait before returning a job id (default 50)"),
        return_images: z.number().int().min(0).max(12).optional().describe("Images returned when done (default 6)"),
      },
      annotations: WRITE,
    },
    guarded(async (args, extra) => {
      const plan: Array<{ name?: string; parameters: CreateVariantOptions["parameters"] }> = [];
      for (const v of args.variants ?? []) plan.push(v);
      if (args.sweep) {
        const sweep: Record<string, unknown[]> = {};
        for (const [k, v] of Object.entries(args.sweep)) sweep[k] = Array.isArray(v) ? v : range(v as any);
        for (const combo of sweepCombinations(sweep)) plan.push({ parameters: combo });
      }
      if (plan.length === 0) throw new Error("Give 'variants' or 'sweep'.");
      const max = args.max_variants ?? 50;
      if (plan.length > max) throw new Error(`${plan.length} variants requested (> max_variants=${max}). Reduce the sweep or raise max_variants.`);

      const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
      const touched = new Set(plan.flatMap((p) => normalizeChanges(p.parameters).map((c) => norm(c.parameter))));
      let waiting = true; // progress notifications only while the tool call is still open
      const job = jobs.start("variants", `${plan.length} variants`, plan.length, async (h) => {
        let original: Record<string, unknown> = {};
        if (args.restore !== false) {
          const params = await backend.getInputs(args.definition);
          for (const input of params.inputs ?? []) if (touched.has(norm(input.name)) || touched.has(norm(input.id))) original[input.id] = input.value;
        }
        const created: VariantRecord[] = [];
        const images: Array<{ id: string; data: string; mimeType: string }> = [];
        try {
          for (let i = 0; i < plan.length; i++) {
            h.checkCancelled();
            const item = plan[i];
            h.update(i, `variant ${i + 1}/${plan.length}`);
            const name = item.name ?? (args.name_prefix ? `${args.name_prefix} ${i + 1}` : undefined);
            const { record, image } = await createVariant(backend, store, config, {
              definition: args.definition,
              name,
              parameters: item.parameters,
              outputs: args.outputs,
              capture: args.capture,
              image: args.image ?? { width: 960, height: 600 },
              save_geometry: args.save_geometry,
              save_definition: args.save_definition,
              bake: args.bake,
            });
            created.push(record);
            if (image) images.push({ id: record.id, ...image });
            h.log(`${record.id} ${record.name} saved`);
            if (waiting) await progress(extra, i + 1, plan.length, `${record.id} ${record.name}`);
          }
        } finally {
          if (Object.keys(original).length > 0) {
            try {
              await backend.apply(args.definition, Object.entries(original).map(([parameter, value]) => ({ parameter, value })));
              h.log("original parameters restored");
            } catch (err) {
              h.log(`could not restore parameters: ${(err as Error).message}`);
            }
          }
        }
        h.update(plan.length, "done");
        const comparison = compareVariants(created, { objectives: args.objectives });
        return { created: created.map(summarize), comparison, images };
      });

      const finished = await jobs.waitFor(job.id, (args.wait_seconds ?? 50) * 1000);
      waiting = false;
      if (!isFinished(finished)) {
        return ok(jobView(finished), `Job ${job.id} is running (${finished.progress.done}/${finished.progress.total}). Call job_status with this id to follow it.`);
      }
      return jobResult(finished, args.return_images ?? 6);
    }),
  );

  const jobResult = (job: any, maxImages: number): CallToolResult => {
    if (job.status !== "done") return ok(jobView(job), `Job ${job.id} ${job.status}${job.error ? `: ${job.error}` : ""}.`);
    const { images = [], ...result } = job.result ?? {};
    const content: CallToolResult["content"] = [];
    for (const img of images.slice(0, maxImages)) {
      content.push({ type: "text", text: `Variant ${img.id}:` });
      content.push({ type: "image", data: img.data, mimeType: img.mimeType });
    }
    const table = result.comparison?.table ? `${result.comparison.table}\n\n` : "";
    content.push({ type: "text", text: `${table}${toText({ job: jobView({ ...job, result: undefined }, false), ...result })}` });
    return { content };
  };

  server.registerTool(
    "job_status",
    {
      title: "Follow background jobs",
      description: "Status, progress and (when finished) the result of a background job (variant series, simulations). Without job_id: list all jobs.",
      inputSchema: {
        job_id: z.string().optional(),
        wait_seconds: z.number().int().min(0).max(240).optional().describe("Wait up to this long for the job to finish (default 20)"),
        return_images: z.number().int().min(0).max(12).optional(),
      },
      annotations: READ,
    },
    guarded(async (args) => {
      if (!args.job_id) return ok(jobs.list().map((j) => jobView(j, false)));
      const job = await jobs.waitFor(args.job_id, (args.wait_seconds ?? 20) * 1000);
      if (!isFinished(job)) return ok(jobView(job), `Job ${job.id} is ${job.status} (${job.progress.done}/${job.progress.total}).`);
      return jobResult(job, args.return_images ?? 6);
    }),
  );

  server.registerTool(
    "job_cancel",
    {
      title: "Cancel a background job",
      description: "Stop a running job after its current step (the variants already saved are kept).",
      inputSchema: { job_id: z.string() },
      annotations: WRITE,
    },
    guarded(async (args) => ok(jobView(jobs.cancel(args.job_id), false), "Cancellation requested.")),
  );

  server.registerTool(
    "variant_list",
    {
      title: "List saved variants",
      description: "Saved variants (all definitions, or one), with their changes, metrics, kept flag (★) and folder.",
      inputSchema: { definition: z.string().optional().describe("Definition key (file name without .gh)"), kept_only: z.boolean().optional() },
      annotations: READ,
    },
    guarded(async (args) => {
      let list = await store.list(args.definition);
      if (args.kept_only) list = list.filter((v) => v.kept);
      if (list.length === 0) return ok({ variants: [], workspace: store.root }, "No variants saved yet.");
      return ok({ variants: list.map(summarize), workspace: store.root }, `${list.length} variant(s).`);
    }),
  );

  server.registerTool(
    "variant_get",
    {
      title: "Show one variant",
      description: "Everything about one variant ('V03', '3', or its name): parameters, metrics, outputs, files — with its image (\"Voici la variante 03\").",
      inputSchema: { variant: z.string(), definition: z.string().optional(), include_image: z.boolean().optional() },
      annotations: READ,
    },
    guarded(async (args) => {
      const record = await store.find(args.variant, args.definition);
      const image = args.include_image === false ? undefined : await readPreview(record);
      return variantResult(record, image, `${record.definition.key}/${record.id} "${record.name}"${record.kept ? " ★ kept" : ""}`);
    }),
  );

  server.registerTool(
    "variant_compare",
    {
      title: "Compare variants",
      description:
        "Side-by-side table of variants: parameters that differ, metrics with % difference from the baseline (first " +
        "variant unless 'baseline'), and a ranking when objectives are given ({metric: 'max'|'min'}, optional weights).",
      inputSchema: {
        definition: z.string().optional(),
        variants: z.array(z.string()).optional().describe("Variant ids/names (default: all of the definition)"),
        metrics: z.array(z.string()).optional(),
        baseline: z.string().optional(),
        objectives: Objectives,
        weights: z.record(z.string(), z.number()).optional(),
      },
      annotations: READ,
    },
    guarded(async (args) => {
      let list: VariantRecord[];
      if (args.variants?.length) {
        list = [];
        for (const ref of args.variants) list.push(await store.find(ref, args.definition));
      } else {
        list = await store.list(args.definition);
        if (!args.definition) {
          const defs = new Set(list.map((v) => v.definition.key));
          if (defs.size > 1) throw new Error(`Variants of several definitions exist (${[...defs].join(", ")}): pass 'definition'.`);
        }
      }
      const comparison = compareVariants(list, { metrics: args.metrics, baseline: args.baseline, objectives: args.objectives, weights: args.weights });
      return ok(comparison, comparison.table + (comparison.best ? `\n\nBest: ${comparison.best.id} ${comparison.best.name} (score ${comparison.best.score})` : ""));
    }),
  );

  server.registerTool(
    "variant_apply",
    {
      title: `Restore a variant (${T.engine})`,
      description: `Put the parameter values of a saved variant back into the ${T.definition} and recompute, to continue working from it.`,
      inputSchema: { variant: z.string(), definition: z.string().optional(), solve: z.boolean().optional() },
      annotations: WRITE,
    },
    guarded(async (args) => {
      const record = await store.find(args.variant, args.definition);
      const definition = args.definition ?? (backend.kind === "revit" ? record.definition.path ?? undefined : undefined);
      const current = await backend.getInputs(definition);
      const names = new Set((current.inputs ?? []).map((i: any) => i.name));
      const changes = Object.entries(record.parameters)
        .filter(([name]) => names.has(name))
        .map(([parameter, value]) => ({ parameter, value }));
      const skipped = Object.keys(record.parameters).filter((n) => !names.has(n));
      const res = await backend.apply(definition, changes);
      return ok({ ...(res.raw as object), solution: res.solution, skipped }, `Applied ${record.id} "${record.name}" (${changes.length} parameter(s)${skipped.length ? `, ${skipped.length} not found` : ""}).`);
    }),
  );

  server.registerTool(
    "variant_keep",
    {
      title: "Keep (star) a variant",
      description: "Mark a variant as kept (★) or not, with an optional note — kept variants are protected from variant_delete unkept_only.",
      inputSchema: { variant: z.string(), definition: z.string().optional(), keep: z.boolean().optional(), note: z.string().optional() },
      annotations: WRITE,
    },
    guarded(async (args) => {
      const record = await store.find(args.variant, args.definition);
      record.kept = args.keep !== false;
      if (args.note !== undefined) record.note = args.note;
      await store.save(record);
      return ok(summarize(record), `${record.id} "${record.name}" ${record.kept ? "kept ★" : "no longer kept"}.`);
    }),
  );

  server.registerTool(
    "variant_delete",
    {
      title: "Delete variants",
      description: "Delete saved variants (their folders). unkept_only=true deletes every variant of the definition that is not kept ★. Confirm with the user first.",
      inputSchema: {
        variants: z.array(z.string()).optional(),
        definition: z.string().optional(),
        unkept_only: z.boolean().optional(),
      },
      annotations: { readOnlyHint: false, destructiveHint: true, openWorldHint: false },
    },
    guarded(async (args) => {
      let targets: VariantRecord[] = [];
      if (args.variants?.length) for (const ref of args.variants) targets.push(await store.find(ref, args.definition));
      else if (args.unkept_only) {
        if (!args.definition) throw new Error("unkept_only needs 'definition'.");
        targets = (await store.list(args.definition)).filter((v) => !v.kept);
      } else throw new Error("Give 'variants' or unkept_only with 'definition'.");
      for (const t of targets) await store.remove(t);
      return ok({ deleted: targets.map((t) => `${t.definition.key}/${t.id} ${t.name}`) }, `${targets.length} variant(s) deleted.`);
    }),
  );
}
