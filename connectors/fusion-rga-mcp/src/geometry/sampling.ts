import { bounds, pointInPolygon, type Polygon, type Vec2 } from "./polygon.js";

/** Deterministic pseudo-random generator (mulberry32) so results are reproducible with a seed. */
export function rng(seed = 1): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Staggered (hexagonal) grid of points inside a polygon: the densest regular planting pattern. */
export function hexGrid(poly: Polygon, spacing: number, margin = 0): Vec2[] {
  const b = bounds(poly.outer);
  const rowH = (spacing * Math.sqrt(3)) / 2;
  const out: Vec2[] = [];
  let row = 0;
  for (let y = b.min[1] + margin; y <= b.max[1] - margin + 1e-9; y += rowH, row++) {
    const shift = row % 2 ? spacing / 2 : 0;
    for (let x = b.min[0] + margin + shift; x <= b.max[0] - margin + 1e-9; x += spacing) {
      if (pointInPolygon([x, y], poly)) out.push([x, y]);
    }
  }
  return out;
}

/** Square grid of points inside a polygon. */
export function squareGrid(poly: Polygon, spacing: number): Vec2[] {
  const b = bounds(poly.outer);
  const out: Vec2[] = [];
  for (let y = b.min[1] + spacing / 2; y <= b.max[1]; y += spacing)
    for (let x = b.min[0] + spacing / 2; x <= b.max[0]; x += spacing) if (pointInPolygon([x, y], poly)) out.push([x, y]);
  return out;
}

/**
 * Natural-looking random distribution with a minimum distance between points
 * (Bridson's Poisson-disk sampling), restricted to the polygon.
 */
export function poissonDisk(poly: Polygon, minDist: number, random: () => number, maxPoints = 10000, k = 30): Vec2[] {
  const b = bounds(poly.outer);
  const cell = minDist / Math.SQRT2;
  const cols = Math.ceil((b.max[0] - b.min[0]) / cell) + 1;
  const rows = Math.ceil((b.max[1] - b.min[1]) / cell) + 1;
  const grid = new Int32Array(cols * rows).fill(-1);
  const points: Vec2[] = [];
  const active: number[] = [];
  const gi = (p: Vec2) => [Math.floor((p[0] - b.min[0]) / cell), Math.floor((p[1] - b.min[1]) / cell)];
  const fits = (p: Vec2) => {
    if (!pointInPolygon(p, poly)) return false;
    const [cx, cy] = gi(p);
    for (let x = Math.max(0, cx - 2); x <= Math.min(cols - 1, cx + 2); x++)
      for (let y = Math.max(0, cy - 2); y <= Math.min(rows - 1, cy + 2); y++) {
        const idx = grid[y * cols + x];
        if (idx >= 0 && Math.hypot(points[idx][0] - p[0], points[idx][1] - p[1]) < minDist) return false;
      }
    return true;
  };
  const add = (p: Vec2) => {
    points.push(p);
    const [cx, cy] = gi(p);
    grid[cy * cols + cx] = points.length - 1;
    active.push(points.length - 1);
  };

  for (let tries = 0; tries < 200 && points.length === 0; tries++) {
    const p: Vec2 = [b.min[0] + random() * (b.max[0] - b.min[0]), b.min[1] + random() * (b.max[1] - b.min[1])];
    if (pointInPolygon(p, poly)) add(p);
  }
  while (active.length > 0 && points.length < maxPoints) {
    const ai = Math.floor(random() * active.length);
    const base = points[active[ai]];
    let placed = false;
    for (let i = 0; i < k; i++) {
      const ang = random() * 2 * Math.PI;
      const rad = minDist * (1 + random());
      const p: Vec2 = [base[0] + rad * Math.cos(ang), base[1] + rad * Math.sin(ang)];
      if (p[0] < b.min[0] || p[0] > b.max[0] || p[1] < b.min[1] || p[1] > b.max[1]) continue;
      if (fits(p)) {
        add(p);
        placed = true;
        break;
      }
    }
    if (!placed) active.splice(ai, 1);
  }
  return points;
}

/**
 * Points every `spacing` along a polyline (street alignment), shifted sideways by `offset`
 * on the left, the right or both sides. `start` skips the first metres (crossroads).
 */
export function alongPolyline(
  line: Vec2[],
  spacing: number,
  offset = 0,
  sides: "left" | "right" | "both" | "center" = "both",
  start = spacing / 2,
): Vec2[] {
  const out: Vec2[] = [];
  const sideList = offset === 0 || sides === "center" ? [0] : sides === "both" ? [1, -1] : sides === "left" ? [1] : [-1];
  let carried = start;
  for (let i = 1; i < line.length; i++) {
    const a = line[i - 1];
    const b = line[i];
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (len < 1e-9) continue;
    const ux = (b[0] - a[0]) / len;
    const uy = (b[1] - a[1]) / len;
    let d = carried;
    for (; d <= len + 1e-9; d += spacing) {
      const px = a[0] + ux * d;
      const py = a[1] + uy * d;
      for (const s of sideList) out.push([px - uy * offset * s, py + ux * offset * s]);
    }
    carried = d - len;
  }
  return out;
}
