import path from "node:path";
import { z } from "zod";
import type { ToolContext } from "../context.js";
import { guarded, ok, withImage } from "../util/result.js";
import { Color, FilterShape, GEOMETRY_HELP, GeometrySpec, Plane, Point, Vector } from "./schemas.js";

const READ = { readOnlyHint: true, openWorldHint: false } as const;
const WRITE = { readOnlyHint: false, destructiveHint: false, openWorldHint: false } as const;
const DESTRUCTIVE = { readOnlyHint: false, destructiveHint: true, openWorldHint: false } as const;

export const TransformOperation = z
  .looseObject({
    operation: z.enum(["translate", "move", "rotate", "scale", "mirror", "orient", "matrix"]),
    vector: Vector.optional().describe("translate: displacement"),
    from: z.any().optional().describe("translate: start point · orient: source plane"),
    to: z.any().optional().describe("translate: end point · orient: target plane"),
    angle: z.number().optional().describe("rotate: degrees, counter-clockwise around axis"),
    axis: Vector.optional().describe("rotate: axis (default z)"),
    factor: z.number().optional().describe("scale: uniform factor"),
    factors: z.array(z.number()).length(3).optional().describe("scale: [sx, sy, sz] non-uniform"),
    origin: Point.optional().describe("rotate/scale/mirror: fixed point (default: anchor)"),
    anchor: z.enum(["center", "bottom", "top", "min", "world_origin"]).optional().describe("Default origin taken from the selection bounding box (default center; 'bottom' keeps buildings on the ground)"),
    plane: Plane.optional().describe("mirror: mirror plane"),
    normal: Vector.optional().describe("mirror: plane normal through origin"),
    from_points: z.array(Point).optional().describe("orient: 3 reference points"),
    to_points: z.array(Point).optional().describe("orient: 3 target points"),
    matrix: z.array(z.array(z.number()).length(4)).length(4).optional().describe("matrix: 4×4 row-major"),
  })
  .describe("One transformation step");

export function registerRhinoTools(ctx: ToolContext): void {
  const { server, bridge, config } = ctx;

  server.registerTool(
    "rhino_get_document",
    {
      title: "Read the Rhino document",
      description:
        "Overview of the open Rhino model: file name and path, units, tolerances, object count by type, bounding box, " +
        "layers (with object counts), blocks, materials, selected objects, views, document user text, " +
        "earth anchor (georeferencing) and Grasshopper status. Call this first to understand the model.",
      inputSchema: { max_layers: z.number().int().positive().optional().describe("Maximum layers listed (default 500)") },
      annotations: READ,
    },
    guarded(async (args) => ok(await bridge.call("rhino.get_document", args))),
  );

  server.registerTool(
    "rhino_get_objects",
    {
      title: "Find Rhino objects",
      description:
        "Search objects by GUID, layer, type, name (wildcards), user text or selection. Returns totals by type and layer " +
        "and a page of objects. detail='summary' (id, type, name, layer), 'full' (+ bounding box, area/volume/length, " +
        "user text, colour) or 'geometry' (+ RhinoCommon JSON). Use limit/offset to page through large models.",
      inputSchema: {
        ...FilterShape,
        detail: z.enum(["summary", "full", "geometry"]).optional().describe("Level of detail (default summary)"),
        limit: z.number().int().positive().max(5000).optional().describe("Page size (default 200)"),
        offset: z.number().int().min(0).optional(),
      },
      annotations: READ,
    },
    guarded(async (args) => ok(await bridge.call("rhino.get_objects", args))),
  );

  server.registerTool(
    "rhino_create_geometry",
    {
      title: "Create Rhino geometry",
      description:
        "Create one or many objects in a single undoable step. If any spec is invalid nothing is created.\n" + GEOMETRY_HELP,
      inputSchema: {
        geometries: z.array(GeometrySpec).min(1).describe("Geometry specs"),
        defaults: z
          .looseObject({ layer: z.string().optional(), color: Color.optional(), user_text: z.record(z.string(), z.any()).optional(), group: z.string().optional() })
          .optional()
          .describe("Attributes applied to every spec unless the spec overrides them"),
        select: z.boolean().optional().describe("Select the new objects"),
      },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("rhino.create_geometry", args))),
  );

  server.registerTool(
    "rhino_transform_objects",
    {
      title: "Move / rotate / scale / mirror / orient objects",
      description:
        "Apply one or more transformations (in order) to the selected objects. copy=true keeps the originals; " +
        "copies=N makes an array of N copies, each one step further. Rotation angles are in degrees. " +
        "Example: raise buildings by 10% keeping them on the ground → {operation:'scale', factors:[1,1,1.1], anchor:'bottom'}.",
      inputSchema: {
        ...FilterShape,
        operations: z.array(TransformOperation).min(1),
        copy: z.boolean().optional(),
        copies: z.number().int().positive().max(10000).optional(),
      },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("rhino.transform_objects", args))),
  );

  server.registerTool(
    "rhino_delete_objects",
    {
      title: "Delete Rhino objects",
      description:
        "Delete objects by ids or filter (one undoable step; the user can Undo in Rhino). A filter matching more " +
        "than max_count objects is refused. Use dry_run=true first when deleting by filter and confirm with the user.",
      inputSchema: {
        ...FilterShape,
        dry_run: z.boolean().optional().describe("Only list what would be deleted"),
        max_count: z.number().int().positive().optional().describe("Safety limit for filter-based deletes (default 1000)"),
      },
      annotations: DESTRUCTIVE,
    },
    guarded(async (args) => ok(await bridge.call("rhino.delete_objects", args))),
  );

  server.registerTool(
    "rhino_update_object",
    {
      title: "Modify object properties or geometry",
      description:
        "Change properties of objects chosen by ids or filter: name, layer, color ('by_layer' to reset), material, " +
        "visible, locked. With exactly one object, 'geometry' (a geometry spec) replaces its geometry and keeps its id.",
      inputSchema: {
        ids: z.array(z.string()).optional(),
        filter: z.object(FilterShape).optional(),
        properties: z
          .object({
            name: z.string().optional(),
            layer: z.string().optional(),
            color: z.union([Color, z.literal("by_layer")]).optional(),
            material: z.string().optional(),
            visible: z.boolean().optional(),
            locked: z.boolean().optional(),
          })
          .optional(),
        geometry: GeometrySpec.optional(),
      },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("rhino.update_object", args))),
  );

  server.registerTool(
    "rhino_create_layer",
    {
      title: "Create layers",
      description:
        "Create a layer (parents are created automatically: 'Urban::Buildings::Housing'), or update an existing one " +
        "(color, visibility, lock, current). Pass 'layers' to create several at once.",
      inputSchema: {
        path: z.string().optional().describe("Full layer path, e.g. 'Urban::Buildings'"),
        color: Color.optional(),
        visible: z.boolean().optional(),
        locked: z.boolean().optional(),
        current: z.boolean().optional().describe("Make it the current layer"),
        material: z.string().optional(),
        layers: z
          .array(z.object({ path: z.string(), color: Color.optional(), visible: z.boolean().optional(), locked: z.boolean().optional(), current: z.boolean().optional() }))
          .optional(),
      },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("rhino.create_layer", args))),
  );

  server.registerTool(
    "rhino_set_object_data",
    {
      title: "Attach names, user text and metadata",
      description:
        "Write data on objects (ids or filter) or on the document (target='document'): name, user_text {key: value, " +
        "null deletes}, metadata (JSON object merged into the 'mcp.metadata' user text) and parameters " +
        "(stored as 'param.<name>' user text, visible in Rhino's Attribute User Text panel).",
      inputSchema: {
        target: z.enum(["objects", "document"]).optional(),
        ids: z.array(z.string()).optional(),
        filter: z.object(FilterShape).optional(),
        name: z.string().optional(),
        user_text: z.record(z.string(), z.any()).optional(),
        metadata: z.record(z.string(), z.any()).optional(),
        parameters: z.record(z.string(), z.any()).optional(),
      },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("rhino.set_object_data", args))),
  );

  server.registerTool(
    "rhino_select_objects",
    {
      title: "Select objects in Rhino",
      description: "Select objects for the user (mode replace/add/remove/clear), optionally zooming on them.",
      inputSchema: {
        ...FilterShape,
        mode: z.enum(["replace", "add", "remove", "clear"]).optional(),
        zoom: z.boolean().optional(),
      },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("rhino.select_objects", args))),
  );

  server.registerTool(
    "rhino_capture_viewport",
    {
      title: "Capture the Rhino viewport",
      description:
        "Screenshot of a Rhino view, returned as an image. Draws the current Grasshopper result too (preview='auto'). " +
        "Choose the camera with direction (top, front, back, left, right, iso_sw, iso_se, iso_ne, iso_nw, perspective, aerial) " +
        "or camera {location, target, lens}; display_mode e.g. Shaded, Rendered, Arctic, Technical, Pen, Ghosted; " +
        "zoom extents/selected/preview or a filter. highlight {ids|filter, color} draws objects in colour. " +
        "The user's view is restored afterwards unless restore=false. save_path stores the image on disk.",
      inputSchema: {
        view: z.string().optional().describe("View name (default: active view)"),
        width: z.number().int().min(64).max(4096).optional(),
        height: z.number().int().min(64).max(4096).optional(),
        display_mode: z.string().optional(),
        direction: z.string().optional(),
        projection: z.enum(["parallel", "perspective"]).optional(),
        lens: z.number().optional(),
        camera: z.object({ location: Point, target: Point, lens: z.number().optional() }).optional(),
        zoom: z.union([z.enum(["none", "extents", "selected", "preview"]), z.array(z.string()), z.object(FilterShape)]).optional(),
        preview: z.enum(["auto", "grasshopper", "none"]).optional(),
        outputs: z.array(z.string()).optional().describe("Only draw these Grasshopper outputs"),
        highlight: z.object({ ...FilterShape, color: Color.optional() }).optional(),
        transparent_background: z.boolean().optional(),
        format: z.enum(["png", "jpg"]).optional(),
        save_path: z.string().optional(),
        restore: z.boolean().optional(),
      },
      annotations: READ,
    },
    guarded(async (args) => {
      const res = await bridge.call("rhino.capture_viewport", args, { timeoutMs: config.longTimeoutMs });
      return withImage(res, `Viewport '${res.view}' (${res.width}×${res.height}, ${res.display_mode}).`);
    }),
  );

  server.registerTool(
    "rhino_run_command",
    {
      title: "Run a Rhino command macro",
      description:
        "Run a Rhino command macro as typed on the command line, e.g. \"_-Export \\\"C:\\\\out\\\\model.dwg\\\" _Enter\" or " +
        "\"_SelLayer Buildings _Enter\". Use scripted (dash) forms so no dialog opens. Returns created objects. " +
        "Prefer the dedicated tools when one exists.",
      inputSchema: {
        command: z.string().min(1),
        echo: z.boolean().optional().describe("Echo the macro on the command line"),
      },
      annotations: DESTRUCTIVE,
    },
    guarded(async (args) => ok(await bridge.call("rhino.run_command", args, { timeoutMs: config.longTimeoutMs }))),
  );

  server.registerTool(
    "rhino_export",
    {
      title: "Export objects to a file",
      description:
        "Export objects (filter, or everything visible) to a file whose format follows the extension: .3dm (with layers " +
        "and attributes), .stl, .obj, .step/.stp, .igs, .dwg, .dxf, .fbx, .3mf, .ply… Relative paths go into the workspace folder.",
      inputSchema: {
        path: z.string().describe("Destination file"),
        ...FilterShape,
        version: z.number().int().optional().describe(".3dm version (default 8)"),
      },
      annotations: WRITE,
    },
    guarded(async (args) => {
      const p = path.isAbsolute(args.path) ? args.path : path.join(config.workspace, "exports", args.path);
      return ok(await bridge.call("rhino.export", { ...args, path: p }, { timeoutMs: config.longTimeoutMs }));
    }),
  );

  server.registerTool(
    "rhino_save_document",
    {
      title: "Save the Rhino model",
      description: "Save the model (no path), save it under a new path, or write a copy (copy=true keeps the current file name).",
      inputSchema: { path: z.string().optional(), copy: z.boolean().optional() },
      annotations: WRITE,
    },
    guarded(async (args) => ok(await bridge.call("rhino.save_document", args, { timeoutMs: config.longTimeoutMs }))),
  );

  server.registerTool(
    "rhino_open_document",
    {
      title: "Open a Rhino model",
      description:
        "Open a .3dm file in Rhino (it replaces the current model on Windows). If the current model has unsaved changes " +
        "Rhino asks the user, unless discard_changes=true (only with the user's explicit agreement).",
      inputSchema: { path: z.string(), discard_changes: z.boolean().optional() },
      annotations: DESTRUCTIVE,
    },
    guarded(async (args) => ok(await bridge.call("rhino.open_document", args, { timeoutMs: config.longTimeoutMs }))),
  );

  server.registerTool(
    "rhino_bridge_status",
    {
      title: "Connection status",
      description:
        "Diagnose the connection: lists running Rhino instances (pid, port, document), which one is used, the bridge " +
        "version and the available bridge methods. select_pid switches to another Rhino instance.",
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
