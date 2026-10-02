import { z } from "zod";

/** [x, y] or [x, y, z] in model units. */
export const Point = z
  .union([z.array(z.number()).min(2).max(3), z.object({ x: z.number(), y: z.number(), z: z.number().optional() })])
  .describe("Point [x, y, z] in model units");

export const Vector = z
  .union([z.array(z.number()).min(2).max(3), z.enum(["x", "-x", "y", "-y", "z", "-z", "up", "down"])])
  .describe("Vector [x, y, z] or an axis name");

export const Plane = z
  .union([
    z.enum(["xy", "yz", "zx", "xz"]),
    z.object({ origin: Point.optional(), normal: Vector.optional(), x_axis: Vector.optional(), y_axis: Vector.optional() }),
  ])
  .describe("Plane: 'xy' | 'yz' | 'zx' or {origin, normal} or {origin, x_axis, y_axis}");

export const Color = z
  .union([z.string(), z.array(z.number()).min(3).max(4)])
  .describe("'#RRGGBB', a colour name, or [r, g, b]");

/** Object selection shared by get/delete/transform/select/export. All criteria combine with AND. */
export const FilterShape = {
  ids: z.array(z.string()).optional().describe("Object GUIDs"),
  layer: z.string().optional().describe("Layer full path ('Parent::Child') or unique layer name"),
  include_sublayers: z.boolean().optional().describe("Include objects of sub-layers (default true)"),
  types: z
    .array(z.string())
    .optional()
    .describe("point, curve, surface, brep, extrusion, mesh, subd, text_dot, annotation, block_instance, point_cloud, hatch"),
  name: z.string().optional().describe("Object name, wildcards * and ? allowed"),
  user_text: z
    .record(z.string(), z.string())
    .optional()
    .describe("User text that must be present: {key: value}; value '*' = any value; wildcards allowed"),
  selected: z.boolean().optional().describe("true = only selected objects"),
  include_hidden: z.boolean().optional().describe("Include hidden objects (default true)"),
  include_locked: z.boolean().optional().describe("Include locked objects (default true)"),
};

export const Filter = z.object(FilterShape).describe("Object filter (same fields as rhino_get_objects)");

export const GEOMETRY_TYPES = [
  "point", "points", "line", "polyline", "curve", "circle", "arc", "ellipse", "rectangle", "polygon",
  "surface", "planar_surface", "box", "sphere", "cylinder", "cone", "extrusion", "loft", "pipe",
  "mesh", "text_dot", "text", "brep", "json",
] as const;

export const GeometrySpec = z
  .looseObject({
    type: z.enum(GEOMETRY_TYPES),
    layer: z.string().optional(),
    name: z.string().optional(),
    color: Color.optional(),
    user_text: z.record(z.string(), z.any()).optional(),
    group: z.string().optional(),
  })
  .describe("Geometry spec: 'type' plus the fields of that type (see tool description)");

export const GEOMETRY_HELP = `Geometry specs (coordinates in model units, angles in degrees):
- point {location} · points {points:[[x,y,z],…]}
- line {from, to} · polyline {points, closed?}
- curve {points, degree?=3, interpolate?=true, closed?} (control_points instead of points = control-point curve)
- circle {center, radius, normal?|plane?} · arc {center, radius, start_angle, end_angle, plane?} or {start, mid, end}
- ellipse {center, radius_x, radius_y, plane?} · rectangle {corner, width, height, plane?} or {center, width, height} or {corner_a, corner_b}
- polygon {center, radius, sides, rotation?}
- surface {corners:[p1,p2,p3,p4]} or {points, u_count, v_count, degree?} · planar_surface {points (closed boundary) | curve_ids}
- box {corner, size:[dx,dy,dz]} or {center, size} or {min, max} · sphere {center, radius}
- cylinder {base, radius, height, axis?, cap?=true} · cone {base, radius, height, axis?}
- extrusion {profile:[[x,y],…] (footprint, closed automatically) | curve_id, height | direction:[x,y,z], cap?=true}  ← buildings from footprints
- loft {sections:[[points…],[points…]] | curve_ids, closed?, cap?} · pipe {points | curve_id, radius}
- mesh {vertices:[[x,y,z],…], faces:[[i,j,k] | [i,j,k,l],…], vertex_colors?}
- text_dot {location, text} · text {location, text, height?}
- brep / json {json: RhinoCommon / rhino3dm JSON}
Every spec also accepts: layer ('A::B', created if missing), name, color, user_text {key: value}, group.`;
