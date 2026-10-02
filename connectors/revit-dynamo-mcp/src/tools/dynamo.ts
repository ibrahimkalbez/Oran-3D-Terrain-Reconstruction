import fs from "node:fs/promises";
import path from "node:path";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import type { ToolContext } from "../../../rhino-grasshopper-mcp/src/context.js";
import { guarded, ok, toText } from "../../../rhino-grasshopper-mcp/src/util/result.js";
import { normalizeChanges } from "../../../rhino-grasshopper-mcp/src/variants/workflow.js";
import type { RevitDesign } from "../backend.js";
import { listGraphs, readDyn, writeDynInputs } from "../dynamo/dyn.js";
import type { RevitConfig } from "../settings.js";

const READ = { readOnlyHint: true, openWorldHint: false } as const;
const WRITE = { readOnlyHint: false, destructiveHint: false, openWorldHint: false } as const;

const InputChanges = z
  .union([
    z.record(z.string(), z.any()),
    z.array(
      z.object({
        parameter: z.string().describe("Input name as shown in Dynamo Player (or node id); 'global:<name>' for a global parameter"),
        value: z.any().optional(),
        mode: z.enum(["set", "add", "multiply", "percent", "toggle"]).optional(),
        on_out_of_range: z.enum(["extend", "clamp", "error"]).optional(),
      }),
    ),
  ])
  .describe("Inputs: {\"Hauteur\": 18, \"Niveaux\": 6} or [{parameter, value, mode}] — relative modes add/multiply/percent use the current value");

export function registerDynamoTools(ctx: ToolContext): void {
  const { server, bridge } = ctx;
  const config = ctx.config as RevitConfig;
  const design = ctx.backend as RevitDesign;

  server.registerTool(
    "dynamo_list_graphs",
    {
      title: "List Dynamo graphs",
      description:
        "Dynamo graphs (.dyn) found in the graph folders (the Dynamo Player folders set in the extension settings, plus " +
        "<workspace>/graphs) or in 'folder', with their Player inputs and outputs.",
      inputSchema: { folder: z.string().optional(), limit: z.number().int().positive().max(500).optional() },
      annotations: READ,
    },
    guarded(async (args) => {
      const folders = args.folder ? [path.resolve(args.folder)] : config.graphFolders;
      const files = await listGraphs(folders, 4, args.limit ?? 200);
      const graphs = [];
      for (const f of files) {
        try {
          const g = await readDyn(f);
          graphs.push({ path: f, name: g.name, description: g.description, inputs: g.inputs.map((i) => `${i.name} (${i.kind})`), outputs: g.outputs.map((o) => o.name) });
        } catch (err) {
          graphs.push({ path: f, error: (err as Error).message });
        }
      }
      return ok({ folders, graphs }, graphs.length ? `${graphs.length} graph(s).` : "No .dyn graph found: give 'folder' or set the graph folders in the extension settings.");
    }),
  );

  server.registerTool(
    "dynamo_get_graph",
    {
      title: "Read a Dynamo graph",
      description:
        "Inputs of a graph as Dynamo Player shows them (name, kind slider|number|toggle|text|value_list|path|selection, value, " +
        "min/max/step), its outputs, the packages it needs and the values currently applied by the connector. Call before dynamo_run.",
      inputSchema: { graph: z.string().describe("Path or name of the .dyn") },
      annotations: READ,
    },
    guarded(async (args) => {
      const file = await design.graphOf(args.graph);
      if (!file) throw new Error("Give a Dynamo graph (path or name).");
      const g = await readDyn(file);
      const applied = design.values(file);
      return ok({
        ...g,
        inputs: g.inputs.map((i) => ({ ...i, ...(i.id in applied ? { applied_value: applied[i.id] } : {}) })),
        last_run: design.lastRun(file) ? { evaluated: design.lastRun(file).evaluated, errors: design.lastRun(file).errors?.length ?? 0 } : null,
      });
    }),
  );

  const runResult = (res: any, image?: { data: string; mimeType: string }, extra?: Record<string, unknown>): CallToolResult => {
    const content: CallToolResult["content"] = [];
    if (image) content.push({ type: "image", data: image.data, mimeType: image.mimeType });
    const errors = res?.errors?.length ?? 0;
    const warnings = res?.warnings?.length ?? 0;
    const head = res
      ? `Graph ${res.evaluated ? "run" : "NOT evaluated"} in ${res.duration_s ?? "?"} s — ${errors} error(s), ${warnings} warning(s).`
      : "Done.";
    content.push({ type: "text", text: `${head}\n\n${toText({ ...extra, outputs: res?.outputs, errors: res?.errors, warnings: res?.warnings, inputs_applied: res?.inputs_applied })}` });
    return { content, ...(errors > 0 && !res?.evaluated ? { isError: true } : {}) };
  };

  server.registerTool(
    "dynamo_run",
    {
      title: "Run a Dynamo graph (Dynamo Player)",
      description:
        "Run a Dynamo graph on the open Revit project like Dynamo Player — Dynamo stays hidden, the inputs are set, the graph " +
        "runs once inside one Revit operation, and the elements it created are updated (not duplicated) on the next runs. " +
        "Inputs by name with absolute or relative values; the connector remembers the values it applied. Returns the output " +
        "and watch nodes, node errors/warnings, and an image when capture=true. reload=true re-reads the .dyn after editing it.",
      inputSchema: {
        graph: z.string().describe("Path or name of the .dyn"),
        inputs: InputChanges.optional(),
        capture: z.boolean().optional().describe("Return an image of the model after the run (default false)"),
        direction: z.string().optional().describe("Camera for the image (default iso_sw)"),
        reload: z.boolean().optional(),
      },
      annotations: WRITE,
    },
    guarded(async (args) => {
      const file = await design.graphOf(args.graph);
      if (!file) throw new Error("Give a Dynamo graph (path or name).");
      if (args.reload) design.markReload(file);
      const changes = normalizeChanges(args.inputs as any);
      const applied = changes.length ? await design.apply(file, changes) : { solution: await design.solve(file) };
      const res = design.lastRun(file);
      let image;
      if (args.capture) {
        try {
          const dir = path.join(config.workspace, "captures");
          await fs.mkdir(dir, { recursive: true });
          image = await design.capture({ image: { direction: args.direction ?? "iso_sw", width: 1280 }, file: path.join(dir, `${path.basename(file, ".dyn")}-${Date.now()}.png`) });
        } catch {
          image = undefined;
        }
      }
      const raw = (applied as any).raw ?? {};
      return runResult(res, image, { graph: file, ...(raw.global_parameters ? { global_parameters: raw.global_parameters } : {}), ...(raw.notes ? { notes: raw.notes } : {}) });
    }),
  );

  server.registerTool(
    "dynamo_status",
    {
      title: "Dynamo status",
      description: "Whether Dynamo for Revit is available, its version, the open workspace, run count and the last run; nodes=true lists every node with its value and state.",
      inputSchema: { nodes: z.boolean().optional(), max_items: z.number().int().positive().max(500).optional() },
      annotations: READ,
    },
    guarded(async (args) => {
      const status = await bridge.call("dynamo.status", {});
      if (args.nodes) {
        try {
          status.nodes = (await bridge.call("dynamo.get_workspace", { all: true, max_items: args.max_items ?? 20 })).nodes;
        } catch (err) {
          status.nodes_error = (err as Error).message;
        }
      }
      return ok(status);
    }),
  );

  server.registerTool(
    "dynamo_save_graph_copy",
    {
      title: "Save a graph with values",
      description:
        "Write a copy of a graph whose inputs hold the given values (default: the values last applied by the connector) — a " +
        "preset to share or open in Dynamo. The original .dyn is never modified.",
      inputSchema: {
        graph: z.string(),
        path: z.string().optional().describe("Destination (default: <workspace>/graphs/<name>_preset.dyn)"),
        values: z.record(z.string(), z.any()).optional().describe("{input name: value}; merged over the applied values"),
      },
      annotations: WRITE,
    },
    guarded(async (args) => {
      const file = await design.graphOf(args.graph);
      if (!file) throw new Error("Give a Dynamo graph (path or name).");
      const g = await readDyn(file);
      const values: Record<string, unknown> = design.values(file);
      for (const [name, value] of Object.entries(args.values ?? {})) {
        const input = g.inputs.find((i) => i.name === name || i.id === name.toLowerCase());
        if (!input) throw new Error(`No input '${name}'. Inputs: ${g.inputs.map((i) => i.name).join(", ")}`);
        values[input.id] = value;
      }
      const dest = path.resolve(args.path ?? path.join(config.workspace, "graphs", `${path.basename(file, ".dyn")}_preset.dyn`));
      if (path.resolve(dest).toLowerCase() === path.resolve(file).toLowerCase()) throw new Error("Choose another path: the original graph is never overwritten.");
      await fs.mkdir(path.dirname(dest), { recursive: true });
      await fs.writeFile(dest, writeDynInputs(await fs.readFile(file, "utf8"), values));
      return ok({ path: dest, values: Object.fromEntries(g.inputs.filter((i) => i.id in values).map((i) => [i.name, values[i.id]])) }, `Saved ${dest}.`);
    }),
  );
}
