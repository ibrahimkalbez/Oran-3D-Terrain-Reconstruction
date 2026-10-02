import fs from "node:fs/promises";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import { isFinished, jobView } from "../../../rhino-grasshopper-mcp/src/util/jobs.js";
import { guarded, ok, toText } from "../../../rhino-grasshopper-mcp/src/util/result.js";
import { READ, SourceSchema, WRITE, type FusionContext } from "../context.js";
import { openRing } from "../geometry/polygon.js";
import { loadBuildings, type Source } from "../site.js";
import { adapter, ADAPTERS } from "../sim/registry.js";
import type { SimCase } from "../sim/store.js";
import { ORAN, sunPath } from "../sim/sun.js";
import { windDomain } from "../sim/wind.js";

export function registerSimTools(ctx: FusionContext): void {
  const { server, bridge, config, jobs, sims } = ctx;
  const actx = { bridge, config };

  /** Links a case to a saved variant: its parameters feed the solver, its metrics receive the results. */
  async function linkVariant(c: SimCase, ref?: string) {
    if (!ref) return;
    const record = await ctx.variants.find(ref);
    c.variant = { definition: record.definition.key, id: record.id };
    c.settings._variant_parameters = record.parameters;
  }

  async function storeOnVariant(c: SimCase) {
    if (!c.variant || !c.metrics) return;
    const record = await ctx.variants.find(c.variant.id, c.variant.definition);
    for (const [k, v] of Object.entries(c.metrics)) record.metrics[`${c.solver}.${k}`] = v;
    const sims = ((record as any).simulations ??= []) as Array<{ id: string; solver: string; status: string }>;
    sims.push({ id: c.id, solver: c.solver, status: c.status });
    await ctx.variants.save(record);
  }

  async function caseResult(c: SimCase, summary: string, withImages = true): Promise<CallToolResult> {
    const content: CallToolResult["content"] = [];
    if (withImages) {
      for (const img of c.images ?? []) {
        try {
          content.push({ type: "image", data: (await fs.readFile(img)).toString("base64"), mimeType: img.endsWith(".jpg") ? "image/jpeg" : "image/png" });
        } catch {
          // image removed
        }
      }
    }
    const { settings, ...rest } = c;
    const { _variant_parameters, ...publicSettings } = settings ?? {};
    content.push({ type: "text", text: `${summary}\n\n${toText({ ...rest, settings: publicSettings })}` });
    return { content };
  }

  server.registerTool(
    "sim_solvers",
    {
      title: "Available simulation solvers",
      description:
        "Solvers of the simulation manager with their settings and whether they are available on this computer: solar (native sun hours / " +
        "shadows), ansys_workbench (updates a validated ANSYS Workbench project: Fluent wind, Mechanical, thermal…), command (PyFluent/PyMAPDL " +
        "scripts, Fluent/MAPDL journals, any solver).",
      inputSchema: {},
      annotations: READ,
    },
    guarded(async () => {
      const list = [];
      for (const a of ADAPTERS) list.push({ id: a.id, title: a.title, description: a.description, settings: a.settings, status: await a.check(actx) });
      return ok({ solvers: list, workspace: sims.root });
    }),
  );

  const CaseInput = {
    solver: z.string().describe("solar | ansys_workbench | command"),
    name: z.string().optional(),
    settings: z.record(z.string(), z.any()).optional().describe("Solver settings (see sim_solvers)"),
    variant: z.string().optional().describe("Saved variant to simulate: its parameters feed the solver and the results are added to its metrics"),
  };

  server.registerTool(
    "sim_create",
    {
      title: "Prepare a simulation",
      description:
        "Create a simulation case and prepare its inputs (geometry export, sun path, Workbench journal, parameters.json) without running " +
        "it, so the inputs and the command can be reviewed. Run it with sim_run {case_id}.",
      inputSchema: CaseInput,
      annotations: WRITE,
    },
    guarded(async (args) => {
      const a = adapter(args.solver);
      const c = await sims.create(a.id, args.name ?? a.id, args.settings ?? {});
      await linkVariant(c, args.variant);
      try {
        await a.prepare(actx, c);
        c.status = "prepared";
      } catch (err) {
        c.status = "failed";
        c.error = (err as Error).message;
      }
      await sims.save(c);
      if (c.status === "failed") throw new Error(`Preparation failed: ${c.error} (case ${c.id})`);
      return caseResult(c, `Case ${c.id} prepared in ${c.dir}.${c.command ? `\nCommand: ${c.command.join(" ")}` : ""}`, false);
    }),
  );

  server.registerTool(
    "sim_run",
    {
      title: "Run a simulation",
      description:
        "Run a prepared case (case_id) or create-and-run one (solver + settings). Runs as a background job; results come back as metrics " +
        "(and images), are stored in the case folder and, for a variant, added to the variant metrics as '<solver>.<metric>' so that " +
        "variant_compare can rank variants on simulation results.",
      inputSchema: { case_id: z.string().optional(), ...CaseInput, solver: CaseInput.solver.optional(), wait_seconds: z.number().int().min(0).max(240).optional() },
      annotations: WRITE,
    },
    guarded(async (args) => {
      let c: SimCase;
      if (args.case_id) c = await sims.get(args.case_id);
      else {
        if (!args.solver) throw new Error("Give case_id or solver.");
        c = await sims.create(adapter(args.solver).id, args.name ?? args.solver, args.settings ?? {});
        await linkVariant(c, args.variant);
        c.status = "queued";
      }
      const a = adapter(c.solver);
      const needsPrepare = !args.case_id;
      const job = jobs.start("simulation", `${c.solver} ${c.id}`, 3, async (h) => {
        try {
          if (needsPrepare) {
            h.update(0, "preparing");
            await a.prepare(actx, c);
          }
          c.status = "running";
          await sims.save(c);
          h.update(1, "running");
          if (a.run) await a.run(actx, c, h);
          h.update(2, "collecting results");
          await a.collect(actx, c);
          c.status = "done";
          await storeOnVariant(c);
        } catch (err) {
          c.status = h.job.cancelRequested ? "cancelled" : "failed";
          c.error = (err as Error).message;
          throw err;
        } finally {
          await sims.save(c);
        }
        h.update(3, "done");
        return { case_id: c.id };
      });
      c.job_id = job.id;
      await sims.save(c);
      const done = await jobs.waitFor(job.id, (args.wait_seconds ?? 50) * 1000);
      if (!isFinished(done)) return ok({ case_id: c.id, job: jobView(done) }, `Simulation ${c.id} running: follow it with sim_status.`);
      const fresh = await sims.get(c.id);
      if (fresh.status !== "done") throw new Error(`Simulation ${fresh.status}: ${fresh.error ?? done.error ?? ""} (case ${c.id}, log ${fresh.log_file ?? "—"})`);
      return caseResult(fresh, `Simulation ${c.id} done.`);
    }),
  );

  server.registerTool(
    "sim_status",
    {
      title: "Simulation status",
      description: "Status, progress and the end of the log of a simulation case; returns the results when it is finished.",
      inputSchema: { case_id: z.string(), wait_seconds: z.number().int().min(0).max(240).optional() },
      annotations: READ,
    },
    guarded(async (args) => {
      let c = await sims.get(args.case_id);
      if (c.job_id && jobs.get(c.job_id)) {
        await jobs.waitFor(c.job_id, (args.wait_seconds ?? 15) * 1000);
        c = await sims.get(args.case_id);
      }
      if (c.status === "done") return caseResult(c, `Simulation ${c.id} done.`);
      let tail = "";
      if (c.log_file) {
        try {
          tail = (await fs.readFile(c.log_file, "utf8")).split(/\r?\n/).slice(-25).join("\n");
        } catch {
          tail = "";
        }
      }
      const job = c.job_id ? jobs.get(c.job_id) : undefined;
      return ok({ id: c.id, status: c.status, error: c.error, job: job ? jobView(job, false) : null, log_tail: tail }, `Simulation ${c.id}: ${c.status}.`);
    }),
  );

  server.registerTool(
    "sim_results",
    {
      title: "Simulation results",
      description: "Metrics, detailed results, files and images of a finished simulation case.",
      inputSchema: { case_id: z.string(), include_images: z.boolean().optional() },
      annotations: READ,
    },
    guarded(async (args) => {
      const c = await sims.get(args.case_id);
      return caseResult(c, `Simulation ${c.id} (${c.status}).`, args.include_images !== false);
    }),
  );

  server.registerTool(
    "sim_list",
    {
      title: "List simulations",
      description: "Simulation cases of the workspace, newest first, with solver, status, linked variant and main metrics.",
      inputSchema: { solver: z.string().optional(), limit: z.number().int().positive().optional() },
      annotations: READ,
    },
    guarded(async (args) => {
      const list = (await sims.list()).filter((c) => !args.solver || c.solver === args.solver).slice(0, args.limit ?? 30);
      return ok(list.map((c) => ({ id: c.id, name: c.name, solver: c.solver, status: c.status, created_at: c.created_at, variant: c.variant, metrics: c.metrics, error: c.error })));
    }),
  );

  server.registerTool(
    "sim_cancel",
    {
      title: "Cancel a simulation",
      description: "Stop a running simulation (the solver process is terminated).",
      inputSchema: { case_id: z.string() },
      annotations: WRITE,
    },
    guarded(async (args) => {
      const c = await sims.get(args.case_id);
      if (!c.job_id || !jobs.get(c.job_id)) throw new Error("This case is not running in this session.");
      jobs.cancel(c.job_id);
      return ok({ id: c.id }, "Cancellation requested.");
    }),
  );

  server.registerTool(
    "sim_sun_path",
    {
      title: "Sun positions",
      description: "Sun azimuth and elevation (and the vectors used for shadows) for given days at the site. Default: Oran, UTC+1.",
      inputSchema: {
        dates: z.array(z.string()).min(1).describe("YYYY-MM-DD"),
        latitude: z.number().optional(),
        longitude: z.number().optional(),
        utc_offset: z.number().optional(),
        step_minutes: z.number().int().positive().optional(),
        north: z.array(z.number()).length(2).optional(),
      },
      annotations: READ,
    },
    guarded(async (args) => {
      const samples = sunPath({ ...args, north: args.north as [number, number] | undefined, step_minutes: args.step_minutes ?? 60 });
      const byDay: Record<string, { sunrise: string; sunset: string; max_elevation: number; daylight_hours: number }> = {};
      for (const s of samples) {
        const day = s.local_time.slice(0, 10);
        const d = (byDay[day] ??= { sunrise: s.local_time.slice(11), sunset: s.local_time.slice(11), max_elevation: 0, daylight_hours: 0 });
        d.sunset = s.local_time.slice(11);
        d.max_elevation = Math.max(d.max_elevation, s.elevation);
        d.daylight_hours += s.weight_hours;
      }
      return ok({ site: { latitude: args.latitude ?? ORAN.latitude, longitude: args.longitude ?? ORAN.longitude }, days: byDay, samples });
    }),
  );

  server.registerTool(
    "sim_wind_domain",
    {
      title: "Wind simulation domain",
      description:
        "Size the computational domain of an urban wind (CFD) study from the buildings and the wind direction, following best-practice " +
        "guidelines (5 H upstream and on the sides, 15 H downstream, 6 H high, blockage < 3 %). Optionally draws the domain in Rhino " +
        "(layer 'Analysis::Wind domain') to build the ANSYS project geometry.",
      inputSchema: {
        buildings: SourceSchema.describe("Buildings (layer, ids, filter or Grasshopper outputs)"),
        direction_deg: z.number().describe("Direction the wind comes FROM, degrees clockwise from north (0 = north wind)"),
        north: z.array(z.number()).length(2).optional(),
        upstream: z.number().optional(),
        downstream: z.number().optional(),
        lateral: z.number().optional(),
        top: z.number().optional(),
        draw: z.boolean().optional(),
      },
      annotations: WRITE,
    },
    guarded(async (args) => {
      const buildings = await loadBuildings(bridge, args.buildings as Source, config.longTimeoutMs);
      if (buildings.length === 0) throw new Error("No buildings found.");
      const pts = buildings.flatMap((b) => b.parts.flatMap((p) => openRing(p.outer)));
      const hMax = Math.max(...buildings.map((b) => b.top_z - Math.min(...buildings.map((x) => x.base_z))));
      const groundZ = Math.min(...buildings.map((b) => b.base_z));
      const domain = windDomain(pts, hMax, groundZ, args.direction_deg, args.north as [number, number] | undefined, args);
      let drawn: unknown = null;
      if (args.draw) {
        await bridge.call("rhino.delete_objects", { user_text: { "mcp.kind": "wind_domain" }, max_count: 100 }).catch(() => undefined);
        drawn = await bridge.call("rhino.create_geometry", {
          geometries: [
            { type: "extrusion", profile: domain.corners.map((c) => [c[0], c[1], c[2]]), height: domain.height, cap: true, name: `Wind domain ${args.direction_deg}°`, layer: "Analysis::Wind domain", user_text: { "mcp.kind": "wind_domain" } },
            { type: "line", from: domain.inlet_edge[0], to: domain.inlet_edge[1], name: "Inlet", layer: "Analysis::Wind domain", color: "#E30613", user_text: { "mcp.kind": "wind_domain" } },
          ],
        });
      }
      return ok({ buildings: buildings.length, ...domain, drawn }, domain.blockage_ok ? "Domain OK (blockage < 3 %)." : "Blockage ratio ≥ 3 %: enlarge the lateral/top distances.");
    }),
  );
}
