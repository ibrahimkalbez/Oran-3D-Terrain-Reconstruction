import { z } from "zod";

/** Point [x, y, z] in meters (project internal coordinates). */
export const Point = z
  .union([z.array(z.number()).min(2).max(3), z.object({ x: z.number(), y: z.number(), z: z.number().optional() })])
  .describe("Point [x, y, z] in meters");

export const Color = z.union([z.string(), z.array(z.number()).min(3).max(4)]).describe("'#RRGGBB', a colour name, or [r, g, b]");

/** Element selection shared by the Revit tools. All criteria combine with AND. */
export const FilterShape = {
  ids: z.array(z.string()).optional().describe("Element ids (numbers) or UniqueIds"),
  categories: z
    .array(z.string())
    .optional()
    .describe("Categories in English, French or OST_ form: Mass/Volume, Walls/Murs, Floors/Sols, Roofs, Generic Models, Planting, Property Lines, Toposolid, Topography, Rooms, Lines…"),
  layer: z.string().optional().describe("Pseudo-layer: 'Category::Walls', 'LineStyle::Roads', or a path set by the connector (e.g. 'Vegetation::Trees')"),
  name: z.string().optional().describe("Element name, wildcards * and ? allowed"),
  family: z.string().optional().describe("Family name (wildcards)"),
  type_name: z.string().optional().describe("Type name (wildcards)"),
  level: z.string().optional().describe("Level name"),
  user_text: z.record(z.string(), z.string()).optional().describe("Connector data that must be present: {key: value}; '*' = any value"),
  exclude_user_text: z.array(z.record(z.string(), z.string())).optional().describe("Leave out elements matching any of these sets, e.g. [{\"mcp.kind\": \"analysis\"}]"),
  selected: z.boolean().optional().describe("true = only the elements selected in Revit"),
};

export const ParameterValueChange = z.object({
  name: z.string().describe("Parameter name as shown in Revit (or a BuiltInParameter name)"),
  value: z.any().describe("New value in SI (m, m², m³, °), text, yes/no, or an element/level name"),
  mode: z.enum(["set", "add", "multiply", "percent"]).optional().describe("set (default) | add | multiply | percent (+value %)"),
  target: z.enum(["instance", "type"]).optional(),
});

export const GEOMETRY_TYPES = [
  "point", "points", "text_dot", "line", "polyline", "curve", "circle", "arc", "ellipse", "rectangle", "polygon",
  "surface", "planar_surface", "box", "sphere", "cylinder", "cone", "extrusion", "loft", "mesh",
] as const;

export const GeometrySpec = z
  .looseObject({
    type: z.enum(GEOMETRY_TYPES),
    category: z.string().optional().describe("Revit category of the DirectShape (default Generic Models; trees → Planting, masses → Mass)"),
    layer: z.string().optional().describe("Pseudo-layer stored on the element"),
    name: z.string().optional(),
    color: Color.optional(),
    transparency: z.number().min(0).max(100).optional(),
    user_text: z.record(z.string(), z.any()).optional(),
  })
  .describe("Geometry spec: 'type' plus the fields of that type (see tool description)");

export const GEOMETRY_HELP = `Free-form geometry becomes DirectShape elements (meters, degrees):
- point {location} · points {points} · text_dot {location, text} (a point named with the text)
- line {from, to} · polyline {points, closed?} · curve {points, closed?} · circle {center, radius} · arc {center, radius, start_angle, end_angle} or {start, end, through}
- ellipse {center, radius_x, radius_y} · rectangle {corner, width, height} or {center, width, height} · polygon {center, radius, sides, rotation?}
- planar_surface {points (closed boundary)} · surface {corners:[p1,p2,p3(,p4)]}
- box {corner, size:[dx,dy,dz]} or {center, size} or {min, max} · sphere {center, radius} · cylinder {base, radius, height, axis?} · cone {base, radius, height}
- extrusion {profile:[[x,y,z],…] (closed), holes?, height | direction} ← masses from footprints
- loft {sections:[[points…],[points…]]} · mesh {vertices, faces:[[i,j,k]|[i,j,k,l]], vertex_colors?} (colours become shading materials)
Every spec also accepts: category, layer, name, color, transparency, user_text.`;

export const ELEMENT_HELP = `Native Revit elements (meters, degrees):
- level {elevation, name?, create_view?=true}
- grid {from, to, name?}
- wall {from, to | points (polyline), closed?, level?, height?=3, base_offset?, type?, structural?}
- floor {points (closed boundary), holes?, level?, type?, offset?}
- roof {points (footprint), level?, type?, slope_deg? (every edge), offset?}
- family_instance {family?, type?, location | locations, level?, rotation_deg?}
- model_line {points | from, to, closed?, line_style?, color?}
- room {level, location}
- mass {profile, height, holes?, name?} — DirectShape in the Mass category
- terrain {points:[[x,y,z],…]} — Toposolid (Revit 2024+) or Topography (2022–2023)
Every element also accepts: parameters {"Name": value} set after creation, user_text, layer.`;
