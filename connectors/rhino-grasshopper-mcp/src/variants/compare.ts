/** Pure comparison helpers (unit-tested in test/compare.test.mjs). */

export type MetricValue = number | null | undefined;
export type Metrics = Record<string, unknown>;

export interface MetricChange {
  before: MetricValue;
  after: MetricValue;
  delta?: number;
  delta_pct?: number;
}

const round = (v: number, d = 6) => Math.round(v * 10 ** d) / 10 ** d;

function num(v: unknown): number | undefined {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return undefined;
}

/** Metric-by-metric difference; unchanged metrics are left out unless `includeUnchanged`. */
export function diffMetrics(before: Metrics | undefined, after: Metrics | undefined, includeUnchanged = false): Record<string, MetricChange> {
  const out: Record<string, MetricChange> = {};
  const keys = new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})]);
  for (const key of [...keys].sort()) {
    const b = num(before?.[key]);
    const a = num(after?.[key]);
    if (b === undefined && a === undefined) continue;
    if (!includeUnchanged && b !== undefined && a !== undefined && Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(b))) continue;
    const change: MetricChange = { before: b ?? null, after: a ?? null };
    if (a !== undefined && b !== undefined) {
      change.delta = round(a - b);
      if (b !== 0) change.delta_pct = round(((a - b) / Math.abs(b)) * 100, 2);
    }
    out[key] = change;
  }
  return out;
}

export interface VariantLike {
  id: string;
  name: string;
  parameters: Record<string, unknown>;
  metrics: Metrics;
  kept?: boolean;
}

export interface CompareOptions {
  metrics?: string[];
  baseline?: string;
  /** Ranking: {metric: "max" | "min"}; metrics are min-max normalised then averaged (weights optional). */
  objectives?: Record<string, "max" | "min">;
  weights?: Record<string, number>;
}

export interface Comparison {
  baseline: string;
  parameters: string[];
  metrics: string[];
  rows: Array<{
    id: string;
    name: string;
    kept: boolean;
    parameters: Record<string, unknown>;
    metrics: Record<string, number | null>;
    delta_pct: Record<string, number | null>;
    score?: number;
    rank?: number;
  }>;
  best?: { id: string; name: string; score: number };
  table: string;
}

export function compareVariants(variants: VariantLike[], options: CompareOptions = {}): Comparison {
  if (variants.length === 0) throw new Error("No variants to compare.");
  const base = (options.baseline && variants.find((v) => v.id === options.baseline || v.name === options.baseline)) || variants[0];

  // Parameters that actually differ between the variants.
  const paramKeys = [...new Set(variants.flatMap((v) => Object.keys(v.parameters ?? {})))].filter((k) => {
    const values = new Set(variants.map((v) => JSON.stringify(v.parameters?.[k] ?? null)));
    return values.size > 1;
  });

  const numericMetrics = [...new Set(variants.flatMap((v) => Object.keys(v.metrics ?? {})))].filter((k) =>
    variants.some((v) => num(v.metrics?.[k]) !== undefined),
  );
  const metricKeys = options.metrics?.length
    ? options.metrics.filter((m) => numericMetrics.includes(m) || variants.some((v) => m in (v.metrics ?? {})))
    : numericMetrics;

  const rows: Comparison["rows"] = variants.map((v) => {
    const metrics: Record<string, number | null> = {};
    const delta: Record<string, number | null> = {};
    for (const m of metricKeys) {
      const value = num(v.metrics?.[m]);
      const b = num(base.metrics?.[m]);
      metrics[m] = value ?? null;
      delta[m] = value !== undefined && b !== undefined && b !== 0 ? round(((value - b) / Math.abs(b)) * 100, 2) : null;
    }
    const parameters: Record<string, unknown> = {};
    for (const k of paramKeys) parameters[k] = v.parameters?.[k] ?? null;
    return { id: v.id, name: v.name, kept: Boolean(v.kept), parameters, metrics, delta_pct: delta };
  });

  let best: Comparison["best"];
  const objectives = Object.entries(options.objectives ?? {}).filter(([m]) => metricKeys.includes(m));
  if (objectives.length > 0) {
    for (const row of rows) row.score = 0;
    let totalWeight = 0;
    for (const [m, direction] of objectives) {
      const w = options.weights?.[m] ?? 1;
      totalWeight += w;
      const values = rows.map((r) => r.metrics[m]).filter((x): x is number => x !== null);
      const min = Math.min(...values);
      const max = Math.max(...values);
      for (const row of rows) {
        const v = row.metrics[m];
        let s = v === null ? 0 : max === min ? 1 : (v - min) / (max - min);
        if (direction === "min" && v !== null) s = 1 - s;
        row.score! += w * s;
      }
    }
    for (const row of rows) row.score = round(row.score! / totalWeight, 4);
    const ranked = [...rows].sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
    ranked.forEach((r, i) => (r.rank = i + 1));
    best = { id: ranked[0].id, name: ranked[0].name, score: ranked[0].score! };
  }

  return { baseline: base.id, parameters: paramKeys, metrics: metricKeys, rows, best, table: markdownTable(rows, paramKeys, metricKeys, base.id) };
}

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "–";
  if (typeof v === "number") {
    const abs = Math.abs(v);
    if (abs >= 1000) return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
    if (abs >= 10) return v.toFixed(1).replace(/\.0$/, "");
    return String(round(v, 3));
  }
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v).slice(0, 30);
}

function markdownTable(rows: Comparison["rows"], params: string[], metrics: string[], baseline: string): string {
  const head = ["Variant", ...params, ...metrics, ...(rows.some((r) => r.rank) ? ["Score", "Rank"] : [])];
  const lines = [`| ${head.join(" | ")} |`, `|${head.map(() => "---").join("|")}|`];
  for (const r of rows) {
    const label = `${r.name && r.name !== r.id ? `${r.id} ${r.name}` : r.id}${r.kept ? " ★" : ""}${r.id === baseline ? " (ref)" : ""}`;
    const cells = [
      label,
      ...params.map((p) => fmt(r.parameters[p])),
      ...metrics.map((m) => {
        const d = r.delta_pct[m];
        return r.id === baseline || d === null || d === 0 ? fmt(r.metrics[m]) : `${fmt(r.metrics[m])} (${d > 0 ? "+" : ""}${d}%)`;
      }),
      ...(r.rank ? [fmt(r.score), String(r.rank)] : []),
    ];
    lines.push(`| ${cells.join(" | ")} |`);
  }
  return lines.join("\n");
}

/** Cartesian product of a parameter sweep: {H: [12, 15], W: [8, 10]} → 4 combinations. */
export function sweepCombinations(sweep: Record<string, unknown[]>): Array<Record<string, unknown>> {
  let combos: Array<Record<string, unknown>> = [{}];
  for (const [name, values] of Object.entries(sweep)) {
    if (!Array.isArray(values) || values.length === 0) throw new Error(`Sweep '${name}' needs a non-empty list of values.`);
    combos = combos.flatMap((c) => values.map((v) => ({ ...c, [name]: v })));
  }
  return combos;
}

/** Values from {min, max, steps} or {start, stop, step}. */
export function range(spec: { min?: number; max?: number; steps?: number; start?: number; stop?: number; step?: number }): number[] {
  if (spec.steps !== undefined && spec.min !== undefined && spec.max !== undefined) {
    if (spec.steps < 2) return [spec.min];
    return Array.from({ length: spec.steps }, (_, i) => round(spec.min! + ((spec.max! - spec.min!) * i) / (spec.steps! - 1)));
  }
  if (spec.start !== undefined && spec.stop !== undefined && spec.step) {
    const out: number[] = [];
    for (let v = spec.start; spec.step > 0 ? v <= spec.stop + 1e-9 : v >= spec.stop - 1e-9; v += spec.step) out.push(round(v));
    return out;
  }
  throw new Error("Range needs {min, max, steps} or {start, stop, step}.");
}
