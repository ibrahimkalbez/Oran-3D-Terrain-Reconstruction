// Unit tests of the computational modules of the Fusion connector (no Rhino needed).
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, test } from "node:test";

const lib = "../dist/fusion-rga-mcp/src";
const { evaluate } = await import(`${lib}/rules/expression.js`);
const poly = await import(`${lib}/geometry/polygon.js`);
const sampling = await import(`${lib}/geometry/sampling.js`);
const { evaluateRules, siteVariables } = await import(`${lib}/rules/engine.js`);
const { PRESETS } = await import(`${lib}/rules/presets.js`);
const { treeMesh } = await import(`${lib}/trees/mesh.js`);
const { candidates, filterObstacles, assignSpecies } = await import(`${lib}/trees/placement.js`);
const { findSpecies } = await import(`${lib}/trees/catalog.js`);
const doe = await import(`${lib}/explore/doe.js`);
const { optimize, scoreAll } = await import(`${lib}/explore/optimize.js`);
const { solarPosition, sunPath, sunVector } = await import(`${lib}/sim/sun.js`);
const { lawsonClass, comfortStats, parseVelocityCsv, logLawSpeed } = await import(`${lib}/sim/comfort.js`);
const { windDomain } = await import(`${lib}/sim/wind.js`);
const { workbenchJournal, parseWorkbenchResults, pyLiteral } = await import(`${lib}/sim/adapters/workbench.js`);
const { commandAdapter, expandArgument } = await import(`${lib}/sim/adapters/command.js`);
const { SimulationStore } = await import(`${lib}/sim/store.js`);

const rect = (x0, y0, x1, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
const building = (id, ring, height, floors) => ({
  id, name: id, layer: "Bati", parts: [{ outer: ring }], area: Math.abs(poly.signedArea(ring)), base_z: 0, top_z: height, height, floors, centroid: poly.centroid(ring),
});

describe("expression language", () => {
  test("arithmetic, comparisons, logic and functions", () => {
    const v = { gfa: 12000, site_area: 4000, "Buildings.volume": 900, height_max: 24 };
    assert.equal(evaluate("gfa / site_area", v), 3);
    assert.equal(evaluate("gfa / site_area <= 2.5", v), false);
    assert.equal(evaluate("far <= 3 and coverage <= 0.6", { far: 2.5, coverage: 0.4 }), true);
    assert.equal(evaluate("max(height_max, 30) - 2 ^ 2 * 1.5", v), 24);
    assert.equal(evaluate("`Buildings.volume` / 100 + Buildings.volume / 100", v), 18);
    assert.equal(evaluate("not (height_max > 30) et gfa > 0", v), true);
    assert.equal(evaluate(".5 + 2.25", {}), 2.75);
  });
  test("refuses unknown names and anything that is not arithmetic", () => {
    assert.throws(() => evaluate("unknown + 1", {}), /Unknown value 'unknown'/);
    assert.throws(() => evaluate("process.exit(1)", {}), /Unknown function|Unknown value/);
    assert.throws(() => evaluate("constructor", {}), /Unknown value/);
    assert.throws(() => evaluate("1 / 0", {}), /Division by zero/);
    assert.throws(() => evaluate("a = 1", { a: 1 }), /Unexpected/);
  });
});

describe("2D geometry", () => {
  test("areas, centroids and distances", () => {
    const sq = rect(0, 0, 10, 10);
    assert.equal(poly.signedArea(sq), 100);
    assert.deepEqual(poly.centroid(sq), [5, 5]);
    assert.equal(poly.polygonArea({ outer: sq, holes: [rect(2, 2, 4, 4)] }), 96);
    assert.equal(poly.polygonDistance({ outer: sq }, { outer: rect(13, 0, 20, 10) }), 3);
    assert.equal(poly.polygonDistance({ outer: sq }, { outer: rect(5, 5, 20, 20) }), 0);
    assert.equal(poly.insetDistance({ outer: rect(2, 3, 8, 8) }, { outer: sq }), 2);
    assert.equal(poly.insetDistance({ outer: rect(5, 5, 12, 8) }, { outer: sq }), -2);
    assert.equal(poly.distanceToPolyline({ outer: sq }, [[-5, -4], [20, -4]]), 4);
  });
  test("sampling", () => {
    const area = { outer: rect(0, 0, 100, 50) };
    const hex = sampling.hexGrid(area, 10);
    assert.ok(hex.length > 50 && hex.every((p) => poly.pointInPolygon(p, area)));
    const pd = sampling.poissonDisk(area, 8, sampling.rng(3));
    for (let i = 0; i < pd.length; i++) for (let j = i + 1; j < pd.length; j++) assert.ok(Math.hypot(pd[i][0] - pd[j][0], pd[i][1] - pd[j][1]) >= 8 - 1e-9);
    assert.ok(pd.length > 30);
    const along = sampling.alongPolyline([[0, 0], [100, 0]], 10, 2, "both", 5);
    assert.equal(along.length, 20);
    assert.deepEqual(along.slice(0, 2), [[5, 2], [5, -2]]);
  });
});

describe("urban rules engine", () => {
  // Plot 50 × 40; A: 20 × 20, 5 m from the limits, 15 m; B: 10 × 10, 2 m from the right limit, 24 m.
  const site = {
    plots: [{ id: "P1", name: "Parcelle 1", polygon: { outer: rect(0, 0, 50, 40) }, area: 2000 }],
    buildings: [building("A", rect(5, 5, 25, 25), 15, 5), building("B", rect(38, 5, 48, 15), 24)],
    roads: [{ id: "R1", name: "Rue", points: [[-10, -6], [60, -6]], closed: false }],
    green: [{ id: "G1", name: "Jardin", polygon: { outer: rect(5, 30, 25, 38) }, area: 160 }],
  };

  test("site variables", () => {
    const v = siteVariables(site, 3);
    assert.equal(v.footprint_area, 500);
    assert.equal(v.gfa, 400 * 5 + 100 * 8);
    assert.equal(v.coverage, 0.25);
    assert.equal(v.far, 1.4);
    assert.equal(v.height_max, 24);
  });

  test("every rule type with pass/fail and violating objects", () => {
    const report = evaluateRules(site, { OUT_GFA: 2800 }, {
      name: "test",
      floor_height: 3,
      rules: [
        { id: "H", type: "max_height", value: 20 },
        { id: "N", type: "max_floors", value: 8 },
        { id: "CES", type: "max_coverage", value: 0.2 },
        { id: "COS", type: "max_far", value: 3 },
        { id: "EV", type: "min_green_ratio", value: 0.1, severity: "warning" },
        { id: "LIM", type: "min_boundary_setback", ratio: 0.5, min: 4 },
        { id: "RUE", type: "min_street_setback", value: 5 },
        { id: "PROSPECT", type: "min_building_spacing", ratio: 1, min: 6 },
        { id: "GFA", type: "metric_min", metric: "OUT_GFA", value: 3000 },
        { id: "EXPR", type: "expression", expression: "far <= 3 and height_max <= 30" },
        { id: "NA", type: "metric_max", metric: "missing", value: 1 },
      ],
    });
    const r = Object.fromEntries(report.results.map((x) => [x.id, x]));
    assert.equal(r.H.status, "fail");
    assert.deepEqual(r.H.violations.map((v) => v.object_id), ["B"]);
    assert.equal(r.N.status, "pass");
    assert.equal(r.CES.status, "fail");
    assert.equal(r.CES.measured, 0.25);
    assert.equal(r.COS.status, "pass");
    assert.equal(r.EV.status, "fail");
    assert.equal(r.EV.severity, "warning");
    assert.equal(r.LIM.status, "fail");
    assert.deepEqual(r.LIM.violations.map((v) => [v.object_id, v.measured, v.limit]), [["A", 5, 7.5], ["B", 2, 12]]);
    assert.equal(r.RUE.status, "pass");
    assert.equal(r.PROSPECT.status, "fail");
    assert.equal(r.PROSPECT.violations[0].measured, 13);
    assert.equal(r.GFA.status, "fail");
    assert.equal(r.EXPR.status, "pass");
    assert.equal(r.NA.status, "not_applicable");
    assert.equal(report.compliant, false);
    assert.deepEqual(report.violating_ids.sort(), ["A", "B"]);
  });

  test("contiguous buildings are accepted when allowed", () => {
    // A on the left plot limit, C on the bottom limit, A and C sharing a party wall.
    const twin = { ...site, buildings: [building("A", rect(0, 5, 20, 25), 15), building("C", rect(20, 0, 30, 20), 15)] };
    const rs = (allow) => evaluateRules(twin, {}, { name: "t", rules: [{ id: "S", type: "min_building_spacing", ratio: 1, min: 6, allow_contiguous: allow }, { id: "L", type: "min_boundary_setback", ratio: 0.5, min: 4, allow_contiguous: allow }] });
    assert.equal(rs(true).results[0].status, "pass");
    assert.equal(rs(true).results[1].status, "pass");
    assert.equal(rs(false).results[0].status, "fail");
  });

  test("presets are valid", () => {
    for (const p of PRESETS) {
      const report = evaluateRules(site, { gfa: 2800 }, p);
      assert.ok(report.results.every((r) => r.status !== "error"), `${p.name}: ${JSON.stringify(report.results.filter((r) => r.status === "error"))}`);
    }
  });
});

describe("tree generator", () => {
  // Closed, consistently oriented, outward meshes: every edge used twice in opposite directions and positive volume.
  function checkClosed(m) {
    const edges = new Map();
    for (const f of m.faces) for (let i = 0; i < f.length; i++) {
      const a = f[i], b = f[(i + 1) % f.length];
      const k = `${a}>${b}`;
      edges.set(k, (edges.get(k) ?? 0) + 1);
    }
    for (const [k, n] of edges) {
      const [a, b] = k.split(">");
      assert.equal(n, 1, `edge ${k} used ${n} times`);
      assert.ok(edges.has(`${b}>${a}`), `edge ${k} has no twin`);
    }
    let vol = 0;
    for (const f of m.faces) for (let i = 1; i < f.length - 1; i++) {
      const [p, q, r] = [m.vertices[f[0]], m.vertices[f[i]], m.vertices[f[i + 1]]];
      vol += (p[0] * (q[1] * r[2] - q[2] * r[1]) - p[1] * (q[0] * r[2] - q[2] * r[0]) + p[2] * (q[0] * r[1] - q[1] * r[0])) / 6;
    }
    return vol;
  }
  for (const shape of ["round", "oval", "umbrella", "cone", "columnar", "palm"]) {
    test(`${shape} tree mesh is closed and outward`, () => {
      const m = treeMesh(10, 20, 5, 12, 8, 3, shape, "low");
      assert.ok(checkClosed(m) > 0, "positive volume");
      const zs = m.vertices.map((v) => v[2]);
      assert.ok(Math.min(...zs) >= 5 - 1e-9 && Math.max(...zs) <= 5 + 12 + 1e-9);
    });
  }
  test("placement along a street avoids a building and keeps spacing", () => {
    const spec = { mode: "along", lines: [[[0, 0], [100, 0]]], spacing: 10, offset: 3, sides: "left" };
    const pts = candidates(spec);
    assert.equal(pts.length, 10);
    const { kept, rejected } = filterObstacles(pts, { areas: [{ outer: rect(30, 2, 60, 20) }], areaClearance: 2 }, 5);
    assert.equal(rejected.filter((r) => r.reason === "building").length, 3);
    assert.equal(kept.length, 7);
    const species = assignSpecies(100, [{ species: "ficus", weight: 3 }, { species: "olivier", weight: 1 }], 2);
    const ficus = species.filter((s) => s === "ficus").length;
    assert.ok(ficus > 60 && ficus < 90);
    assert.equal(findSpecies("Olea europaea").id, "olivier");
  });
});

describe("design exploration", () => {
  const inputs = [
    { name: "Building_Height", kind: "slider", min: 3, max: 30, decimals: 1, step_type: "float" },
    { name: "Floors", kind: "slider", min: 1, max: 10, decimals: 0, step_type: "integer" },
    { name: "Roof", kind: "value_list", items: [{ name: "flat" }, { name: "pitched" }] },
  ];
  test("dimensions come from the slider ranges", () => {
    const dims = doe.resolveDimensions({ "building height": { steps: 4 }, Floors: {}, Roof: {} }, inputs);
    assert.deepEqual(dims.map((d) => [d.name, d.kind, d.min, d.max]), [["Building_Height", "continuous", 3, 30], ["Floors", "integer", 1, 10], ["Roof", "discrete", 0, 1]]);
    assert.equal(doe.gridDesign(dims).length, 4 * 3 * 2);
  });
  test("latin hypercube covers every stratum once", () => {
    const dims = doe.resolveDimensions({ Building_Height: { min: 0, max: 10 } }, []);
    const values = doe.lhsDesign(dims, 10, 5).map((d) => d.Building_Height);
    const strata = new Set(values.map((v) => Math.min(9, Math.floor(v))));
    assert.equal(strata.size, 10);
  });
  test("pareto front", () => {
    const rows = [{ a: 1, b: 1 }, { a: 2, b: 2 }, { a: 3, b: 0 }, { a: 1, b: 3 }];
    assert.deepEqual(doe.paretoFront(rows, { a: "max", b: "max" }).sort(), [1, 2, 3]);
    assert.deepEqual(doe.paretoFront(rows, { a: "max", b: "min" }), [2]);
  });
  test("genetic optimisation finds the optimum and honours constraints", async () => {
    const dims = doe.resolveDimensions({ x: { min: 0, max: 10 } }, []);
    const evaluate = async (p) => ({ params: p, metrics: { f: -((p.x - 7) ** 2) }, feasible: p.x <= 8, violations: p.x <= 8 ? 0 : 1 });
    const res = await optimize(dims, evaluate, { objectives: { f: "max" }, population: 10, generations: 8, seed: 4 });
    assert.ok(res.best.feasible);
    assert.ok(Math.abs(res.best.params.x - 7) < 0.8, `best x = ${res.best.params.x}`);
    assert.equal(res.history.length, 8);
    assert.deepEqual(scoreAll([{ metrics: { f: 1 } }, { metrics: { f: 3 } }], { f: "min" }), [1, 0]);
  });
});

describe("environment and simulation", () => {
  test("sun position at Oran", () => {
    // Summer solstice: solar noon ≈ 12:04 UTC (longitude 0.63° W, equation of time ≈ −1.7 min); elevation ≈ 90 − 35.7 + 23.44.
    const summer = solarPosition(new Date(Date.UTC(2026, 5, 21, 12, 4)), 35.6971, -0.6308);
    assert.ok(Math.abs(summer.elevation - 77.7) < 0.5, `summer elevation ${summer.elevation}`);
    assert.ok(Math.abs(summer.azimuth - 180) < 5, `azimuth ${summer.azimuth}`);
    const winter = solarPosition(new Date(Date.UTC(2026, 11, 21, 12, 1)), 35.6971, -0.6308);
    assert.ok(Math.abs(winter.elevation - 30.9) < 0.5, `winter elevation ${winter.elevation}`);
    const dec = sunPath({ dates: ["2026-12-21"], step_minutes: 60 });
    const hours = dec.reduce((s, x) => s + x.weight_hours, 0);
    assert.ok(hours >= 9 && hours <= 10.5, `daylight ${hours} h`);
    const v = sunVector({ azimuth: 180, elevation: 30 });
    assert.ok(Math.abs(v[0]) < 1e-9 && v[1] < 0 && Math.abs(v[2] - 0.5) < 1e-9, "south sun points to -Y");
  });
  test("wind comfort and ABL", () => {
    assert.equal(lawsonClass(3), "sitting");
    assert.equal(lawsonClass(9), "walking");
    assert.equal(lawsonClass(12), "uncomfortable");
    const samples = parseVelocityCsv("x,y,z,speed\n0,0,1.5,2\n1,0,1.5,5\n2,0,1.5,7\n3,0,1.5,11\n");
    const s = comfortStats(samples, 10);
    assert.equal(s.samples, 4);
    assert.equal(s.area_pct.uncomfortable, 25);
    assert.equal(s.mean_speed_ratio, 0.625);
    assert.ok(Math.abs(logLawSpeed(10, 5) - 5) < 1e-9);
  });
  test("wind domain follows the guidelines", () => {
    const d = windDomain([[0, 0], [40, 0], [40, 20], [0, 20]], 20, 0, 0);
    assert.equal(d.upstream, 100);
    assert.equal(d.downstream, 300);
    assert.equal(d.height, 120);
    assert.equal(Math.abs(d.flow_direction[0]), 0);
    assert.equal(d.flow_direction[1], -1);
    assert.equal(d.length, 20 + 100 + 300);
    assert.equal(d.width, 40 + 200);
    assert.equal(d.blockage_ok, true);
  });
  test("Workbench journal and results", () => {
    const j = workbenchJournal({ project: "C:\\proj\\wind.wbpj", geometry: "C:\\cases\\a b\\geometry.step", system: "SYS", component: "Geometry", parameters: { P1: 18, P2: "3 [m s^-1]" }, dir: "C:\\cases\\a b", save_project: false });
    assert.match(j, /Open\(FilePath=case\["project"\]\)/);
    assert.match(j, /u"C:\\\\proj\\\\wind.wbpj"/);
    assert.match(j, /u"P1": 18/);
    assert.match(j, /Parameters\.GetAllParameters\(\)/);
    assert.equal(pyLiteral({ a: [true, null] }), '{u"a": [True, None]}');
    const r = parseWorkbenchResults("P1\tBuilding Height\t18 [m]\nP3\tVitesse max (piéton)\t4.25 [m s^-1]\nP4\tLabel\tn/a\n");
    assert.deepEqual(r.metrics, { P1: 18, Building_Height: 18, P3: 4.25, "Vitesse_max_piéton": 4.25 });
  });
  test("command adapter runs a script and reads results.json", async () => {
    const ws = fs.mkdtempSync(path.join(os.tmpdir(), "fsim-"));
    const script = path.join(ws, "solver.mjs");
    fs.writeFileSync(script, `import fs from "node:fs"; const dir = process.argv[2]; const p = JSON.parse(fs.readFileSync(dir + "/parameters.json","utf8"));
console.log("solving", p.parameters.H); fs.writeFileSync(dir + "/results.json", JSON.stringify({ max_stress: p.parameters.H * 2, ok: true }));
fs.writeFileSync(dir + "/vel.csv", "0,0,1.5,3\\n1,0,1.5,9\\n");`);
    const store = new SimulationStore(ws);
    const c = await store.create("command", "test", { command: [process.execPath, script, "{case_dir}"], velocity_csv: "vel.csv", reference_speed: 6 });
    c.settings._variant_parameters = { H: 21 };
    assert.equal(expandArgument("{H}-{case_dir}", c), `21-${c.dir}`);
    await commandAdapter.prepare({}, c);
    const job = { cancelRequested: false };
    await commandAdapter.run({}, c, { job, log: () => {}, update: () => {}, checkCancelled: () => {} });
    await commandAdapter.collect({}, c);
    assert.equal(c.metrics.max_stress, 42);
    assert.equal(c.metrics.wind_pct_uncomfortable, 0);
    assert.equal(c.metrics.wind_pct_sitting, 50);
    assert.match(fs.readFileSync(path.join(c.dir, "run.log"), "utf8"), /solving 21/);
  });
});
