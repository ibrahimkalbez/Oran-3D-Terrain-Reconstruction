import path from "node:path";
import { z } from "zod";
import type { ToolContext } from "../../../rhino-grasshopper-mcp/src/context.js";
import { guarded, ok, withImage } from "../../../rhino-grasshopper-mcp/src/util/result.js";
import { Color, ELEMENT_HELP, FilterShape, GEOMETRY_HELP, GeometrySpec, ParameterValueChange } from "./schemas.js";

const READ = { readOnlyHint: true, openWorldHint: false } as const;
const WRITE = { readOnlyHint: false, destructiveHint: false, openWorldHint: false } as const;
const DESTRUCTIVE = { readOnlyHint: false, destructiveHint: true, openWorldHint: false } as const;

const Target = {
  ids: z.array(z.string()).optional().describe("Element ids"),
  filter: z.object(FilterShape).optional().describe("Or a filter (categories, layer, name, level, user_text…)"),
};

export function registerRevitTools(ctx: ToolContext): void {
  const { server, bridge, config } = ctx;
  const long = { timeoutMs: config.longTimeoutMs };

  server.registerTool(
    "revit_get_document",
    {
      title: "Read the Revit project",
      description:
        "Overview of the open Revit project: file, element count by category, bounding box (m), pseudo-layers (categories, " +
        "line styles, connector layers), levels with elevations, views, selection, links, global parameters, project " +
        "information, location (latitude/longitude, true north, base points) and Dynamo availability. Call this first. " +
        "All values are in SI (m, m², m³, °), coordinates are internal project coordinates in meters.",
      inputSchema: { max_layers: z.number().int().positive().optional() },
      annotations: READ,
    },
    guarded(async (args) => ok(await bridge.call("revit.get_document", args))),
  );

  server.registerTool(
    "revit_get_objects",
    {
      title: "Find Revit elements",
      description:
        "Search elements by id, category, pseudo-layer, name, family, type, level, connector data or selection. Returns totals " +
        "by category and layer and a page of elements. detail='summary' (id, category, name, family, type, level), 'full' " +
        "(+ bounding box, location, area/volume, comments, mark) or 'parameters' (+ every parameter with SI values).",
      inputSchema: {
        ...FilterShape,
        detail: z.enum(["summary", "full", "parameters"]).optional(),
        limit: z.number().int().positive().max(5000).optional().describe("Page size (default 200)"),
        offset: z.number().int().min(0).optional(),
      },
      annotations: READ,
    },
    guarded(async (args) => ok(await bridge.call("revit.get_objects", args))),
  );

  server.registerTool(
    "revit_get_parameters",
    {
      title: "Read element parameters",
      description:
        "Instance and type parameters of the selected elements, with SI values (m, m², m³, °), units, storage type, read-only " +
        "flag and Revit's display string. names=[…] limits to some parameters.",
      inputSchema: {
        ...FilterShape,
        names: z.array(z.string()).optional(),
        include_type: z.boolean().optional().describe("Also the type parameters (default true)"),
        include_empty: z.boolean().optional(),
        limit: z.number().int().positive().max(2000).optional().describe("Elements returned (default 50)"),
      },
      annotations: READ,
    },
    guarded(async (args) => ok(await bridge.call("revit.get_parameters", args))),
  );

  server.registerTool(
    "revit_set_parameters",
    {
      title: "Change element parameters",
      description:
        "Set instance (or type) parameters on elements — one undoable Revit transaction. values {\"Unconnected Height\": 12} or " +
        "changes [{name, value, mode: set|add|multiply|percent, target: instance|type}]. Values in SI: lengths in m, areas " +
        "m², angles °; Yes/No as true/false; levels, materials and types by name. Returns before → after for each change.",
      inputSchema: {
        ...Target,
        values: z.record(z.string(), z.any()).optional(),
        changes: z.array(ParameterValueChange).optional(),
        target: z.enum(["instance", "type"]).optional().describe("type = change the type (every element of that type)"),
        max_count: z.number().int().positive().optional(),
      },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("revit.set_parameters", args, long))),
  );

  server.registerTool(
    "revit_get_global_parameters",
    {
      title: "Read global parameters",
      description: "The project's global parameters (the 'sliders' of a parametric Revit model): value in SI, unit, formula, reporting flag.",
      inputSchema: { names: z.array(z.string()).optional() },
      annotations: READ,
    },
    guarded(async (args) => ok(await bridge.call("revit.get_global_parameters", args))),
  );

  server.registerTool(
    "revit_set_global_parameters",
    {
      title: "Change global parameters",
      description:
        "Change global parameters (absolute or relative: mode add/multiply/percent) — Revit regenerates every element driven " +
        "by them. create=true creates missing ones (create_type length|area|volume|angle|number|integer|yesno|text). " +
        "Parameters driven by a formula are refused: change their inputs.",
      inputSchema: {
        values: z.record(z.string(), z.any()).optional(),
        changes: z
          .array(z.object({ name: z.string(), value: z.any(), mode: z.enum(["set", "add", "multiply", "percent"]).optional(), create_type: z.string().optional() }))
          .optional(),
        create: z.boolean().optional(),
      },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("revit.set_global_parameters", args, long))),
  );

  server.registerTool(
    "revit_get_types",
    {
      title: "List family types",
      description: "Loaded family types and system types (wall, floor, roof, level types…) by category — the names to use in revit_create_elements.",
      inputSchema: { categories: z.array(z.string()).optional(), name: z.string().optional(), limit: z.number().int().positive().optional() },
      annotations: READ,
    },
    guarded(async (args) => ok(await bridge.call("revit.get_types", args))),
  );

  server.registerTool(
    "revit_create_elements",
    {
      title: "Create native Revit elements",
      description: `Create levels, grids, walls, floors, roofs, family instances, model lines, rooms, masses and terrain in one undoable transaction. If one element fails, nothing is created.\n${ELEMENT_HELP}`,
      inputSchema: {
        elements: z.array(z.looseObject({ kind: z.enum(["level", "grid", "wall", "floor", "roof", "family_instance", "model_line", "room", "mass", "terrain"]) })),
        defaults: z.record(z.string(), z.any()).optional().describe("Fields applied to every element (level, type, height…)"),
      },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("revit.create_elements", args, long))),
  );

  server.registerTool(
    "revit_create_geometry",
    {
      title: "Create free-form geometry",
      description: `Create geometry as DirectShape elements (sketches, massing, analysis meshes, trees…) in one undoable transaction.\n${GEOMETRY_HELP}`,
      inputSchema: {
        geometries: z.array(GeometrySpec).min(1),
        defaults: z.record(z.string(), z.any()).optional(),
        select: z.boolean().optional(),
      },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("revit.create_geometry", args, long))),
  );

  server.registerTool(
    "revit_transform_objects",
    {
      title: "Move / rotate / mirror / copy elements",
      description:
        "Transform elements: operations [{operation: translate (vector [dx,dy,dz] m or from/to), rotate (angle °, axis?, origin? or " +
        "anchor center|bottom|top), mirror (normal, origin?)}]. copy=true or copies=N makes arrays (each copy offset again).",
      inputSchema: {
        ...FilterShape,
        operations: z.array(z.looseObject({ operation: z.enum(["translate", "move", "rotate", "mirror"]) })).min(1),
        copy: z.boolean().optional(),
        copies: z.number().int().min(1).max(1000).optional(),
      },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("revit.transform_objects", args, long))),
  );

  server.registerTool(
    "revit_delete_objects",
    {
      title: "Delete elements",
      description:
        "Delete the elements matching ids or a filter (dependent elements go too). Refuses an empty filter; dry_run=true lists " +
        "what would be deleted; max_count protects against broad filters. Confirm with the user before deleting their elements.",
      inputSchema: { ...FilterShape, dry_run: z.boolean().optional(), max_count: z.number().int().positive().optional() },
      annotations: DESTRUCTIVE,
    },
    guarded(async (args) => ok(await bridge.call("revit.delete_objects", args, long))),
  );

  server.registerTool(
    "revit_set_object_data",
    {
      title: "Attach names and data",
      description:
        "Store connector data on elements (user_text kept in Revit extensible storage), a pseudo-layer, the name (when Revit allows it), " +
        "Comments and Mark. target='document' stores user_text on Project Information.",
      inputSchema: {
        ...Target,
        target: z.enum(["elements", "document"]).optional(),
        user_text: z.record(z.string(), z.any()).optional().describe("Values; null deletes a key"),
        layer: z.string().optional(),
        name: z.string().optional(),
        comments: z.string().optional(),
        mark: z.string().optional(),
      },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("revit.set_object_data", args))),
  );

  server.registerTool(
    "revit_select_objects",
    {
      title: "Select elements in Revit",
      description: "Select (replace/add/remove/clear) elements in the Revit window, optionally zooming to them.",
      inputSchema: { ...FilterShape, mode: z.enum(["replace", "add", "remove", "clear"]).optional(), zoom: z.boolean().optional() },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("revit.select_objects", args))),
  );

  server.registerTool(
    "revit_capture_view",
    {
      title: "Image of the Revit model",
      description:
        "Image of a view, returned so you can show it. Give 'view' (name) to export an existing view, or a direction " +
        "(top, front, back, left, right, iso_sw, iso_se, iso_ne, iso_nw, aerial) for a temporary 3D view that leaves no trace in " +
        "the project; zoom {filter} frames elements with a section box; highlight {ids|filter, color} colours elements; " +
        "display_mode shaded, shaded_edges, realistic, hidden, wireframe, consistent. save_path stores the image.",
      inputSchema: {
        view: z.string().optional(),
        direction: z.string().optional(),
        display_mode: z.string().optional(),
        width: z.number().int().min(200).max(4096).optional(),
        zoom: z.union([z.enum(["extents"]), z.array(z.string()), z.object(FilterShape)]).optional(),
        highlight: z.object({ ids: z.array(z.string()).optional(), filter: z.object(FilterShape).optional(), color: Color.optional() }).optional(),
        format: z.enum(["png", "jpg"]).optional(),
        save_path: z.string().optional(),
      },
      annotations: READ,
    },
    guarded(async (args) => {
      const res = await bridge.call("revit.capture_viewport", args, long);
      return withImage(res, `View '${res.view}' (${res.width}×${res.height}).`);
    }),
  );

  server.registerTool(
    "revit_export",
    {
      title: "Export elements",
      description:
        "Export the selected elements (default: buildings and site) to a file whose extension picks the format: .stl / .obj " +
        "(triangulated, meters — simulation meshes, 3D printing), .sat (ACIS solids for ANSYS SpaceClaim/DesignModeler/Discovery), " +
        ".ifc, .dwg, .fbx. A relative path goes to the workspace 'exports' folder.",
      inputSchema: { path: z.string(), ...FilterShape, ascii: z.boolean().optional().describe("STL: ASCII instead of binary") },
      annotations: WRITE,
    },
    guarded(async (args) => {
      const p = path.isAbsolute(args.path) ? args.path : path.join(config.workspace, "exports", args.path);
      return ok(await bridge.call("revit.export", { ...args, path: p }, long));
    }),
  );

  server.registerTool(
    "revit_metrics",
    {
      title: "Quantities",
      description:
        "Counts, areas (m²), volumes (m³), lengths (m) and heights grouped by category, level, type, family, layer, " +
        "'user_text:<key>' or 'parameter:<name>'; plus floor area per level, mass floor area and rooms. Default selection: " +
        "buildings and site elements.",
      inputSchema: { ...FilterShape, group_by: z.string().optional().describe("category (default) | level | type | family | layer | none | user_text:<key> | parameter:<name>") },
      annotations: READ,
    },
    guarded(async (args) => ok(await bridge.call("revit.metrics", args, long))),
  );

  server.registerTool(
    "revit_save_document",
    {
      title: "Save the project",
      description: "Save the project, save it under a new path (path), write a copy (copy=true), or synchronise a workshared model (synchronize=true).",
      inputSchema: { path: z.string().optional(), copy: z.boolean().optional(), synchronize: z.boolean().optional(), comment: z.string().optional() },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("revit.save_document", args, long))),
  );

  server.registerTool(
    "revit_open_document",
    {
      title: "Open a project",
      description: "Open and activate a Revit project (.rvt) — the current one stays open in Revit.",
      inputSchema: { path: z.string() },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("revit.open_document", args, long))),
  );

  server.registerTool(
    "revit_bridge_status",
    {
      title: "Connection status",
      description:
        "Diagnose the connection: running Revit sessions with the add-in (pid, port, project), the one in use, the add-in version, " +
        "Dynamo availability and the available bridge methods. select_pid switches to another Revit session.",
      inputSchema: { select_pid: z.number().int().optional() },
      annotations: READ,
    },
    guarded(async (args) => {
      if (args.select_pid) await bridge.select(args.select_pid);
      const instances = (await bridge.instances()).map(({ token, ...rest }) => rest);
      let info: unknown = null;
      let error: string | undefined;
      try {
        info = await bridge.call("bridge.info", {}, { timeoutMs: 15000 });
      } catch (err) {
        error = (err as Error).message;
      }
      const current = bridge.current;
      return ok({
        connected: info !== null,
        error,
        endpoint: current ? { host: current.host, port: current.port, pid: current.pid, document: current.document, source: current.source } : null,
        info,
        instances,
        workspace: config.workspace,
        discovery_folders: config.bridgeDirs,
      });
    }),
  );
}

