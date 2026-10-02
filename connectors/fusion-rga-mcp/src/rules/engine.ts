import {
  centroid,
  distanceToPolyline,
  insetDistance,
  pointInPolygon,
  polygonArea,
  polygonDistance,
  type Polygon,
  type Vec2,
} from "../geometry/polygon.js";
import { evaluate } from "./expression.js";

// ----------------------------------------------------------------------------- site model

export interface Building {
  id: string;
  name: string;
  layer?: string | null;
  parts: Polygon[];
  area: number;
  base_z: number;
  top_z: number;
  height: number;
  floors?: number;
  volume?: number;
  centroid: Vec2;
  use?: string;
}

export interface Plot {
  id: string;
  name: string;
  polygon: Polygon;
  area: number;
  zone?: string;
}

export interface Line {
  id: string;
  name: string;
  points: Vec2[];
  closed: boolean;
}

export interface Area {
  id: string;
  name: string;
  polygon: Polygon;
  area: number;
}

export interface SiteModel {
  buildings: Building[];
  plots: Plot[];
  roads: Line[];
  green: Area[];
  /** Site area used when there are no plots (e.g. the area of the study perimeter). */
  site_area?: number;
}

// ----------------------------------------------------------------------------- rules

export const RULE_TYPES = [
  "max_height",
  "min_height",
  "max_floors",
  "max_footprint",
  "max_coverage",
  "max_far",
  "min_green_ratio",
  "min_boundary_setback",
  "min_street_setback",
  "min_building_spacing",
  "metric_max",
  "metric_min",
  "metric_range",
  "expression",
] as const;
export type RuleType = (typeof RULE_TYPES)[number];

export interface Rule {
  id: string;
  type: RuleType;
  label?: string;
  severity?: "error" | "warning";
  value?: number;
  min?: number;
  max?: number;
  /** Multiplier of the building height (setbacks, spacing): limit = max(min, ratio × H). */
  ratio?: number;
  metric?: string;
  expression?: string;
  /** Spacing/setback rules: buildings built on the line (distance 0) are accepted. */
  allow_contiguous?: boolean;
  scope?: { layer?: string; zone?: string; use?: string };
}

export interface RuleSet {
  name: string;
  description?: string;
  reference?: string;
  /** Storey height used to derive floors from heights when objects carry no 'floors' user text. */
  floor_height?: number;
  tolerance?: number;
  rules: Rule[];
}

export interface Violation {
  object_id: string;
  object: string;
  measured: number;
  limit: number;
  detail?: string;
}

export type RuleStatus = "pass" | "fail" | "not_applicable" | "error";

export interface RuleResult {
  id: string;
  type: RuleType;
  label: string;
  severity: "error" | "warning";
  status: RuleStatus;
  limit?: number | string;
  measured?: number;
  message?: string;
  violations: Violation[];
}

export interface RuleReport {
  rule_set: string;
  compliant: boolean;
  score: number;
  passed: number;
  failed: number;
  warnings: number;
  not_applicable: number;
  results: RuleResult[];
  violating_ids: string[];
  variables: Record<string, number>;
}

const round = (v: number, d = 3) => Math.round(v * 10 ** d) / 10 ** d;

export function floorsOf(b: Building, floorHeight: number): number {
  if (b.floors && b.floors > 0) return b.floors;
  return Math.max(1, Math.round(b.height / floorHeight));
}

function inScope(rule: Rule, b: Building): boolean {
  if (rule.scope?.layer && !(b.layer ?? "").toLowerCase().startsWith(rule.scope.layer.toLowerCase())) return false;
  if (rule.scope?.use && (b.use ?? "").toLowerCase() !== rule.scope.use.toLowerCase()) return false;
  return true;
}

function plotInScope(rule: Rule, p: Plot): boolean {
  if (!rule.scope?.zone) return true;
  const z = rule.scope.zone.toLowerCase();
  return (p.zone ?? "").toLowerCase() === z || p.name.toLowerCase() === z;
}

/** The plot holding the building (by centroid). */
export function plotOf(b: Building, plots: Plot[]): Plot | undefined {
  return plots.find((p) => pointInPolygon(b.centroid, p.polygon));
}

/** Aggregates available to expression and metric rules (merged with Grasshopper metrics). */
export function siteVariables(site: SiteModel, floorHeight: number): Record<string, number> {
  const footprint = site.buildings.reduce((s, b) => s + b.area, 0);
  const gfa = site.buildings.reduce((s, b) => s + b.area * floorsOf(b, floorHeight), 0);
  const siteArea = site.plots.length > 0 ? site.plots.reduce((s, p) => s + p.area, 0) : site.site_area ?? 0;
  const green = site.green.reduce((s, g) => s + g.area, 0);
  const heights = site.buildings.map((b) => b.height);
  const vars: Record<string, number> = {
    building_count: site.buildings.length,
    plot_count: site.plots.length,
    footprint_area: round(footprint),
    gfa: round(gfa),
    height_max: heights.length ? round(Math.max(...heights)) : 0,
    height_mean: heights.length ? round(heights.reduce((a, b) => a + b, 0) / heights.length) : 0,
    floors_max: site.buildings.length ? Math.max(...site.buildings.map((b) => floorsOf(b, floorHeight))) : 0,
    green_area: round(green),
    road_length: round(site.roads.reduce((s, r) => s + lineLength(r.points), 0)),
  };
  if (siteArea > 0) {
    vars.site_area = round(siteArea);
    vars.coverage = round(footprint / siteArea, 4);
    vars.far = round(gfa / siteArea, 4);
    vars.green_ratio = round(green / siteArea, 4);
  }
  return vars;
}

function lineLength(pts: Vec2[]): number {
  let l = 0;
  for (let i = 1; i < pts.length; i++) l += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  return l;
}

export function evaluateRules(site: SiteModel, metrics: Record<string, unknown>, ruleSet: RuleSet): RuleReport {
  const fh = ruleSet.floor_height ?? 3;
  const tol = ruleSet.tolerance ?? 0.01;
  const vars: Record<string, number> = { ...siteVariables(site, fh) };
  for (const [k, v] of Object.entries(metrics ?? {})) {
    const n = typeof v === "number" ? v : typeof v === "string" ? Number(v) : NaN;
    if (Number.isFinite(n)) vars[k] = n;
  }

  const results = ruleSet.rules.map((rule) => {
    try {
      return evaluateRule(rule, site, vars, fh, tol);
    } catch (err) {
      return base(rule, "error", { message: (err as Error).message });
    }
  });

  const applicable = results.filter((r) => r.status === "pass" || r.status === "fail");
  const failedErrors = results.filter((r) => r.status === "fail" && r.severity === "error");
  const violating = new Set(results.flatMap((r) => r.violations.flatMap((v) => v.object_id.split("|"))));
  return {
    rule_set: ruleSet.name,
    compliant: failedErrors.length === 0 && !results.some((r) => r.status === "error"),
    score: applicable.length ? round(applicable.filter((r) => r.status === "pass").length / applicable.length, 3) : 1,
    passed: results.filter((r) => r.status === "pass").length,
    failed: failedErrors.length,
    warnings: results.filter((r) => r.status === "fail" && r.severity === "warning").length,
    not_applicable: results.filter((r) => r.status === "not_applicable").length,
    results,
    violating_ids: [...violating].filter((id) => site.buildings.some((b) => b.id === id)),
    variables: vars,
  };
}

function base(rule: Rule, status: RuleStatus, extra: Partial<RuleResult> = {}): RuleResult {
  return {
    id: rule.id,
    type: rule.type,
    label: rule.label ?? rule.id,
    severity: rule.severity ?? "error",
    status,
    violations: [],
    ...extra,
  };
}

function evaluateRule(rule: Rule, site: SiteModel, vars: Record<string, number>, fh: number, tol: number): RuleResult {
  const buildings = site.buildings.filter((b) => inScope(rule, b));
  const plots = site.plots.filter((p) => plotInScope(rule, p));
  const need = (v: number | undefined, name: string) => {
    if (v === undefined || !Number.isFinite(v)) throw new Error(`Rule '${rule.id}' needs '${name}'.`);
    return v;
  };
  const perBuilding = (measure: (b: Building) => number, limit: number, worse: (m: number, l: number) => boolean, detail?: string): RuleResult => {
    if (buildings.length === 0) return base(rule, "not_applicable", { limit, message: "No building in scope." });
    const violations: Violation[] = [];
    let worst: number | undefined;
    for (const b of buildings) {
      const m = measure(b);
      if (worst === undefined || worse(m, worst)) worst = m;
      if (worse(m, limit)) violations.push({ object_id: b.id, object: b.name || b.id, measured: round(m), limit, detail });
    }
    return base(rule, violations.length ? "fail" : "pass", { limit, measured: worst !== undefined ? round(worst) : undefined, violations });
  };
  const greater = (m: number, l: number) => m > l + tol;
  const smaller = (m: number, l: number) => m < l - tol;

  // Ratio of a quantity over plot areas; falls back to the whole site.
  const perPlot = (numerator: (p: Plot | undefined) => number, limit: number, worse: (m: number, l: number) => boolean): RuleResult => {
    if (plots.length > 0) {
      const violations: Violation[] = [];
      let worst: number | undefined;
      for (const p of plots) {
        if (p.area <= 0) continue;
        const ratio = numerator(p) / p.area;
        if (worst === undefined || worse(ratio, worst)) worst = ratio;
        if (worse(ratio, limit)) violations.push({ object_id: p.id, object: p.name || p.id, measured: round(ratio, 4), limit });
      }
      return base(rule, violations.length ? "fail" : "pass", { limit, measured: worst !== undefined ? round(worst, 4) : undefined, violations });
    }
    if (vars.site_area > 0) {
      const ratio = numerator(undefined) / vars.site_area;
      const fail = worse(ratio, limit);
      return base(rule, fail ? "fail" : "pass", {
        limit,
        measured: round(ratio, 4),
        violations: fail ? [{ object_id: "site", object: "site", measured: round(ratio, 4), limit }] : [],
      });
    }
    return base(rule, "not_applicable", { limit, message: "No plots and no site_area: give the plots layer or site_area." });
  };
  const inPlot = (b: Building, p: Plot | undefined) => (p ? pointInPolygon(b.centroid, p.polygon) : true);
  const ratioTol = (m: number, l: number) => m > l + 1e-6;
  const ratioMin = (m: number, l: number) => m < l - 1e-6;

  switch (rule.type) {
    case "max_height":
      return perBuilding((b) => b.height, need(rule.value, "value"), greater);
    case "min_height":
      return perBuilding((b) => b.height, need(rule.value, "value"), smaller);
    case "max_floors":
      return perBuilding((b) => floorsOf(b, fh), need(rule.value, "value"), (m, l) => m > l);
    case "max_footprint":
      return perBuilding((b) => b.area, need(rule.value, "value"), greater);

    case "max_coverage":
      return perPlot((p) => buildings.filter((b) => inPlot(b, p)).reduce((s, b) => s + b.area, 0), need(rule.value, "value"), ratioTol);
    case "max_far":
      return perPlot((p) => buildings.filter((b) => inPlot(b, p)).reduce((s, b) => s + b.area * floorsOf(b, fh), 0), need(rule.value, "value"), ratioTol);
    case "min_green_ratio":
      return perPlot(
        (p) => site.green.filter((g) => (p ? pointInPolygon(centroid(g.polygon.outer), p.polygon) : true)).reduce((s, g) => s + g.area, 0),
        need(rule.value, "value"),
        ratioMin,
      );

    case "min_boundary_setback": {
      if (plots.length === 0) return base(rule, "not_applicable", { message: "No plots: give the plots layer." });
      const violations: Violation[] = [];
      let worst: number | undefined;
      let checked = 0;
      for (const b of buildings) {
        const p = plotOf(b, plots);
        if (!p) continue;
        checked++;
        const d = Math.min(...b.parts.map((part) => insetDistance(part, p.polygon)));
        const limit = Math.max(rule.min ?? 0, rule.value ?? 0, (rule.ratio ?? 0) * b.height);
        if (worst === undefined || d < worst) worst = d;
        if (d < -tol) violations.push({ object_id: b.id, object: b.name || b.id, measured: round(d), limit: round(limit), detail: `outside plot ${p.name || p.id}` });
        else if (d <= tol && rule.allow_contiguous) continue;
        else if (d < limit - tol) violations.push({ object_id: b.id, object: b.name || b.id, measured: round(d), limit: round(limit), detail: `plot ${p.name || p.id}` });
      }
      if (checked === 0) return base(rule, "not_applicable", { message: "No building lies inside a plot." });
      return base(rule, violations.length ? "fail" : "pass", { limit: describeLimit(rule), measured: worst !== undefined ? round(worst) : undefined, violations });
    }

    case "min_street_setback": {
      if (site.roads.length === 0) return base(rule, "not_applicable", { message: "No roads: give the roads layer." });
      return perBuilding(
        (b) => Math.min(...b.parts.flatMap((part) => site.roads.map((r) => distanceToPolyline(part, r.points, r.closed)))),
        need(rule.value ?? rule.min, "value"),
        smaller,
      );
    }

    case "min_building_spacing": {
      if (buildings.length < 2) return base(rule, "not_applicable", { message: "Fewer than two buildings." });
      const allowContiguous = rule.allow_contiguous ?? true;
      const violations: Violation[] = [];
      let worst: number | undefined;
      const boxes = buildings.map((b) => box(b));
      for (let i = 0; i < buildings.length; i++) {
        for (let j = i + 1; j < buildings.length; j++) {
          const a = buildings[i];
          const c = buildings[j];
          const limit = Math.max(rule.min ?? 0, rule.value ?? 0, (rule.ratio ?? 0) * Math.max(a.height, c.height));
          if (boxGap(boxes[i], boxes[j]) > limit + tol) continue;
          const d = Math.min(...a.parts.flatMap((pa) => c.parts.map((pc) => polygonDistance(pa, pc))));
          if (d <= tol && allowContiguous) continue;
          if (worst === undefined || d < worst) worst = d;
          if (d < limit - tol)
            violations.push({ object_id: `${a.id}|${c.id}`, object: `${a.name || a.id} ↔ ${c.name || c.id}`, measured: round(d), limit: round(limit) });
        }
      }
      return base(rule, violations.length ? "fail" : "pass", { limit: describeLimit(rule), measured: worst !== undefined ? round(worst) : undefined, violations });
    }

    case "metric_max":
    case "metric_min":
    case "metric_range": {
      const name = rule.metric ?? "";
      const key = Object.keys(vars).find((k) => k.toLowerCase() === name.toLowerCase());
      if (!key) return base(rule, "not_applicable", { message: `Metric '${name}' not available.` });
      const v = vars[key];
      const lo = rule.type === "metric_max" ? undefined : rule.type === "metric_min" ? need(rule.value ?? rule.min, "value") : rule.min;
      const hi = rule.type === "metric_min" ? undefined : rule.type === "metric_max" ? need(rule.value ?? rule.max, "value") : rule.max;
      const fail = (lo !== undefined && v < lo - 1e-9) || (hi !== undefined && v > hi + 1e-9);
      const limit = lo !== undefined && hi !== undefined ? `${lo}…${hi}` : (hi ?? lo)!;
      return base(rule, fail ? "fail" : "pass", {
        limit,
        measured: round(v, 4),
        violations: fail ? [{ object_id: key, object: key, measured: round(v, 4), limit: hi !== undefined && v > hi ? hi : lo ?? 0 }] : [],
      });
    }

    case "expression": {
      if (!rule.expression) throw new Error(`Rule '${rule.id}' needs 'expression'.`);
      const ok = evaluate(rule.expression, vars);
      return base(rule, ok ? "pass" : "fail", { limit: rule.expression });
    }
  }
  return base(rule, "error", { message: `Unknown rule type '${(rule as Rule).type}'.` });
}

function describeLimit(rule: Rule): string | number {
  const parts: string[] = [];
  if (rule.ratio) parts.push(`${rule.ratio}×H`);
  if (rule.value) parts.push(String(rule.value));
  if (rule.min) parts.push(`min ${rule.min}`);
  return parts.length === 1 && !rule.ratio ? Number(parts[0].replace("min ", "")) : parts.join(", ");
}

function box(b: Building) {
  const pts = b.parts.flatMap((p) => p.outer);
  return {
    minX: Math.min(...pts.map((p) => p[0])),
    minY: Math.min(...pts.map((p) => p[1])),
    maxX: Math.max(...pts.map((p) => p[0])),
    maxY: Math.max(...pts.map((p) => p[1])),
  };
}

function boxGap(a: ReturnType<typeof box>, b: ReturnType<typeof box>): number {
  const dx = Math.max(0, Math.max(a.minX, b.minX) - Math.min(a.maxX, b.maxX));
  const dy = Math.max(0, Math.max(a.minY, b.minY) - Math.min(a.maxY, b.maxY));
  return Math.hypot(dx, dy);
}

/** Polygon area helper re-exported for site building. */
export { polygonArea };
