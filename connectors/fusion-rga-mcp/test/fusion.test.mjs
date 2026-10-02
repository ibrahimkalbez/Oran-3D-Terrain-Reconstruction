// End-to-end tests of the Fusion connector: real MCP client ↔ server (stdio) ↔ fake bridge.
// The fake bridge of connector 1 is extended with the analysis.* methods of plug-in 1.1.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { after, before, describe, test } from "node:test";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { createFakeBridge, tempDir } from "../../rhino-grasshopper-mcp/test/fake-bridge.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const entry = process.env.FUSION_ENTRY ?? path.join(here, "..", "dist", "fusion-rga-mcp", "src", "index.js");

const text = (res) => res.content.filter((c) => c.type === "text").map((c) => c.text).join("\n");
const json = (res) => {
  const t = text(res);
  return JSON.parse(t.slice(t.indexOf("{")));
};
const area = (pts) => Math.abs(pts.reduce((s, p, i) => s + p[0] * pts[(i + 1) % pts.length][1] - pts[(i + 1) % pts.length][0] * p[1], 0) / 2);

function extend(bridge) {
  const { state, methods } = bridge;
  const height = () => state.definition.inputs.find((i) => i.name === "Building_Height").value;
  const onLayer = (o, layer) => o.layer === layer || o.layer.startsWith(layer + "::");
  const pick = (p) => [...state.objects.values()].filter((o) => (p.layer ? onLayer(o, p.layer) : true) && (p.ids ? p.ids.includes(o.id) : true));

  methods["analysis.footprints"] = (p) => {
    let items;
    if (p.grasshopper) {
      // Four 20 × 30 m buildings, 10 m apart, whose height is the Building_Height slider.
      items = [0, 30, 60, 90].map((x, i) => ({
        id: `OUT_Buildings#${i}`, name: "OUT_Buildings", layer: null, source: "grasshopper",
        parts: [{ outer: [[x, 0], [x + 20, 0], [x + 20, 30], [x, 30]], holes: [] }],
        area: 600, base_z: 0, top_z: height(), height: height(), volume: 600 * height(), centroid: [x + 10, 15], user_text: {},
      }));
    } else {
      items = pick(p).filter((o) => o.spec.type === "extrusion").map((o) => ({
        id: o.id, name: o.name, layer: o.layer, source: "rhino",
        parts: [{ outer: o.spec.profile.map((q) => [q[0], q[1]]), holes: [] }],
        area: area(o.spec.profile), base_z: 0, top_z: o.spec.height, height: o.spec.height, volume: area(o.spec.profile) * o.spec.height,
        centroid: [o.spec.profile.reduce((s, q) => s + q[0], 0) / o.spec.profile.length, o.spec.profile.reduce((s, q) => s + q[1], 0) / o.spec.profile.length],
        user_text: o.user_text,
      }));
    }
    return { count: items.length, total_area: items.reduce((s, x) => s + x.area, 0), items };
  };
  methods["analysis.curves"] = (p) => {
    const items = pick(p).filter((o) => o.spec.type === "polyline").map((o) => ({
      id: o.id, object_id: o.id, name: o.name, layer: o.layer, closed: Boolean(o.spec.closed),
      points: o.spec.points.map((q) => [q[0], q[1], q[2] ?? 0]), length: 0, area: o.spec.closed ? area(o.spec.points) : 0, user_text: o.user_text,
    }));
    return { count: items.length, items };
  };
  methods["analysis.drape_points"] = (p) => ({ points: p.points.map((q) => [q[0], q[1], 10 + (p.offset ?? 0)]), hits: p.points.length, misses: 0 });
  methods["analysis.ray_visibility"] = (p) => {
    // Points west of x = 50 are in the shade half of the time.
    const total = p.weights.reduce((a, b) => a + b, 0);
    const values = p.points.map((q) => (q[0] < 50 ? total / 2 : total));
    return { values, visible_counts: values.map(() => 0), stats: { points: values.length, directions: p.directions.length } };
  };
  methods["rhino.export"] = (p) => {
    fs.mkdirSync(path.dirname(p.path), { recursive: true });
    fs.writeFileSync(p.path, "geometry");
    return { path: p.path, format: path.extname(p.path).slice(1), object_count: 1, bytes: 8 };
  };
}

describe("Fusion Rhino Grasshopper ANSYS", () => {
  let bridge;
  let client;
  let workspace;

  before(async () => {
    const dir = tempDir("fusion-b-");
    workspace = tempDir("fusion-w-");
    bridge = createFakeBridge();
    extend(bridge);
    await bridge.start(dir);
    client = new Client({ name: "test", version: "1" });
    await client.connect(new StdioClientTransport({ command: process.execPath, args: [entry], env: { ...process.env, RHINO_MCP_BRIDGE_DIR: dir, RHINO_MCP_WORKSPACE: workspace }, stderr: "pipe" }));

    // Site: plot 120 × 60, two buildings (one too tall and too close to the limit), a street, a garden.
    const res = await client.callTool({
      name: "rhino_create_geometry",
      arguments: {
        geometries: [
          { type: "polyline", points: [[-10, -10], [110, -10], [110, 50], [-10, 50]], closed: true, layer: "Urbain::Parcelles", name: "Parcelle A" },
          { type: "extrusion", profile: [[0, 0], [20, 0], [20, 30], [0, 30]], height: 15, layer: "Urbain::Bâti", name: "Immeuble 1" },
          { type: "extrusion", profile: [[85, 0], [108, 0], [108, 30], [85, 30]], height: 36, layer: "Urbain::Bâti", name: "Tour" },
          { type: "polyline", points: [[-20, -20], [130, -20]], layer: "Voirie", name: "Boulevard" },
          { type: "polyline", points: [[30, 35], [70, 35], [70, 48], [30, 48]], closed: true, layer: "Espaces verts", name: "Jardin" },
        ],
      },
    });
    assert.ok(!res.isError, text(res));
  });

  after(async () => {
    await client?.close();
    await bridge?.stop();
  });

  test("exposes the Rhino/Grasshopper tools and the fusion modules", async () => {
    const names = (await client.listTools()).tools.map((t) => t.name);
    for (const t of ["rhino_get_document", "grasshopper_set_parameter", "variant_compare", "urban_rules_check", "urban_site_detect", "trees_generate", "design_explore", "design_optimize", "sim_run", "sim_solvers", "sim_wind_domain"]) {
      assert.ok(names.includes(t), t);
    }
    assert.equal(names.length, 58);
  });

  test("detects the site layers", async () => {
    const res = await client.callTool({ name: "urban_site_detect", arguments: {} });
    assert.ok(!res.isError, text(res));
    const d = json(res);
    assert.equal(d.sources.buildings.layer, "Urbain::Bâti");
    assert.equal(d.sources.plots.layer, "Urbain::Parcelles");
    assert.equal(d.sources.roads.layer, "Voirie");
    assert.equal(d.sources.green.layer, "Espaces verts");
    assert.equal(d.summary.buildings, 2);
    assert.deepEqual(d.summary.height_range, [15, 36]);
  });

  test("checks the example rules and flags the tower", async () => {
    const res = await client.callTool({ name: "urban_rules_check", arguments: { rule_set: "exemple_zone_urbaine", grasshopper_metrics: false } });
    assert.ok(!res.isError, text(res));
    const t = text(res);
    assert.match(t, /non conforme/);
    assert.match(t, /❌ \| Hauteur maximale 30 m/);
    assert.match(t, /✅ \| Coefficient d'occupation du sol/);
    assert.equal(res.content[0].type, "image", "violations are shown in red");
    const report = json(res).report;
    const h = report.results.find((r) => r.id === "H_MAX");
    assert.equal(h.violations[0].object, "Tour");
    const setback = report.results.find((r) => r.id === "RECUL_LIM");
    assert.equal(setback.status, "fail");
  });

  test("saves a rule set from the POS and uses it", async () => {
    const save = await client.callTool({ name: "urban_rules_save", arguments: { name: "POS_test", reference: "POS n°X, zone UA", rules: [{ id: "H", type: "max_height", value: 40 }, { id: "N", type: "max_floors", value: 12 }] } });
    assert.ok(!save.isError, text(save));
    const res = await client.callTool({ name: "urban_rules_check", arguments: { rule_set: "POS_test", grasshopper_metrics: false, highlight: false } });
    assert.match(text(res), /\*\*POS_test\*\* — conforme/);
  });

  test("plants street trees that avoid the buildings, then counts and removes them", async () => {
    const res = await client.callTool({
      name: "trees_generate",
      arguments: { mode: "along", lines: { layer: "Voirie" }, spacing: 10, offset: 4, sides: "left", species: [{ species: "ficus", weight: 2 }, { species: "jacaranda" }], buildings: { layer: "Urbain::Bâti" }, set_name: "boulevard" },
    });
    assert.ok(!res.isError, text(res));
    const r = json(res);
    assert.equal(r.trees, 15);
    assert.equal(r.created, 15);
    assert.ok(r.canopy_cover_m2 > 500);
    const trees = [...bridge.state.objects.values()].filter((o) => o.user_text["mcp.kind"] === "tree");
    assert.equal(trees.length, 15);
    assert.ok(trees.every((t) => t.spec.type === "mesh" && t.layer.startsWith("Vegetation::Trees::")));

    const again = json(await client.callTool({ name: "trees_generate", arguments: { mode: "along", lines: { layer: "Voirie" }, spacing: 20, sides: "left", offset: 4, set_name: "boulevard" } }));
    assert.equal(again.trees, 8, "same set replaced, not duplicated");
    assert.equal([...bridge.state.objects.values()].filter((o) => o.user_text["mcp.kind"] === "tree").length, 8);

    const stats = json(await client.callTool({ name: "trees_stats", arguments: {} }));
    assert.equal(stats.trees, 8);
    const removed = json(await client.callTool({ name: "trees_remove", arguments: { set_name: "boulevard" } }));
    assert.equal(removed.deleted, 8);
  });

  test("explores heights with the rules as constraints and keeps the best design", async () => {
    const res = await client.callTool({
      name: "design_explore",
      arguments: {
        space: { Building_Height: { min: 12, max: 30, steps: 4 } },
        objectives: { Volume: "max" },
        rules: { rules: [{ id: "H", type: "max_height", value: 25 }], building_outputs: ["OUT_Buildings"] },
        save: "best",
      },
    });
    assert.ok(!res.isError, text(res));
    const r = json(res);
    assert.equal(r.evaluated, 4);
    assert.equal(r.feasible, 3, "30 m is above the 25 m limit");
    assert.equal(r.best.Building_Height, 24);
    assert.equal(r.saved_variants.length, 1);
    assert.ok(fs.existsSync(r.file));
    assert.ok(fs.existsSync(r.file.replace(/\.json$/, ".csv")));
    assert.equal(bridge.state.definition.inputs.find((i) => i.name === "Building_Height").value, 15, "original value restored");
  });

  test("optimises with a genetic algorithm", async () => {
    const res = await client.callTool({
      name: "design_optimize",
      arguments: { space: { Building_Height: { min: 10, max: 30 } }, objectives: { Volume: "max" }, rules: { rules: [{ id: "H", type: "max_height", value: 20 }] }, population: 6, generations: 3, seed: 2 },
    });
    assert.ok(!res.isError, text(res));
    const r = json(res);
    assert.ok(r.best.feasible);
    assert.ok(r.best.params.Building_Height <= 20 && r.best.params.Building_Height > 15, `best ${r.best.params.Building_Height}`);
    assert.equal(r.variant.name, "optimum");
  });

  test("solar analysis on a variant feeds the variant metrics", async () => {
    const list = json(await client.callTool({ name: "variant_list", arguments: {} }));
    const variantId = list.variants[0].id;
    const res = await client.callTool({
      name: "sim_run",
      arguments: { solver: "solar", variant: variantId, settings: { obstacles: { layer: "Urbain::Bâti" }, spacing: 10, margin: 10, dates: ["2026-12-21"], step_minutes: 60, terrain: { layer: "Urbain::Parcelles" } } },
    });
    assert.ok(!res.isError, text(res));
    const c = json(res);
    assert.equal(c.status, "done");
    assert.ok(c.metrics.sun_hours_mean > 0);
    assert.ok(c.metrics.area_pct_always_shaded === 0);
    assert.ok(c.metrics.area_pct_above_threshold > 0);
    const record = json(await client.callTool({ name: "variant_get", arguments: { variant: variantId, include_image: false } }));
    assert.equal(record.metrics["solar.sun_hours_mean"], c.metrics.sun_hours_mean);
    const mesh = [...bridge.state.objects.values()].find((o) => o.user_text["mcp.sim"] === c.id);
    assert.ok(mesh && mesh.spec.vertex_colors.length === mesh.spec.vertices.length, "coloured analysis mesh created");
  });

  test("runs an external solver script (PyAnsys-style) through the command adapter", async () => {
    const script = path.join(workspace, "fake_solver.mjs");
    fs.writeFileSync(script, `import fs from "node:fs"; const dir = process.argv[2]; fs.writeFileSync(dir + "/results.json", JSON.stringify({ metrics: { max_velocity: 7.5, drag: 1200 } }));`);
    const create = await client.callTool({ name: "sim_create", arguments: { solver: "command", name: "wind", settings: { command: [process.execPath, script, "{case_dir}"], geometry: { layer: "Urbain::Bâti" }, geometry_format: "stl" } } });
    assert.ok(!create.isError, text(create));
    const created = json(create);
    assert.ok(fs.existsSync(path.join(created.dir, "geometry.stl")));
    const run = await client.callTool({ name: "sim_run", arguments: { case_id: created.id } });
    assert.ok(!run.isError, text(run));
    assert.equal(json(run).metrics.max_velocity, 7.5);
    const solvers = json(await client.callTool({ name: "sim_solvers", arguments: {} }));
    assert.deepEqual(solvers.solvers.map((s) => s.id), ["solar", "ansys_workbench", "command"]);
  });

  test("Workbench case preparation writes the batch journal", async () => {
    const project = path.join(workspace, "wind.wbpj");
    fs.writeFileSync(project, "");
    const res = await client.callTool({
      name: "sim_create",
      arguments: { solver: "ansys_workbench", settings: { project, geometry: { layer: "Urbain::Bâti" }, parameters: { P1: 3.5 }, ansys_root: path.join(workspace, "v242") } },
    });
    assert.ok(!res.isError, text(res));
    const c = json(res);
    const journal = fs.readFileSync(path.join(c.dir, "run.wbjn"), "utf8");
    assert.match(journal, /u"P1": 3\.5/);
    assert.ok(c.command[0].endsWith(path.join("Framework", "bin", "Linux64", "runwb2")) || c.command[0].endsWith("RunWB2.exe"));
    assert.deepEqual(c.command.slice(1, 3), ["-B", "-R"]);
  });

  test("sizes and draws the wind domain", async () => {
    const res = await client.callTool({ name: "sim_wind_domain", arguments: { buildings: { layer: "Urbain::Bâti" }, direction_deg: 270, draw: true } });
    assert.ok(!res.isError, text(res));
    const d = json(res);
    assert.equal(d.h_max, 36);
    assert.equal(d.upstream, 180);
    assert.equal(d.downstream, 540);
    assert.ok([...bridge.state.objects.values()].some((o) => o.user_text["mcp.kind"] === "wind_domain"));
  });
});
