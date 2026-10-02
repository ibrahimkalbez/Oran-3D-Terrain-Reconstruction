import fs from "node:fs/promises";
import path from "node:path";
import { z } from "zod";
import { hostMethod } from "../../../rhino-grasshopper-mcp/src/host.js";
import { guarded, ok } from "../../../rhino-grasshopper-mcp/src/util/result.js";
import { READ, SourceSchema, WRITE, type FusionContext } from "../context.js";
import { discUnionArea, openRing, type Polygon, type Vec2 } from "../geometry/polygon.js";
import { rng } from "../geometry/sampling.js";
import { loadBuildings, type Source } from "../site.js";
import { findSpecies, SPECIES, type Species } from "../trees/catalog.js";
import { treeMesh, type Lod } from "../trees/mesh.js";
import { assignSpecies, candidates, filterObstacles, type PlacementSpec } from "../trees/placement.js";

const TREE_KIND = "tree";

function curvesParams(src: Source): Record<string, unknown> {
  if (src.grasshopper) return { grasshopper: src.grasshopper };
  if (src.filter) return src.filter;
  if (src.ids) return { ids: src.ids };
  if (src.layer) return { layer: src.layer, include_sublayers: true };
  throw new Error("A source needs 'layer', 'ids', 'filter' or 'grasshopper'.");
}

async function customSpecies(workspace: string): Promise<Species[]> {
  try {
    return JSON.parse(await fs.readFile(path.join(workspace, "trees", "species.json"), "utf8")) as Species[];
  } catch {
    return [];
  }
}

export function registerTreeTools(ctx: FusionContext): void {
  const { server, bridge, config } = ctx;
  const long = { timeoutMs: config.longTimeoutMs };

  server.registerTool(
    "trees_species",
    {
      title: "Tree species catalogue",
      description:
        "Species available to the tree generator (Mediterranean palette suited to Oran) with height, crown diameter, trunk height, " +
        "crown shape, leaf area density (for wind porosity). Add species in <workspace>/trees/species.json (same fields).",
      inputSchema: {},
      annotations: READ,
    },
    guarded(async () => ok({ species: [...(await customSpecies(config.workspace)), ...SPECIES], custom_file: path.join(config.workspace, "trees", "species.json") }, "Dimensions are indicative mature values.")),
  );

  server.registerTool(
    "trees_generate",
    {
      title: "Generate trees",
      description:
        `Plant trees in ${config.profile?.label ?? "Rhino"}${config.profile?.id === "revit" ? " (Planting DirectShapes)" : ""}: ` +
        "along streets (mode 'along': every N m, offset to one or both sides), in areas (mode 'area': hex/grid/random " +
        "pattern in parks, squares, plots) or at given points. Keeps clear of buildings and roadways, can sit on the terrain, mixes species " +
        "with weights and natural size variation. Each tree is one mesh (trunk + crown) with user text (species, height, crown, LAD) on " +
        "'Vegetation::Trees::<species>'; set_name groups a planting so it can be regenerated (replace=true) or removed. dry_run only counts.",
      inputSchema: {
        mode: z.enum(["along", "area", "points"]),
        lines: SourceSchema.optional().describe("along: street axes / alignments (curves)"),
        polylines: z.array(z.array(z.array(z.number()))).optional().describe("along: explicit polylines [[[x,y],…]]"),
        areas: SourceSchema.optional().describe("area: closed curves, surfaces or hatches"),
        polygons: z.array(z.array(z.array(z.number()))).optional().describe("area: explicit polygons"),
        points: z.array(z.array(z.number())).optional().describe("points: [[x,y],…]"),
        spacing: z.number().positive().optional().describe("Distance between trees (m, default 8)"),
        offset: z.number().optional().describe("along: lateral offset from the axis (m, default 0)"),
        sides: z.enum(["left", "right", "both", "center"]).optional(),
        start: z.number().optional().describe("along: distance before the first tree"),
        pattern: z.enum(["hex", "grid", "random"]).optional(),
        margin: z.number().optional().describe("area: distance kept from the area boundary"),
        species: z.union([z.string(), z.array(z.object({ species: z.string(), weight: z.number().positive().optional() }))]).optional(),
        overrides: z.object({ height: z.number().optional(), crown_diameter: z.number().optional(), trunk_height: z.number().optional() }).optional(),
        size_variation: z.number().min(0).max(0.5).optional().describe("Random ±fraction on sizes (default 0.1)"),
        buildings: SourceSchema.optional().describe("Obstacles: buildings to keep clear of"),
        building_clearance: z.number().optional().describe("Distance to façades (default: crown radius)"),
        roads: SourceSchema.optional().describe("Obstacles: roadway axes"),
        road_clearance: z.number().optional().describe("Distance to road axes (default 3 m)"),
        avoid_existing: z.boolean().optional().describe("Keep away from trees already generated (default true)"),
        terrain: SourceSchema.optional().describe("Terrain to sit the trees on"),
        base_z: z.number().optional().describe("Ground altitude without terrain (default 0)"),
        lod: z.enum(["point", "low", "medium", "high"]).optional().describe("Detail: point (text dots), low (default), medium, high"),
        layer: z.string().optional(),
        set_name: z.string().optional().describe("Name of this planting (default 'trees')"),
        replace: z.boolean().optional().describe("Replace a previous planting with the same set_name (default true)"),
        seed: z.number().int().optional(),
        max_trees: z.number().int().positive().optional().describe("Safety limit (default 3000)"),
        dry_run: z.boolean().optional(),
      },
      annotations: WRITE,
    },
    guarded(async (args) => {
      const spacing = args.spacing ?? 8;
      const seed = args.seed ?? 1;
      const custom = await customSpecies(config.workspace);
      const mix = typeof args.species === "string" ? [{ species: args.species }] : args.species?.length ? args.species : [{ species: "ficus" }];
      const resolved = new Map(mix.map((m) => [m.species, findSpecies(m.species, custom)]));
      const maxCrown = Math.max(...[...resolved.values()].map((s) => args.overrides?.crown_diameter ?? s.crown_diameter));

      let spec: PlacementSpec;
      if (args.mode === "points") {
        if (!args.points?.length) throw new Error("mode 'points' needs 'points'.");
        spec = { mode: "points", points: args.points.map((p) => [p[0], p[1]] as Vec2) };
      } else if (args.mode === "along") {
        let lines: Vec2[][] = (args.polylines ?? []).map((l) => l.map((p) => [p[0], p[1]] as Vec2));
        if (args.lines) {
          const res = await bridge.call("analysis.curves", curvesParams(args.lines as Source), long);
          lines = lines.concat((res.items ?? []).map((c: any) => {
            const pts = c.points.map((p: number[]) => [p[0], p[1]] as Vec2);
            return c.closed ? [...pts, pts[0]] : pts;
          }));
        }
        if (lines.length === 0) throw new Error("mode 'along' needs 'lines' or 'polylines'.");
        spec = { mode: "along", lines, spacing, offset: args.offset ?? 0, sides: args.sides ?? "both", start: args.start };
      } else {
        let areas: Polygon[] = (args.polygons ?? []).map((pg) => ({ outer: openRing(pg) }));
        if (args.areas) {
          const res = await bridge.call("analysis.curves", curvesParams(args.areas as Source), long);
          areas = areas.concat((res.items ?? []).filter((c: any) => c.closed).map((c: any) => ({ outer: openRing(c.points) })));
        }
        if (areas.length === 0) throw new Error("mode 'area' needs closed 'areas' or 'polygons'.");
        spec = { mode: "area", areas, spacing, pattern: args.pattern ?? "hex", margin: args.margin ?? maxCrown / 4 };
      }

      // Obstacles
      const buildingAreas: Polygon[] = args.buildings ? (await loadBuildings(bridge, args.buildings as Source, config.longTimeoutMs)).flatMap((b) => b.parts) : [];
      let roadLines: Vec2[][] = [];
      if (args.roads) {
        const res = await bridge.call("analysis.curves", curvesParams(args.roads as Source), long);
        roadLines = (res.items ?? []).map((c: any) => c.points.map((p: number[]) => [p[0], p[1]] as Vec2));
      }
      const setName = args.set_name ?? "trees";
      let existing: Vec2[] = [];
      if (args.avoid_existing !== false) {
        const found = await bridge.call(hostMethod(config.profile, "get_objects"), { user_text: { "mcp.kind": TREE_KIND }, detail: "full", limit: 5000 });
        existing = (found.objects ?? [])
          .filter((o: any) => !(args.replace !== false && o.user_text?.["mcp.tree_set"] === setName))
          .map((o: any) => [o.bbox.center[0], o.bbox.center[1]] as Vec2);
      }

      const raw = candidates(spec, seed);
      const { kept, rejected } = filterObstacles(
        raw,
        {
          areas: buildingAreas,
          areaClearance: args.building_clearance ?? maxCrown / 2,
          lines: roadLines,
          lineClearance: roadLines.length ? args.road_clearance ?? 3 : 0,
          points: existing,
          pointClearance: existing.length ? spacing * 0.6 : 0,
        },
        args.mode === "along" ? spacing * 0.5 : 0,
      );
      const max = args.max_trees ?? 3000;
      if (kept.length > max) throw new Error(`${kept.length} trees (> max_trees=${max}). Increase spacing or raise max_trees.`);
      const speciesOf = assignSpecies(kept.length, mix, seed);
      const random = rng(seed + 31);
      const variation = args.size_variation ?? 0.1;

      // Altitudes
      let zs: Array<number | null> = kept.map(() => args.base_z ?? 0);
      if (args.terrain && kept.length > 0) {
        const draped = await bridge.call("analysis.drape_points", { points: kept, target: curvesParams(args.terrain as Source) }, long);
        zs = draped.points.map((p: any) => p[2]);
      }

      const trees = kept
        .map((p, i) => {
          const sp = resolved.get(speciesOf[i])!;
          const f = 1 + (random() * 2 - 1) * variation;
          return {
            x: p[0],
            y: p[1],
            z: zs[i],
            species: sp,
            height: (args.overrides?.height ?? sp.height) * f,
            crown: (args.overrides?.crown_diameter ?? sp.crown_diameter) * f,
            trunk: (args.overrides?.trunk_height ?? sp.trunk_height) * f,
          };
        })
        .filter((t) => t.z !== null) as Array<{ x: number; y: number; z: number; species: Species; height: number; crown: number; trunk: number }>;

      const bySpecies: Record<string, number> = {};
      for (const t of trees) bySpecies[t.species.id] = (bySpecies[t.species.id] ?? 0) + 1;
      const canopy = discUnionArea(trees.map((t) => ({ c: [t.x, t.y] as Vec2, r: t.crown / 2 })), 0.5);
      const rejectedBy: Record<string, number> = {};
      for (const r of rejected) rejectedBy[r.reason] = (rejectedBy[r.reason] ?? 0) + 1;
      const summary = {
        set_name: setName,
        trees: trees.length,
        by_species: bySpecies,
        canopy_cover_m2: Math.round(canopy),
        candidates: raw.length,
        rejected: rejectedBy,
        off_terrain: kept.length - trees.length,
      };
      if (args.dry_run) return ok(summary, `Dry run: ${trees.length} trees would be planted.`);

      if (args.replace !== false) {
        await bridge.call(hostMethod(config.profile, "delete_objects"), { user_text: { "mcp.kind": TREE_KIND, "mcp.tree_set": setName }, max_count: 100000 }, long);
      }
      const lod = (args.lod ?? "low") as Lod | "point";
      const ids: string[] = [];
      const batch = 150;
      for (let i = 0; i < trees.length; i += batch) {
        const geometries = trees.slice(i, i + batch).map((t) => {
          const layer = args.layer ?? `Vegetation::Trees::${t.species.name}`;
          const user_text = {
            "mcp.kind": TREE_KIND,
            "mcp.tree_set": setName,
            species: t.species.id,
            latin: t.species.latin,
            height: t.height.toFixed(2),
            crown_diameter: t.crown.toFixed(2),
            trunk_height: t.trunk.toFixed(2),
            lad: String(t.species.lad),
            cd: String(t.species.cd),
          };
          if (lod === "point") return { type: "text_dot", location: [t.x, t.y, t.z + t.height], text: t.species.name, layer, user_text };
          const m = treeMesh(t.x, t.y, t.z, t.height, t.crown, t.trunk, t.species.shape, lod);
          return { type: "mesh", vertices: m.vertices, faces: m.faces, layer, name: t.species.name, user_text };
        });
        const res = await bridge.call(hostMethod(config.profile, "create_geometry"), { geometries }, long);
        for (const c of res.created ?? []) ids.push(c.id);
      }
      return ok({ ...summary, created: ids.length, ids: ids.slice(0, 200) }, `${ids.length} trees planted (set '${setName}'), canopy ≈ ${summary.canopy_cover_m2} m².`);
    }),
  );

  server.registerTool(
    "trees_remove",
    {
      title: "Remove generated trees",
      description: "Delete the trees created by trees_generate: one planting (set_name) or all of them.",
      inputSchema: { set_name: z.string().optional(), dry_run: z.boolean().optional() },
      annotations: { readOnlyHint: false, destructiveHint: true, openWorldHint: false },
    },
    guarded(async (args) =>
      ok(
        await bridge.call(
          hostMethod(config.profile, "delete_objects"),
          { user_text: { "mcp.kind": TREE_KIND, ...(args.set_name ? { "mcp.tree_set": args.set_name } : {}) }, max_count: 100000, dry_run: args.dry_run },
          long,
        ),
      ),
    ),
  );

  server.registerTool(
    "trees_stats",
    {
      title: "Tree statistics",
      description: "Count, species and canopy cover of the generated trees (all or one set_name), optionally inside a study area.",
      inputSchema: { set_name: z.string().optional(), area: SourceSchema.optional() },
      annotations: READ,
    },
    guarded(async (args) => {
      const found = await bridge.call(hostMethod(config.profile, "get_objects"), { user_text: { "mcp.kind": TREE_KIND, ...(args.set_name ? { "mcp.tree_set": args.set_name } : {}) }, detail: "full", limit: 5000 });
      const trees = (found.objects ?? []).map((o: any) => ({
        c: [o.bbox.center[0], o.bbox.center[1]] as Vec2,
        r: Number(o.user_text?.crown_diameter ?? 0) / 2,
        species: o.user_text?.species ?? "?",
        set: o.user_text?.["mcp.tree_set"] ?? "?",
      }));
      let clip: Polygon | undefined;
      let areaM2: number | undefined;
      if (args.area) {
        const res = await bridge.call("analysis.curves", curvesParams(args.area as Source), long);
        const c = (res.items ?? []).find((x: any) => x.closed);
        if (c) {
          clip = { outer: openRing(c.points) };
          areaM2 = c.area;
        }
      }
      const canopy = discUnionArea(trees, 0.5, clip);
      const bySpecies: Record<string, number> = {};
      const bySet: Record<string, number> = {};
      for (const t of trees) {
        bySpecies[t.species] = (bySpecies[t.species] ?? 0) + 1;
        bySet[t.set] = (bySet[t.set] ?? 0) + 1;
      }
      return ok({
        trees: trees.length,
        by_species: bySpecies,
        by_set: bySet,
        canopy_cover_m2: Math.round(canopy),
        ...(areaM2 ? { area_m2: Math.round(areaM2), canopy_ratio_pct: Math.round((canopy / areaM2) * 1000) / 10 } : {}),
      });
    }),
  );
}
