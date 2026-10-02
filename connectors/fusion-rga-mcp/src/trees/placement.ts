import { distanceToPolyline, pointInPolygon, pointSegmentDistance, type Polygon, type Vec2 } from "../geometry/polygon.js";
import { alongPolyline, hexGrid, poissonDisk, rng, squareGrid } from "../geometry/sampling.js";

export interface Obstacles {
  /** Building footprints (and any area to keep free), with the clearance to keep around them. */
  areas?: Polygon[];
  areaClearance?: number;
  /** Lines to keep clear (roadways), with their half-width + margin. */
  lines?: Vec2[][];
  lineClearance?: number;
  /** Existing trees. */
  points?: Vec2[];
  pointClearance?: number;
}

export interface AlongSpec {
  mode: "along";
  lines: Vec2[][];
  spacing: number;
  offset?: number;
  sides?: "left" | "right" | "both" | "center";
  start?: number;
}

export interface AreaSpec {
  mode: "area";
  areas: Polygon[];
  spacing: number;
  pattern?: "hex" | "grid" | "random";
  margin?: number;
}

export interface PointsSpec {
  mode: "points";
  points: Vec2[];
}

export type PlacementSpec = AlongSpec | AreaSpec | PointsSpec;

function shrinkFromBoundary(points: Vec2[], areas: Polygon[], margin: number): Vec2[] {
  if (margin <= 0) return points;
  return points.filter((p) =>
    areas.every((a) => !pointInPolygon(p, a) || boundaryDistance(p, a) >= margin),
  );
}

function boundaryDistance(p: Vec2, poly: Polygon): number {
  let best = Infinity;
  for (const ring of [poly.outer, ...(poly.holes ?? [])]) {
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) best = Math.min(best, pointSegmentDistance(p, ring[j], ring[i]));
  }
  return best;
}

/** Candidate positions for a placement spec (before obstacles). */
export function candidates(spec: PlacementSpec, seed = 1): Vec2[] {
  switch (spec.mode) {
    case "points":
      return spec.points.map((p) => [p[0], p[1]] as Vec2);
    case "along":
      return spec.lines.flatMap((l) => alongPolyline(l, spec.spacing, spec.offset ?? 0, spec.sides ?? "both", spec.start ?? spec.spacing / 2));
    case "area": {
      const random = rng(seed);
      const pts = spec.areas.flatMap((a) =>
        spec.pattern === "grid"
          ? squareGrid(a, spec.spacing)
          : spec.pattern === "random"
            ? poissonDisk(a, spec.spacing, random)
            : hexGrid(a, spec.spacing),
      );
      return shrinkFromBoundary(pts, spec.areas, spec.margin ?? 0);
    }
  }
}

export interface Rejection {
  point: Vec2;
  reason: "building" | "road" | "tree" | "spacing";
}

/** Removes positions that fall in or too close to obstacles, and keeps a minimum spacing between new trees. */
export function filterObstacles(points: Vec2[], obs: Obstacles, minSpacing = 0): { kept: Vec2[]; rejected: Rejection[] } {
  const kept: Vec2[] = [];
  const rejected: Rejection[] = [];
  const areaClear = obs.areaClearance ?? 0;
  const lineClear = obs.lineClearance ?? 0;
  const pointClear = obs.pointClearance ?? 0;
  for (const p of points) {
    if ((obs.areas ?? []).some((a) => pointInPolygon(p, a) || (areaClear > 0 && boundaryDistance(p, a) < areaClear))) {
      rejected.push({ point: p, reason: "building" });
      continue;
    }
    if (lineClear > 0 && (obs.lines ?? []).some((l) => distanceToPolyline({ outer: [p, [p[0] + 1e-6, p[1]], [p[0], p[1] + 1e-6]] }, l) < lineClear)) {
      rejected.push({ point: p, reason: "road" });
      continue;
    }
    if (pointClear > 0 && (obs.points ?? []).some((q) => Math.hypot(q[0] - p[0], q[1] - p[1]) < pointClear)) {
      rejected.push({ point: p, reason: "tree" });
      continue;
    }
    if (minSpacing > 0 && kept.some((q) => Math.hypot(q[0] - p[0], q[1] - p[1]) < minSpacing - 1e-9)) {
      rejected.push({ point: p, reason: "spacing" });
      continue;
    }
    kept.push(p);
  }
  return { kept, rejected };
}

/** Picks a species for each position from a weighted mix (reproducible with the seed). */
export function assignSpecies(count: number, mix: Array<{ species: string; weight?: number }>, seed = 1): string[] {
  if (mix.length === 0) throw new Error("At least one species is needed.");
  const random = rng(seed + 7919);
  const total = mix.reduce((s, m) => s + (m.weight ?? 1), 0);
  return Array.from({ length: count }, () => {
    let r = random() * total;
    for (const m of mix) {
      r -= m.weight ?? 1;
      if (r <= 0) return m.species;
    }
    return mix[mix.length - 1].species;
  });
}
