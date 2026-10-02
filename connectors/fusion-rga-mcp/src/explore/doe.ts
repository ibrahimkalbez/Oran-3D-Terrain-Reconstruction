import { rng } from "../geometry/sampling.js";

/** Requested range of a parameter; missing bounds are taken from the Grasshopper slider. */
export interface DimensionSpec {
  min?: number;
  max?: number;
  steps?: number;
  step?: number;
  values?: unknown[];
  integer?: boolean;
}

export interface Dimension {
  name: string;
  kind: "continuous" | "integer" | "discrete";
  min: number;
  max: number;
  decimals: number;
  values?: unknown[];
  steps?: number;
  step?: number;
}

export interface InputInfo {
  name: string;
  id?: string;
  kind?: string;
  value?: unknown;
  min?: number;
  max?: number;
  decimals?: number;
  step_type?: string;
  items?: Array<{ name: string }>;
}

/** Builds the design space from the request and the definition inputs (slider ranges, list items). */
export function resolveDimensions(space: Record<string, DimensionSpec>, inputs: InputInfo[]): Dimension[] {
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
  return Object.entries(space).map(([name, spec]) => {
    const input = inputs.find((i) => i.name === name || norm(i.name) === norm(name) || i.id === name);
    const realName = input?.name ?? name;
    if (spec.values?.length) return { name: realName, kind: "discrete" as const, min: 0, max: spec.values.length - 1, decimals: 0, values: spec.values };
    if (input?.kind === "value_list" && input.items?.length && spec.min === undefined)
      return { name: realName, kind: "discrete" as const, min: 0, max: input.items.length - 1, decimals: 0, values: input.items.map((i) => i.name) };
    if (input?.kind === "toggle") return { name: realName, kind: "discrete" as const, min: 0, max: 1, decimals: 0, values: [false, true] };
    const min = spec.min ?? input?.min;
    const max = spec.max ?? input?.max;
    if (min === undefined || max === undefined) throw new Error(`Give min and max for '${name}' (no slider range found).`);
    if (max < min) throw new Error(`'${name}': max < min.`);
    const integer = spec.integer ?? (input?.step_type === "integer" || input?.step_type === "even" || input?.step_type === "odd" || input?.decimals === 0);
    return {
      name: realName,
      kind: integer ? "integer" : "continuous",
      min,
      max,
      decimals: integer ? 0 : input?.decimals ?? 2,
      steps: spec.steps,
      step: spec.step,
    };
  });
}

const roundTo = (v: number, d: number) => Math.round(v * 10 ** d) / 10 ** d;

/** Value of a dimension at u ∈ [0, 1]. */
export function valueAt(dim: Dimension, u: number): unknown {
  const t = Math.max(0, Math.min(1, u));
  if (dim.kind === "discrete") return dim.values![Math.min(dim.values!.length - 1, Math.floor(t * dim.values!.length))];
  const v = dim.min + t * (dim.max - dim.min);
  return dim.kind === "integer" ? Math.round(v) : roundTo(v, dim.decimals);
}

function levels(dim: Dimension, defaultSteps: number): unknown[] {
  if (dim.kind === "discrete") return dim.values!;
  let values: number[];
  if (dim.step) {
    values = [];
    for (let v = dim.min; v <= dim.max + 1e-9; v += dim.step) values.push(v);
  } else {
    const n = Math.max(2, dim.steps ?? defaultSteps);
    values = Array.from({ length: n }, (_, i) => dim.min + ((dim.max - dim.min) * i) / (n - 1));
  }
  const out = values.map((v) => (dim.kind === "integer" ? Math.round(v) : roundTo(v, dim.decimals)));
  return [...new Set(out)];
}

/** Full factorial grid. */
export function gridDesign(dims: Dimension[], defaultSteps = 3): Array<Record<string, unknown>> {
  let combos: Array<Record<string, unknown>> = [{}];
  for (const d of dims) combos = combos.flatMap((c) => levels(d, defaultSteps).map((v) => ({ ...c, [d.name]: v })));
  return combos;
}

export function randomDesign(dims: Dimension[], n: number, seed = 1): Array<Record<string, unknown>> {
  const random = rng(seed);
  return Array.from({ length: n }, () => Object.fromEntries(dims.map((d) => [d.name, valueAt(d, random())])));
}

/** Latin hypercube: each parameter range is cut into n strata, each stratum used once. */
export function lhsDesign(dims: Dimension[], n: number, seed = 1): Array<Record<string, unknown>> {
  const random = rng(seed);
  const columns = dims.map(() => {
    const perm = Array.from({ length: n }, (_, i) => i);
    for (let i = n - 1; i > 0; i--) {
      const j = Math.floor(random() * (i + 1));
      [perm[i], perm[j]] = [perm[j], perm[i]];
    }
    return perm.map((k) => (k + random()) / n);
  });
  return Array.from({ length: n }, (_, row) => Object.fromEntries(dims.map((d, c) => [d.name, valueAt(d, columns[c][row])])));
}

/** Removes duplicate parameter sets (integer rounding can create them). */
export function unique(designs: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
  const seen = new Set<string>();
  return designs.filter((d) => {
    const k = JSON.stringify(d);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

/** Indices of the non-dominated rows for the given objectives (Pareto front). */
export function paretoFront(rows: Array<Record<string, unknown>>, objectives: Record<string, "max" | "min">): number[] {
  const keys = Object.keys(objectives);
  const val = (r: Record<string, unknown>, k: string) => {
    const v = Number(r[k]);
    if (!Number.isFinite(v)) return objectives[k] === "max" ? -Infinity : Infinity;
    return objectives[k] === "max" ? v : -v;
  };
  const front: number[] = [];
  for (let i = 0; i < rows.length; i++) {
    let dominated = false;
    for (let j = 0; j < rows.length && !dominated; j++) {
      if (i === j) continue;
      let better = false;
      let worse = false;
      for (const k of keys) {
        const a = val(rows[j], k);
        const b = val(rows[i], k);
        if (a > b) better = true;
        else if (a < b) worse = true;
      }
      if (better && !worse) dominated = true;
    }
    if (!dominated) front.push(i);
  }
  return front;
}
