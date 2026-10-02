/** 2D geometry on plan coordinates (model units). Rings are open: the last point is not repeated. */

export type Vec2 = [number, number];
export type Ring = Vec2[];
export interface Polygon {
  outer: Ring;
  holes?: Ring[];
}

export function openRing(points: number[][]): Ring {
  const r = points.map((p) => [p[0], p[1]] as Vec2);
  if (r.length > 1) {
    const a = r[0];
    const b = r[r.length - 1];
    if (Math.abs(a[0] - b[0]) < 1e-9 && Math.abs(a[1] - b[1]) < 1e-9) r.pop();
  }
  return r;
}

/** Shoelace area, positive when counter-clockwise. */
export function signedArea(r: Ring): number {
  let a = 0;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) a += r[j][0] * r[i][1] - r[i][0] * r[j][1];
  return a / 2;
}

export function polygonArea(p: Polygon): number {
  return Math.abs(signedArea(p.outer)) - (p.holes ?? []).reduce((s, h) => s + Math.abs(signedArea(h)), 0);
}

export function centroid(r: Ring): Vec2 {
  const a = signedArea(r);
  if (Math.abs(a) < 1e-12) {
    const n = r.length || 1;
    return [r.reduce((s, p) => s + p[0], 0) / n, r.reduce((s, p) => s + p[1], 0) / n];
  }
  let cx = 0;
  let cy = 0;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
    const f = r[j][0] * r[i][1] - r[i][0] * r[j][1];
    cx += (r[j][0] + r[i][0]) * f;
    cy += (r[j][1] + r[i][1]) * f;
  }
  return [cx / (6 * a), cy / (6 * a)];
}

export function pointInRing(pt: Vec2, r: Ring): boolean {
  let inside = false;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
    const [xi, yi] = r[i];
    const [xj, yj] = r[j];
    if (yi > pt[1] !== yj > pt[1] && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export function pointInPolygon(pt: Vec2, p: Polygon): boolean {
  return pointInRing(pt, p.outer) && !(p.holes ?? []).some((h) => pointInRing(pt, h));
}

export function pointSegmentDistance(p: Vec2, a: Vec2, b: Vec2): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const len2 = dx * dx + dy * dy;
  let t = len2 === 0 ? 0 : ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}

function orient(a: Vec2, b: Vec2, c: Vec2): number {
  return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
}

export function segmentsIntersect(a: Vec2, b: Vec2, c: Vec2, d: Vec2): boolean {
  const o1 = orient(a, b, c);
  const o2 = orient(a, b, d);
  const o3 = orient(c, d, a);
  const o4 = orient(c, d, b);
  return o1 * o2 < 0 && o3 * o4 < 0;
}

export function segmentDistance(a: Vec2, b: Vec2, c: Vec2, d: Vec2): number {
  if (segmentsIntersect(a, b, c, d)) return 0;
  return Math.min(pointSegmentDistance(a, c, d), pointSegmentDistance(b, c, d), pointSegmentDistance(c, a, b), pointSegmentDistance(d, a, b));
}

function edges(r: Ring, closed = true): Array<[Vec2, Vec2]> {
  const out: Array<[Vec2, Vec2]> = [];
  for (let i = 0; i < r.length - 1; i++) out.push([r[i], r[i + 1]]);
  if (closed && r.length > 2) out.push([r[r.length - 1], r[0]]);
  return out;
}

/** Minimum distance between two polylines (open or closed). */
export function lineDistance(a: Ring, b: Ring, aClosed = true, bClosed = true): number {
  let best = Infinity;
  for (const [p, q] of edges(a, aClosed)) {
    for (const [r, s] of edges(b, bClosed)) {
      const d = segmentDistance(p, q, r, s);
      if (d < best) best = d;
      if (best === 0) return 0;
    }
  }
  return best;
}

/** Distance between two polygon areas: 0 when they overlap or touch. */
export function polygonDistance(a: Polygon, b: Polygon): number {
  if (a.outer.some((p) => pointInPolygon(p, b)) || b.outer.some((p) => pointInPolygon(p, a))) return 0;
  return lineDistance(a.outer, b.outer);
}

/** Distance from the polygon to a polyline (roads); 0 if the line crosses the polygon. */
export function distanceToPolyline(p: Polygon, line: Ring, lineClosed = false): number {
  if (line.some((pt) => pointInRing(pt, p.outer))) return 0;
  return lineDistance(p.outer, line, true, lineClosed);
}

/** Distance from a point to the edges of a closed ring. */
export function ringDistance(p: Vec2, r: Ring): number {
  let best = Infinity;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) best = Math.min(best, pointSegmentDistance(p, r[j], r[i]));
  return best;
}

/**
 * Smallest distance from the inner polygon to the boundary of the outer one; when the inner
 * polygon sticks out, minus how far its farthest vertex lies outside.
 */
export function insetDistance(inner: Polygon, outer: Polygon): number {
  const outside = inner.outer.filter((p) => !pointInRing(p, outer.outer));
  if (outside.length > 0) return -Math.max(...outside.map((p) => ringDistance(p, outer.outer)));
  return lineDistance(inner.outer, outer.outer);
}

export function bounds(points: Vec2[]): { min: Vec2; max: Vec2 } {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const [x, y] of points) {
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  return { min: [minX, minY], max: [maxX, maxY] };
}

export function polylineLength(pl: Vec2[]): number {
  let l = 0;
  for (let i = 1; i < pl.length; i++) l += Math.hypot(pl[i][0] - pl[i - 1][0], pl[i][1] - pl[i - 1][1]);
  return l;
}

/** Area covered by the union of discs (canopy cover), estimated on a grid of `resolution`. */
export function discUnionArea(discs: Array<{ c: Vec2; r: number }>, resolution: number, clip?: Polygon): number {
  if (discs.length === 0) return 0;
  const b = bounds(discs.flatMap((d) => [[d.c[0] - d.r, d.c[1] - d.r] as Vec2, [d.c[0] + d.r, d.c[1] + d.r] as Vec2]));
  const cell = resolution;
  let count = 0;
  for (let x = b.min[0] + cell / 2; x < b.max[0]; x += cell) {
    for (let y = b.min[1] + cell / 2; y < b.max[1]; y += cell) {
      if (clip && !pointInPolygon([x, y], clip)) continue;
      if (discs.some((d) => (x - d.c[0]) ** 2 + (y - d.c[1]) ** 2 <= d.r * d.r)) count++;
    }
  }
  return count * cell * cell;
}
