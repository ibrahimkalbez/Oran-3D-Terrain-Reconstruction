import fsp from "node:fs/promises";
import path from "node:path";
import { bounds, openRing, pointInPolygon, type Polygon, type Vec2 } from "../../geometry/polygon.js";
import { squareGrid } from "../../geometry/sampling.js";
import type { Source } from "../../site.js";
import type { AdapterContext, SimCase, SolverAdapter } from "../store.js";
import { ORAN, sunPath } from "../sun.js";

function filterOf(src: Source | undefined): Record<string, unknown> | undefined {
  if (!src) return undefined;
  if (src.grasshopper) return { grasshopper: src.grasshopper };
  if (src.filter) return src.filter;
  if (src.ids) return { ids: src.ids };
  if (src.layer) return { layer: src.layer, include_sublayers: true };
  return undefined;
}

/** Obstacles for ray casting: studied buildings, plus the surrounding city when given. */
function obstaclesParam(settings: Record<string, any>): Record<string, unknown> {
  const studied = filterOf(settings.obstacles);
  const context = filterOf(settings.context);
  if (studied && context) return { obstacles: [studied, context] };
  if (studied) return { obstacles: studied };
  if (context) return { obstacles: [context] };
  return {};
}

/** Blue → green → yellow ramp for 0..1. */
export function ramp(t: number): [number, number, number] {
  const stops: Array<[number, [number, number, number]]> = [
    [0, [40, 30, 120]],
    [0.35, [30, 120, 170]],
    [0.65, [80, 190, 90]],
    [1, [250, 220, 40]],
  ];
  const x = Math.max(0, Math.min(1, t));
  for (let i = 1; i < stops.length; i++) {
    if (x <= stops[i][0]) {
      const [t0, c0] = stops[i - 1];
      const [t1, c1] = stops[i];
      const f = (x - t0) / (t1 - t0);
      return [0, 1, 2].map((k) => Math.round(c0[k] + (c1[k] - c0[k]) * f)) as [number, number, number];
    }
  }
  return stops[stops.length - 1][1];
}

/**
 * Native sun-hours analysis (no ANSYS needed): sun positions for the chosen days at the site,
 * a grid of points over the study area, and ray casting against buildings/terrain in Rhino.
 * Gives sunlight hours on public space and the shadow footprint of each variant.
 */
export const solarAdapter: SolverAdapter = {
  id: "solar",
  title: "Ensoleillement / ombres portées (natif Rhino)",
  description:
    "Heures d'ensoleillement sur une grille de points (sol ou hauteur piéton) pour un ou plusieurs jours, " +
    "par lancer de rayons contre les bâtiments et le relief. Ne nécessite pas ANSYS.",
  settings: {
    area: "Zone étudiée: {layer|ids|filter} de courbes fermées. Défaut: emprise des bâtiments + marge",
    margin: "Marge autour des bâtiments quand area est absent (m, défaut 20)",
    spacing: "Pas de la grille (m, défaut 2)",
    height_offset: "Hauteur des points au-dessus du sol (m, défaut 0.1 ; 1.5 = piéton)",
    terrain: "Relief sur lequel poser la grille: {layer|ids|filter} (facultatif)",
    ground_z: "Altitude du sol sans relief (défaut: base des bâtiments)",
    obstacles: "Bâtiments étudiés qui font de l'ombre: {layer|ids|filter|grasshopper} (défaut: tous les solides et arbres visibles, sauf cartes d'analyse et domaines de vent)",
    context: "Ville environnante qui fait aussi de l'ombre (ex. {layer: \"Oran::Bâti\"}) — utile quand les bâtiments étudiés viennent de Grasshopper",
    dates: "Jours étudiés YYYY-MM-DD (défaut: solstice d'hiver)",
    step_minutes: "Pas de temps (défaut 30)",
    latitude: `Latitude (défaut Oran ${ORAN.latitude})`,
    longitude: `Longitude (défaut Oran ${ORAN.longitude})`,
    utc_offset: "Décalage horaire (défaut +1)",
    north: "Direction du nord dans le plan [x, y] (défaut [0, 1])",
    threshold_hours: "Seuil d'ensoleillement pour le % de surface (défaut 2 h)",
    visualize: "Créer la carte colorée dans Rhino (défaut true)",
    max_points: "Nombre maximal de points (défaut 20000)",
  },

  async check() {
    return { available: true, detail: "Calcul natif dans Rhino (plug-in RhinoMcpBridge ≥ 1.1)." };
  },

  async prepare(ctx: AdapterContext, c: SimCase) {
    const s = c.settings;
    const long = { timeoutMs: ctx.config.longTimeoutMs };
    const spacing = Number(s.spacing ?? 2);
    if (!(spacing > 0)) throw new Error("spacing must be > 0.");
    const year = new Date().getFullYear();
    const sun = sunPath({
      dates: s.dates ?? [`${year}-12-21`],
      latitude: s.latitude,
      longitude: s.longitude,
      utc_offset: s.utc_offset,
      step_minutes: s.step_minutes,
      north: s.north,
      min_elevation: s.min_elevation ?? 0.5,
    });
    if (sun.length === 0) throw new Error("The sun stays below the horizon for these dates.");

    // Building footprints: the studied buildings (obstacles) and the surrounding city (context).
    // No sample point inside a building; the default study area surrounds the studied buildings.
    const obstacleFilter = filterOf(s.obstacles);
    const contextFilter = filterOf(s.context);
    let groundZ = s.ground_z;
    const footprintsOf = async (filter: Record<string, unknown>) => {
      try {
        const fp = await ctx.bridge.call("analysis.footprints", filter, long);
        const items = (fp.items ?? []).filter((it: any) => (it.height ?? 0) >= 0.5);
        if (groundZ === undefined && items.length) groundZ = Math.min(...items.map((it: any) => it.base_z ?? 0));
        return items.flatMap((it: any) => (it.parts ?? []).map((p: any) => ({ outer: openRing(p.outer) }))) as Polygon[];
      } catch {
        return [] as Polygon[];
      }
    };
    // Default: buildings = visible solids, without trees and the connectors' analysis objects.
    const studied = await footprintsOf(
      obstacleFilter ?? { types: ["brep", "extrusion", "mesh", "subd"], include_hidden: false, exclude_user_text: [{ "mcp.kind": "analysis" }, { "mcp.kind": "wind_domain" }, { "mcp.kind": "tree" }] },
    );
    const footprints: Polygon[] = [...studied, ...(contextFilter ? await footprintsOf(contextFilter) : [])];
    let areas: Polygon[];
    const areaFilter = filterOf(s.area);
    if (areaFilter) {
      const curves = await ctx.bridge.call("analysis.curves", areaFilter, long);
      areas = (curves.items ?? []).filter((x: any) => x.closed).map((x: any) => ({ outer: openRing(x.points) }));
      if (areas.length === 0) throw new Error("The study area contains no closed curve.");
    } else {
      const around = studied.length ? studied : footprints;
      if (around.length === 0) throw new Error("No buildings found: give 'area' or 'obstacles'.");
      const b = bounds(around.flatMap((f) => f.outer));
      const m = Number(s.margin ?? 20);
      areas = [{ outer: [[b.min[0] - m, b.min[1] - m], [b.max[0] + m, b.min[1] - m], [b.max[0] + m, b.max[1] + m], [b.min[0] - m, b.max[1] + m]] }];
    }
    let pts: Vec2[] = areas.flatMap((a) => squareGrid(a, spacing)).filter((p) => !footprints.some((f) => pointInPolygon(p, f)));
    const maxPoints = Number(s.max_points ?? 20000);
    if (pts.length > maxPoints) throw new Error(`${pts.length} points (> max_points=${maxPoints}): increase spacing or reduce the area.`);
    if (pts.length === 0) throw new Error("No sample point in the study area.");

    const offset = Number(s.height_offset ?? 0.1);
    let points3: Array<[number, number, number]>;
    const terrainFilter = filterOf(s.terrain);
    if (terrainFilter) {
      const draped = await ctx.bridge.call("analysis.drape_points", { points: pts, target: terrainFilter, offset }, long);
      points3 = draped.points.filter((p: any) => p[2] !== null);
    } else {
      points3 = pts.map((p) => [p[0], p[1], Number(groundZ ?? 0) + offset]);
    }

    await fsp.writeFile(path.join(c.dir, "sun.json"), JSON.stringify(sun, null, 1));
    await fsp.writeFile(path.join(c.dir, "points.json"), JSON.stringify(points3));
    c.inputs = { sun: "sun.json", points: "points.json" };
    c.settings = { ...s, spacing, resolved_points: points3.length, sun_samples: sun.length };
  },

  async run(ctx, c, h) {
    const sun = JSON.parse(await fsp.readFile(path.join(c.dir, "sun.json"), "utf8"));
    const points = JSON.parse(await fsp.readFile(path.join(c.dir, "points.json"), "utf8"));
    h.log(`${points.length} points × ${sun.length} sun positions`);
    const res = await ctx.bridge.call(
      "analysis.ray_visibility",
      {
        points,
        directions: sun.map((x: any) => x.vector),
        weights: sun.map((x: any) => x.weight_hours),
        normals: points.map(() => [0, 0, 1]),
        ...obstaclesParam(c.settings),
        max_rays: 50_000_000,
      },
      { timeoutMs: ctx.config.longTimeoutMs },
    );
    await fsp.writeFile(path.join(c.dir, "values.json"), JSON.stringify(res.values));
    const csv = ["x,y,z,sun_hours", ...points.map((p: number[], i: number) => `${p[0]},${p[1]},${p[2]},${res.values[i]}`)].join("\n");
    await fsp.writeFile(path.join(c.dir, "sun_hours.csv"), csv);
    c.inputs.values = "values.json";
    c.results = { ray_stats: res.stats };
  },

  async collect(ctx, c) {
    const points: number[][] = JSON.parse(await fsp.readFile(path.join(c.dir, "points.json"), "utf8"));
    const values: number[] = JSON.parse(await fsp.readFile(path.join(c.dir, "values.json"), "utf8"));
    const sun = JSON.parse(await fsp.readFile(path.join(c.dir, "sun.json"), "utf8"));
    const spacing = Number(c.settings.spacing ?? 2);
    const threshold = Number(c.settings.threshold_hours ?? 2);
    const possible = sun.reduce((s: number, x: any) => s + x.weight_hours, 0);
    const n = values.length;
    const mean = values.reduce((a, b) => a + b, 0) / n;
    const above = values.filter((v) => v >= threshold - 1e-9).length;
    const shaded = values.filter((v) => v <= 1e-9).length;
    c.metrics = {
      sun_hours_mean: Math.round(mean * 100) / 100,
      sun_hours_min: Math.min(...values),
      sun_hours_max: Math.max(...values),
      sun_hours_possible: Math.round(possible * 100) / 100,
      area_pct_above_threshold: Math.round((above / n) * 10000) / 100,
      area_pct_always_shaded: Math.round((shaded / n) * 10000) / 100,
      analysed_area_m2: Math.round(n * spacing * spacing),
    };

    if (c.settings.visualize !== false) {
      const layer = `Analysis::Sun hours::${c.name || c.id}`.replace(/[^\w:. -]/g, "_");
      await ctx.bridge.call("rhino.delete_objects", { user_text: { "mcp.sim": c.id }, max_count: 100000 }).catch(() => undefined);
      const half = spacing / 2;
      const vertices: number[][] = [];
      const faces: number[][] = [];
      const colors: number[][] = [];
      points.forEach((p, i) => {
        const k = vertices.length;
        vertices.push([p[0] - half, p[1] - half, p[2]], [p[0] + half, p[1] - half, p[2]], [p[0] + half, p[1] + half, p[2]], [p[0] - half, p[1] + half, p[2]]);
        faces.push([k, k + 1, k + 2, k + 3]);
        const col = ramp(possible > 0 ? values[i] / possible : 0);
        colors.push(col, col, col, col);
      });
      await ctx.bridge.call(
        "rhino.create_geometry",
        { geometries: [{ type: "mesh", vertices, faces, vertex_colors: colors, layer, name: `Sun hours ${c.id}`, user_text: { "mcp.sim": c.id, "mcp.kind": "analysis" } }] },
        { timeoutMs: ctx.config.longTimeoutMs },
      );
      try {
        const file = path.join(c.dir, "sun_hours.png");
        await ctx.bridge.call(
          "rhino.capture_viewport",
          { direction: "top", display_mode: "Shaded", width: 1400, height: 1000, zoom: { user_text: { "mcp.sim": c.id } }, save_path: file, return_image: false, preview: "none" },
          { timeoutMs: ctx.config.longTimeoutMs },
        );
        c.images = [file];
      } catch (err) {
        (c.warnings ??= []).push(`capture failed: ${(err as Error).message}`);
      }
      c.results = { ...(c.results as object), visualization_layer: layer, legend: { min: 0, max: possible, unit: "h", colors: "bleu → jaune" } };
    }
  },
};
