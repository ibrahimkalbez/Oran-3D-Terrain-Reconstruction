import path from "node:path";
import { z } from "zod";
import type { ToolContext } from "../context.js";
import { guarded, ok, withImage } from "../util/result.js";
import { diffMetrics } from "../variants/compare.js";

const READ = { readOnlyHint: true, openWorldHint: false } as const;
const WRITE = { readOnlyHint: false, destructiveHint: false, openWorldHint: false } as const;

const Definition = z
  .string()
  .optional()
  .describe("Which open definition (file name, path or id). Default: the active one");

export const ParameterChange = z.object({
  parameter: z.string().describe("Input name (slider nickname…) or id"),
  value: z.any().optional().describe("New value; for relative modes the amount"),
  mode: z
    .enum(["set", "add", "multiply", "percent", "toggle"])
    .optional()
    .describe("set (default) | add (+value) | multiply (×value) | percent (+value %) | toggle (booleans)"),
  on_out_of_range: z
    .enum(["extend", "clamp", "error"])
    .optional()
    .describe("Slider value outside its range: extend the range (default), clamp, or fail"),
});

export function registerGrasshopperTools(ctx: ToolContext): void {
  const { server, bridge, config } = ctx;
  const long = { timeoutMs: config.longTimeoutMs };

  server.registerTool(
    "grasshopper_open_definition",
    {
      title: "Open a Grasshopper definition",
      description:
        "Open a .gh/.ghx file (Grasshopper is started if needed), make it active, solve it and list its parameters. " +
        "An already open file is reused unless reload=true.",
      inputSchema: {
        path: z.string().describe("Absolute path of the .gh or .ghx file"),
        show_editor: z.boolean().optional().describe("Show the Grasshopper window (default false)"),
        reload: z.boolean().optional(),
        solve: z.boolean().optional().describe("Solve after opening (default true)"),
      },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("grasshopper.open_definition", args, long))),
  );

  server.registerTool(
    "grasshopper_list_definitions",
    {
      title: "List open Grasshopper definitions",
      description: "Grasshopper status: loaded or not, editor visible, solver enabled, and the open definitions (active one flagged).",
      inputSchema: {},
      annotations: READ,
    },
    guarded(async () => ok(await bridge.call("grasshopper.status"))),
  );

  server.registerTool(
    "grasshopper_get_definition",
    {
      title: "Read the Grasshopper graph",
      description:
        "Components, parameters, sliders, panels, groups and wires of a definition: for every object its id, kind, " +
        "name/nickname, position, inputs/outputs (type, access, sources), errors/warnings, preview state; plus the " +
        "list of connections and the exposed parameters.",
      inputSchema: {
        definition: Definition,
        max_objects: z.number().int().positive().optional().describe("Default 400"),
        include_groups: z.boolean().optional(),
      },
      annotations: READ,
    },
    guarded(async (args) => ok(await bridge.call("grasshopper.get_definition", args, long))),
  );

  server.registerTool(
    "grasshopper_get_parameters",
    {
      title: "Read the definition parameters",
      description:
        "Exposed inputs (sliders with min/max/decimals, toggles, value lists with items, panels, number/text/point " +
        "parameters) with their current value and what they feed, and the outputs with a preview of their data. " +
        "Inputs are found automatically; outputs are parameters named 'OUT_…' or grouped in an 'OUTPUT(S)'/'RESULTS' group, " +
        "otherwise terminal parameters and panels.",
      inputSchema: { definition: Definition, include_outputs: z.boolean().optional() },
      annotations: READ,
    },
    guarded(async (args) => ok(await bridge.call("grasshopper.get_parameters", args, long))),
  );

  server.registerTool(
    "grasshopper_set_parameter",
    {
      title: "Change parameters and re-solve",
      description:
        "Set one parameter ({parameter, value}) or several at once (parameters: [...]) then re-solve. Relative changes: " +
        "mode 'percent' (+10 % → value 10), 'add', 'multiply'. Sliders keep their integer/even/odd type and their range " +
        "is extended when needed (reported). Returns old → new values, solution time, errors/warnings, the outputs, and " +
        "how every metric changed (before/after/delta/%) so you can explain the effect.",
      inputSchema: {
        definition: Definition,
        parameter: z.string().optional(),
        value: z.any().optional(),
        mode: ParameterChange.shape.mode,
        on_out_of_range: ParameterChange.shape.on_out_of_range,
        parameters: z.array(ParameterChange).optional().describe("Several changes applied together, solved once"),
        solve: z.boolean().optional().describe("Re-solve after the change (default true)"),
        compare_results: z.boolean().optional().describe("Measure metrics before and after (default true)"),
      },
      annotations: WRITE,
    },
    guarded(async (args) => {
      const { compare_results = true, ...rest } = args;
      const solve = args.solve !== false;
      if (!args.parameters?.length && !args.parameter) throw new Error("Give 'parameter' and 'value', or 'parameters'.");
      let before: Record<string, unknown> | undefined;
      if (solve && compare_results) {
        try {
          before = (await bridge.call("grasshopper.get_results", { definition: args.definition, max_items: 0 }, long)).metrics;
        } catch {
          before = undefined;
        }
      }
      const res = await bridge.call("grasshopper.set_parameter", rest, long);
      if (solve && compare_results) {
        const after = (await bridge.call("grasshopper.get_results", { definition: args.definition, max_items: 0 }, long)).metrics;
        res.metrics_change = diffMetrics(before, after);
        res.metrics = after;
      }
      const lines = (res.changes as any[]).map((c) => {
        let line = `${c.name}: ${JSON.stringify(c.old)} → ${JSON.stringify(c.new)}`;
        if (c.range_extended) line += ` (slider range extended to ${JSON.stringify(c.range_extended)})`;
        if (c.clamped) line += " (clamped to the slider range)";
        return line;
      });
      if (res.solution?.solved) {
        lines.push(
          `Solved in ${res.solution.duration_ms} ms — ${res.solution.errors.length} error(s), ${res.solution.warnings.length} warning(s).`,
        );
      }
      return ok(res, lines.join("\n"));
    }),
  );

  server.registerTool(
    "grasshopper_solve",
    {
      title: "Solve the definition",
      description: "Force a new solution (expire_all=true recomputes every component). Returns duration, errors, warnings and outputs.",
      inputSchema: { definition: Definition, expire_all: z.boolean().optional() },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("grasshopper.solve", args, long))),
  );

  server.registerTool(
    "grasshopper_get_results",
    {
      title: "Read the results",
      description:
        "Numbers, texts and geometry produced by the outputs (or by the named outputs/parameters, e.g. 'Area' or " +
        "'Extrude.Extrusion'). Numbers come with statistics; geometry with count, types, total length/area/volume and " +
        "bounding box. 'metrics' gathers every numeric result for comparisons. include_items lists items one by one; " +
        "include_geometry_json adds RhinoCommon JSON.",
      inputSchema: {
        definition: Definition,
        outputs: z.array(z.string()).optional(),
        include_items: z.boolean().optional(),
        include_geometry_json: z.boolean().optional(),
        max_items: z.number().int().min(0).max(10000).optional().describe("Items listed per output (default 50)"),
        solve: z.boolean().optional().describe("Solve first"),
      },
      annotations: READ,
    },
    guarded(async (args) => ok(await bridge.call("grasshopper.get_results", args, long))),
  );

  server.registerTool(
    "grasshopper_search_components",
    {
      title: "Find Grasshopper components",
      description: "Search the installed Grasshopper components (built-in and plug-ins) by name or description.",
      inputSchema: { query: z.string(), limit: z.number().int().positive().max(100).optional() },
      annotations: READ,
    },
    guarded(async (args) => ok(await bridge.call("grasshopper.search_components", args))),
  );

  server.registerTool(
    "grasshopper_create_component",
    {
      title: "Add a component to the definition",
      description:
        "Add a component or parameter by name ('Number Slider', 'Panel', 'Extrude', 'Area', 'Series'…) or component_guid. " +
        "Placed at position [x, y], next to 'near' (an object id) or right of the existing graph. Sliders take min/max/" +
        "value/integer/decimals, panels text, toggles value, value lists items. Returns the id and the inputs/outputs to connect.",
      inputSchema: {
        definition: Definition,
        name: z.string().optional(),
        component_guid: z.string().optional(),
        category: z.string().optional().describe("Disambiguate homonyms (e.g. 'Maths', 'Surface')"),
        nickname: z.string().optional(),
        position: z.array(z.number()).length(2).optional(),
        near: z.string().optional(),
        min: z.number().optional(),
        max: z.number().optional(),
        value: z.any().optional(),
        integer: z.boolean().optional(),
        decimals: z.number().int().min(0).max(12).optional(),
        text: z.string().optional(),
        items: z.array(z.union([z.string(), z.object({ name: z.string(), value: z.any().optional() })])).optional(),
        solve: z.boolean().optional(),
      },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("grasshopper.create_component", args, long))),
  );

  const Endpoint = z.object({
    id: z.string().optional().describe("Object id (or component parameter id)"),
    name: z.string().optional().describe("Object nickname if no id"),
  });

  server.registerTool(
    "grasshopper_connect_components",
    {
      title: "Wire two components",
      description:
        "Connect an output to an input. source {id|name, output: name or index}, target {id|name, input: name or index}. " +
        "mode connect (add a wire), replace (remove the other wires into that input) or disconnect.",
      inputSchema: {
        definition: Definition,
        source: Endpoint.extend({ output: z.union([z.string(), z.number().int()]).optional() }),
        target: Endpoint.extend({ input: z.union([z.string(), z.number().int()]).optional() }),
        mode: z.enum(["connect", "replace", "disconnect"]).optional(),
        solve: z.boolean().optional(),
      },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("grasshopper.connect_components", args, long))),
  );

  server.registerTool(
    "grasshopper_export_geometry",
    {
      title: "Bake / export Grasshopper geometry",
      description:
        "Bake the output geometry into Rhino (one sub-layer per output under 'layer', default 'Grasshopper::<definition>'), " +
        "with user text on every object. bake_tag + replace=true replaces what was baked earlier with the same tag " +
        "(no duplicates when re-baking). With file_path the geometry is written to a .3dm file instead. " +
        "source='preview' takes everything Grasshopper previews instead of the outputs.",
      inputSchema: {
        definition: Definition,
        outputs: z.array(z.string()).optional(),
        source: z.enum(["outputs", "preview"]).optional(),
        layer: z.string().optional(),
        layer_per_output: z.boolean().optional(),
        user_text: z.record(z.string(), z.any()).optional(),
        bake_tag: z.string().optional(),
        replace: z.boolean().optional(),
        group: z.boolean().optional(),
        file_path: z.string().optional().describe(".3dm file (relative paths go into the workspace 'exports' folder)"),
      },
      annotations: WRITE,
    },
    guarded(async (args) => {
      const params = { ...args };
      if (params.file_path && !path.isAbsolute(params.file_path)) params.file_path = path.join(config.workspace, "exports", params.file_path);
      return ok(await bridge.call("grasshopper.export_geometry", params, long));
    }),
  );

  server.registerTool(
    "grasshopper_save_definition",
    {
      title: "Save the definition",
      description: "Save the definition, or save a copy under 'path' (copy=true by default when a path is given).",
      inputSchema: { definition: Definition, path: z.string().optional(), copy: z.boolean().optional() },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("grasshopper.save_definition", args, long))),
  );

  server.registerTool(
    "grasshopper_close_definition",
    {
      title: "Close a definition",
      description: "Close an open definition. Refused when it has unsaved changes unless discard_changes=true.",
      inputSchema: { definition: Definition, discard_changes: z.boolean().optional() },
      annotations: { readOnlyHint: false, destructiveHint: true, openWorldHint: false },
    },
    guarded(async (args) => ok(await bridge.call("grasshopper.close_definition", args))),
  );

  server.registerTool(
    "grasshopper_capture_canvas",
    {
      title: "Image of the Grasshopper canvas",
      description: "Picture of the whole Grasshopper graph (components and wires), returned as an image.",
      inputSchema: {
        definition: Definition,
        width: z.number().int().min(200).max(4096).optional(),
        height: z.number().int().min(200).max(4096).optional(),
        save_path: z.string().optional(),
      },
      annotations: READ,
    },
    guarded(async (args) => {
      const res = await bridge.call("grasshopper.capture_canvas", args, long);
      return withImage(res, `Grasshopper canvas (${res.width}×${res.height}).`);
    }),
  );
}
