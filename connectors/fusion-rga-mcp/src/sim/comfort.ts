/**
 * Pedestrian wind comfort from a CFD velocity field at pedestrian height.
 * Classes follow the Lawson (LDDC) comfort thresholds on the wind speed; with a single
 * simulated wind (one direction, one reference speed) they describe that wind only, not the
 * annual comfort, which needs the local wind statistics for every direction.
 */
export const LAWSON = [
  { class: "sitting", label: "Assis (long séjour)", max: 4 },
  { class: "standing", label: "Debout / court séjour", max: 6 },
  { class: "strolling", label: "Promenade", max: 8 },
  { class: "walking", label: "Marche rapide", max: 10 },
  { class: "uncomfortable", label: "Inconfortable", max: Infinity },
] as const;

export function lawsonClass(speed: number): (typeof LAWSON)[number]["class"] {
  return LAWSON.find((c) => speed < c.max)!.class;
}

export interface WindSample {
  x: number;
  y: number;
  speed: number;
}

export function comfortStats(samples: WindSample[], referenceSpeed: number, cellArea = 1) {
  if (samples.length === 0) throw new Error("No velocity samples.");
  const speeds = samples.map((s) => s.speed);
  const counts: Record<string, number> = Object.fromEntries(LAWSON.map((c) => [c.class, 0]));
  for (const s of speeds) counts[lawsonClass(s)]++;
  const pct = (n: number) => Math.round((n / samples.length) * 10000) / 100;
  const mean = speeds.reduce((a, b) => a + b, 0) / speeds.length;
  const sorted = [...speeds].sort((a, b) => a - b);
  return {
    samples: samples.length,
    mean_speed: Math.round(mean * 100) / 100,
    max_speed: Math.round(sorted[sorted.length - 1] * 100) / 100,
    p95_speed: Math.round(sorted[Math.min(sorted.length - 1, Math.floor(0.95 * sorted.length))] * 100) / 100,
    mean_speed_ratio: referenceSpeed > 0 ? Math.round((mean / referenceSpeed) * 1000) / 1000 : null,
    area_pct: Object.fromEntries(Object.entries(counts).map(([k, n]) => [k, pct(n)])),
    area_m2: Object.fromEntries(Object.entries(counts).map(([k, n]) => [k, Math.round(n * cellArea)])),
  };
}

/** Reads "x,y,z,speed" (or x,y,speed) CSV lines; header lines are skipped. */
export function parseVelocityCsv(text: string): WindSample[] {
  const out: WindSample[] = [];
  for (const line of text.split(/\r?\n/)) {
    const cells = line.split(/[,;\t ]+/).filter(Boolean).map(Number);
    if (cells.length < 3 || cells.some((v) => !Number.isFinite(v))) continue;
    out.push({ x: cells[0], y: cells[1], speed: cells[cells.length - 1] });
  }
  return out;
}

/** Log-law atmospheric boundary layer: U(z) = U_ref · ln((z + z0)/z0) / ln((z_ref + z0)/z0). */
export function logLawSpeed(z: number, uRef: number, zRef = 10, z0 = 0.5): number {
  return (uRef * Math.log((z + z0) / z0)) / Math.log((zRef + z0) / z0);
}
