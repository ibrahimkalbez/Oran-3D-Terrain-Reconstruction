import type { CrownShape } from "./catalog.js";

export interface MeshData {
  vertices: number[][];
  faces: number[][];
}

export type Lod = "low" | "medium" | "high";
const SEGMENTS: Record<Lod, [number, number]> = { low: [8, 4], medium: [14, 7], high: [24, 12] };

function append(target: MeshData, part: MeshData): void {
  const offset = target.vertices.length;
  target.vertices.push(...part.vertices);
  target.faces.push(...part.faces.map((f) => f.map((i) => i + offset)));
}

/** Closed cylinder (trunk). */
export function cylinder(x: number, y: number, z: number, radius: number, height: number, seg: number): MeshData {
  const vertices: number[][] = [];
  const faces: number[][] = [];
  for (let i = 0; i < seg; i++) {
    const a = (2 * Math.PI * i) / seg;
    vertices.push([x + radius * Math.cos(a), y + radius * Math.sin(a), z]);
    vertices.push([x + radius * Math.cos(a), y + radius * Math.sin(a), z + height]);
  }
  const bottom = vertices.length;
  vertices.push([x, y, z]);
  const top = vertices.length;
  vertices.push([x, y, z + height]);
  for (let i = 0; i < seg; i++) {
    const j = (i + 1) % seg;
    faces.push([2 * i, 2 * j, 2 * j + 1, 2 * i + 1]);
    faces.push([bottom, 2 * j, 2 * i]);
    faces.push([top, 2 * i + 1, 2 * j + 1]);
  }
  return { vertices, faces };
}

/**
 * Closed crown of revolution around (x, y) between z0 and z1 with maximum radius r.
 * `profile(t)` gives the radius ratio at height ratio t ∈ [0, 1].
 */
function revolve(x: number, y: number, z0: number, z1: number, r: number, profile: (t: number) => number, seg: number, rings: number): MeshData {
  const vertices: number[][] = [[x, y, z0]];
  const faces: number[][] = [];
  for (let k = 1; k < rings; k++) {
    const t = k / rings;
    const rr = r * Math.max(0, profile(t));
    for (let i = 0; i < seg; i++) {
      const a = (2 * Math.PI * i) / seg;
      vertices.push([x + rr * Math.cos(a), y + rr * Math.sin(a), z0 + (z1 - z0) * t]);
    }
  }
  const topIndex = vertices.length;
  vertices.push([x, y, z1]);
  const ring = (k: number, i: number) => 1 + (k - 1) * seg + (i % seg);
  for (let i = 0; i < seg; i++) faces.push([0, ring(1, i + 1), ring(1, i)]);
  for (let k = 1; k < rings - 1; k++) for (let i = 0; i < seg; i++) faces.push([ring(k, i), ring(k, i + 1), ring(k + 1, i + 1), ring(k + 1, i)]);
  for (let i = 0; i < seg; i++) faces.push([topIndex, ring(rings - 1, i), ring(rings - 1, i + 1)]);
  return { vertices, faces };
}

const PROFILES: Record<Exclude<CrownShape, "palm">, (t: number) => number> = {
  round: (t) => Math.sqrt(Math.max(0, 1 - (2 * t - 1) ** 2)),
  oval: (t) => Math.sqrt(Math.max(0, 1 - (2 * t - 1) ** 2)),
  umbrella: (t) => (t < 0.35 ? Math.sqrt(Math.max(0, 1 - ((0.35 - t) / 0.35) ** 2)) * 0.95 + 0.05 : Math.sqrt(Math.max(0, 1 - ((t - 0.35) / 0.65) ** 2))),
  cone: (t) => 1 - t,
  columnar: (t) => Math.sqrt(Math.max(0, 1 - (2 * t - 1) ** 2)) * (1 - 0.3 * t),
};

/** Palm: a flat star of drooping fronds made of thin closed wedges. */
function palmCrown(x: number, y: number, z: number, r: number, fronds: number): MeshData {
  const out: MeshData = { vertices: [], faces: [] };
  const thickness = Math.max(0.08, r * 0.04);
  for (let i = 0; i < fronds; i++) {
    const a = (2 * Math.PI * i) / fronds;
    const w = (Math.PI / fronds) * 0.6;
    const tip = [x + r * Math.cos(a), y + r * Math.sin(a), z - r * 0.35];
    const l = [x + 0.25 * r * Math.cos(a - w), y + 0.25 * r * Math.sin(a - w), z + 0.15 * r];
    const rr = [x + 0.25 * r * Math.cos(a + w), y + 0.25 * r * Math.sin(a + w), z + 0.15 * r];
    const c = [x, y, z];
    const part: MeshData = {
      vertices: [c, l, tip, rr, [c[0], c[1], c[2] - thickness], [l[0], l[1], l[2] - thickness], [tip[0], tip[1], tip[2] - thickness], [rr[0], rr[1], rr[2] - thickness]],
      faces: [
        [0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6],
        [0, 4, 5, 1], [1, 5, 6, 2], [2, 6, 7, 3], [3, 7, 4, 0],
      ],
    };
    append(out, part);
  }
  return out;
}

/** One tree as a single mesh (trunk + crown), base at (x, y, z). */
export function treeMesh(x: number, y: number, z: number, height: number, crownDiameter: number, trunkHeight: number, shape: CrownShape, lod: Lod = "low"): MeshData {
  const [seg, rings] = SEGMENTS[lod];
  const out: MeshData = { vertices: [], faces: [] };
  const trunkRadius = Math.max(0.08, Math.min(0.6, crownDiameter * 0.035, height * 0.03));
  const r = crownDiameter / 2;
  if (shape === "palm") {
    append(out, cylinder(x, y, z, trunkRadius, trunkHeight + 0.3, Math.max(6, seg / 2)));
    append(out, palmCrown(x, y, z + trunkHeight + 0.3, r, lod === "low" ? 8 : 12));
    return out;
  }
  const crownBase = z + Math.min(trunkHeight, height * 0.9);
  // The trunk enters the crown so the two closed shells overlap (no gap when printed).
  append(out, cylinder(x, y, z, trunkRadius, Math.min(trunkHeight + (height - trunkHeight) * 0.3, height), Math.max(6, Math.round(seg / 2))));
  append(out, revolve(x, y, crownBase, z + height, r, PROFILES[shape], seg, rings));
  return out;
}
