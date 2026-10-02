import type { BridgeClient } from "../../rhino-grasshopper-mcp/src/bridge/client.js";
import { hostMethod, type HostProfile } from "../../rhino-grasshopper-mcp/src/host.js";
import { centroid, openRing, polygonArea, type Polygon } from "./geometry/polygon.js";
import type { Area, Building, Line, Plot, SiteModel } from "./rules/engine.js";

/**
 * Where each category of the site comes from: a Rhino layer, ids, a full filter,
 * or Grasshopper outputs ({grasshopper: {outputs: ["OUT_Buildings"]}}).
 */
export interface Source {
  layer?: string;
  ids?: string[];
  filter?: Record<string, unknown>;
  grasshopper?: { definition?: string; outputs?: string[] };
}

export interface SiteSources {
  buildings?: Source;
  plots?: Source;
  roads?: Source;
  green?: Source;
  site_area?: number;
}

const KEYWORDS: Record<keyof Omit<SiteSources, "site_area">, RegExp> = {
  buildings: /(b[aâ]ti|building|b[aâ]timent|construction|immeuble|logement|massing|volume|^Category::(Mass|Masse)$)/i,
  plots: /(parcel|plot|lot\b|lots|[iî]lot|cadast|terrain_a_batir|unit[eé] fonci|property line|ligne de propri)/i,
  roads: /(road|route|rue|voirie|street|chauss|axe|voie)/i,
  green: /(espace.?vert|vert|green|jardin|parc|park|landscape|paysag|pelouse|lawn)/i,
};

/** Guesses the layer of each category from the layer names of the document. */
export async function detectSources(bridge: BridgeClient, profile?: HostProfile): Promise<{ sources: SiteSources; candidates: Record<string, string[]> }> {
  const doc = await bridge.call(hostMethod(profile, "get_document"), { max_layers: 2000 });
  const layers: Array<{ path: string; object_count: number }> = doc.layers ?? [];
  const sources: SiteSources = {};
  const candidates: Record<string, string[]> = {};
  for (const [key, rx] of Object.entries(KEYWORDS) as Array<[keyof typeof KEYWORDS, RegExp]>) {
    const hits = layers.filter((l) => rx.test(l.path) && l.object_count > 0);
    // A parent layer gathering several matches is preferred: its sub-layers are included.
    hits.sort((a, b) => a.path.split("::").length - b.path.split("::").length || b.object_count - a.object_count);
    candidates[key] = hits.map((h) => `${h.path} (${h.object_count})`);
    if (hits.length) sources[key] = { layer: hits[0].path };
  }
  return { sources, candidates };
}

function params(src: Source): Record<string, unknown> {
  if (src.grasshopper) return { grasshopper: src.grasshopper };
  if (src.filter) return { ...src.filter };
  if (src.ids) return { ids: src.ids };
  if (src.layer) return { layer: src.layer, include_sublayers: true };
  throw new Error("A source needs 'layer', 'ids', 'filter' or 'grasshopper'.");
}

const FLOOR_KEYS = ["floors", "niveaux", "etages", "étages", "storeys", "stories", "nb_niveaux", "levels"];
const USE_KEYS = ["use", "usage", "affectation", "fonction", "program"];

function userValue(ut: Record<string, string> | undefined, keys: string[]): string | undefined {
  if (!ut) return undefined;
  for (const [k, v] of Object.entries(ut)) if (keys.includes(k.toLowerCase())) return v;
  return undefined;
}

/** Objects lower than this are not buildings (ground surfaces, plot slabs, flat outputs). */
export const MIN_BUILDING_HEIGHT = 0.5;

export async function loadBuildings(bridge: BridgeClient, src: Source, timeoutMs: number): Promise<Building[]> {
  const res = await bridge.call("analysis.footprints", params(src), { timeoutMs });
  return (res.items ?? []).filter((it: any) => (it.height ?? 0) >= MIN_BUILDING_HEIGHT).map((it: any): Building => {
    const parts: Polygon[] = (it.parts ?? []).map((p: any) => ({ outer: openRing(p.outer), holes: (p.holes ?? []).map(openRing) }));
    const floors = Number(userValue(it.user_text, FLOOR_KEYS));
    return {
      id: it.id,
      name: it.name || "",
      layer: it.layer,
      parts,
      area: it.area ?? parts.reduce((s, p) => s + polygonArea(p), 0),
      base_z: it.base_z ?? 0,
      top_z: it.top_z ?? 0,
      height: it.height ?? 0,
      floors: Number.isFinite(floors) && floors > 0 ? floors : undefined,
      volume: it.volume ?? undefined,
      centroid: it.centroid ?? centroid(parts[0]?.outer ?? [[0, 0]]),
      use: userValue(it.user_text, USE_KEYS),
    };
  });
}

async function loadCurves(bridge: BridgeClient, src: Source, timeoutMs: number): Promise<any[]> {
  const res = await bridge.call("analysis.curves", params(src), { timeoutMs });
  return res.items ?? [];
}

export async function loadSite(bridge: BridgeClient, sources: SiteSources, timeoutMs: number): Promise<SiteModel> {
  const site: SiteModel = { buildings: [], plots: [], roads: [], green: [], site_area: sources.site_area };
  if (sources.buildings) site.buildings = await loadBuildings(bridge, sources.buildings, timeoutMs);
  if (sources.plots) {
    site.plots = (await loadCurves(bridge, sources.plots, timeoutMs))
      .filter((c) => c.closed)
      .map((c): Plot => {
        const polygon = { outer: openRing(c.points) };
        return { id: c.id, name: c.name || "", polygon, area: Math.abs(polygonArea(polygon)), zone: c.user_text?.zone ?? c.user_text?.zonage };
      });
  }
  if (sources.roads) {
    site.roads = (await loadCurves(bridge, sources.roads, timeoutMs)).map((c): Line => ({
      id: c.id,
      name: c.name || "",
      points: openRing(c.points),
      closed: Boolean(c.closed),
    }));
  }
  if (sources.green) {
    site.green = (await loadCurves(bridge, sources.green, timeoutMs))
      .filter((c) => c.closed)
      .map((c): Area => {
        const polygon = { outer: openRing(c.points) };
        return { id: c.id, name: c.name || "", polygon, area: Math.abs(polygonArea(polygon)) };
      });
  }
  return site;
}

/** Short description of the site for Claude (counts, areas, heights). */
export function siteSummary(site: SiteModel) {
  const heights = site.buildings.map((b) => b.height);
  return {
    buildings: site.buildings.length,
    plots: site.plots.length,
    roads: site.roads.length,
    green_areas: site.green.length,
    footprint_area: Math.round(site.buildings.reduce((s, b) => s + b.area, 0)),
    plots_area: Math.round(site.plots.reduce((s, p) => s + p.area, 0)),
    height_range: heights.length ? [Math.round(Math.min(...heights) * 10) / 10, Math.round(Math.max(...heights) * 10) / 10] : null,
  };
}
