// End-to-end tests of the Fusion Revit Dynamo ANSYS connector: real MCP client ↔ server (stdio) ↔
// fake Revit bridge (same protocol as the add-in, simulated Dynamo massing graph).
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { after, before, describe, test } from "node:test";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { createFakeRevitBridge, IDS, tempDir } from "../../revit-dynamo-mcp/test/fake-revit-bridge.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const entry = process.env.FUSION_REVIT_ENTRY ?? path.join(here, "..", "dist", "fusion-rda-mcp", "src", "index.js");
const graphs = path.join(here, "..", "..", "revit-dynamo-mcp", "test", "fixtures", "graphs");

const text = (res) => res.content.filter((c) => c.type === "text").map((c) => c.text).join("\n");
const json = (res) => {
  const t = text(res);
  return JSON.parse(t.slice(t.indexOf("{")));
};

describe("Fusion Revit Dynamo ANSYS", () => {
  let bridge;
  let client;
  let workspace;

  before(async () => {
    const bridgeDir = tempDir("frb-");
    workspace = tempDir("frw-");
    bridge = createFakeRevitBridge();
    await bridge.start(bridgeDir);
    client = new Client({ name: "test", version: "1.0.0" });
    await client.connect(
      new StdioClientTransport({
        command: process.execPath,
        args: [entry],
        env: { ...process.env, REVIT_MCP_BRIDGE_DIR: bridgeDir, REVIT_MCP_WORKSPACE: workspace, REVIT_MCP_GRAPHS: graphs },
        stderr: "pipe",
      }),
    );
  });

  after(async () => {
    await client?.close();
    await bridge?.stop();
  });

  test("combines the Revit/Dynamo tools with the Fusion modules", async () => {
    const { tools } = await client.listTools();
    const names = tools.map((t) => t.name);
    for (const required of [
      "revit_get_document", "revit_set_global_parameters", "revit_create_elements", "dynamo_run", "dynamo_get_graph",
      "urban_site_detect", "urban_rules_check", "urban_rules_save", "design_explore", "design_optimize",
      "trees_generate", "trees_stats", "trees_remove", "sim_solvers", "sim_create", "sim_run", "sim_wind_domain",
      "variant_generate", "variant_compare", "job_status",
    ]) assert.ok(names.includes(required), `missing tool ${required}`);
    assert.ok(!names.some((n) => n.startsWith("rhino_") || n.startsWith("grasshopper_")));
    assert.match(tools.find((t) => t.name === "design_explore").description, /Revit\/Dynamo parameters/);
    assert.match(tools.find((t) => t.name === "trees_generate").description, /Plant trees in Revit \(Planting DirectShapes\)/);
    assert.match(client.getInstructions() ?? "", /ANSYS/);
  });

  test("detects the Revit site: masses, property lines and road line styles", async () => {
    const res = await client.callTool({ name: "urban_site_detect", arguments: {} });
    assert.ok(!res.isError, text(res));
    const r = json(res);
    assert.equal(r.sources.buildings.layer, "Category::Mass");
    assert.equal(r.sources.plots.layer, "Category::Property Lines");
    assert.equal(r.sources.roads.layer, "LineStyle::Voirie");
  });

  test("checks urban rules on the Revit masses", async () => {
    const res = await client.callTool({ name: "urban_rules_check", arguments: { rules: [{ id: "H", type: "max_height", value: 40 }, { id: "CES", type: "max_coverage", value: 0.5 }] } });
    assert.ok(!res.isError, text(res));
    assert.match(text(res), /conforme/);
    const body = json(res);
    assert.equal(body.site.buildings, 2);
    assert.equal(body.site.plots, 1);
  });

  test("explores a Dynamo input under urban rules and a sun-hours simulation", async () => {
    const res = await client.callTool({
      name: "design_explore",
      arguments: {
        definition: "Massing_Oran",
        space: { Hauteur: { values: [12, 18, 24] } },
        rules: { rules: [{ id: "H", type: "max_height", value: 20 }] },
        simulations: [{ solver: "solar", settings: { spacing: 10, margin: 10, dates: ["2026-12-21"], step_minutes: 60 } }],
        simulation_constraints: [{ metric: "solar.sun_hours_mean", min: 1 }],
        objectives: { "design.volume_m3": "max" },
        save: "best",
        wait_seconds: 120,
      },
    });
    assert.ok(!res.isError, text(res));
    const r = json(res);
    assert.equal(r.evaluated, 3);
    assert.equal(r.simulated, 2, "the 24 m design breaks the height rule and is not simulated");
    assert.equal(r.feasible, 2);
    assert.equal(r.best.Hauteur, 18);
    assert.equal(r.best["design.volume_m3"], 600 * 18);
    assert.ok(r.best["solar.sun_hours_mean"] > 1);
    // The bridge turned the geographic sun vectors to project north.
    const ray = bridge.state.calls.filter((c) => c.method === "analysis.ray_visibility").at(-1);
    assert.equal(ray.params.true_north, true);
    assert.equal(ray.params.obstacles, undefined, "Revit designs are model elements: default obstacles = whole model");
    // The buildings checked by the rules are the elements output by the graph.
    const fp = bridge.state.calls.filter((c) => c.method === "analysis.footprints" && c.params.ids);
    assert.ok(fp.length >= 3);
    assert.deepEqual(fp[0].params.ids, ["1001", "1002"]);
    // The best design is saved as a variant with its image and OBJ geometry, then the graph is restored.
    assert.equal(r.saved_variants.length, 1);
    assert.equal(r.saved_variants[0].files.geometry, "geometry.obj");
    assert.equal(bridge.state.graph[IDS.hauteur], 15, "original value restored");
  });

  test("plants street trees as Planting elements and counts them", async () => {
    const res = await client.callTool({ name: "trees_generate", arguments: { mode: "along", lines: { layer: "LineStyle::Voirie" }, spacing: 10, offset: 4, sides: "left", set_name: "rue" } });
    assert.ok(!res.isError, text(res));
    const created = [...bridge.state.objects.values()].filter((o) => o.user_text["mcp.kind"] === "tree");
    assert.ok(created.length >= 8, `${created.length} trees`);
    assert.ok(created.every((t) => t.spec.type === "mesh" && t.layer.startsWith("Vegetation::Trees::")));
    const create = bridge.state.calls.find((c) => c.method === "revit.create_geometry");
    assert.ok(create, "trees created through revit.create_geometry");
    const stats = json(await client.callTool({ name: "trees_stats", arguments: {} }));
    assert.equal(stats.trees, created.length);
    const removed = json(await client.callTool({ name: "trees_remove", arguments: { set_name: "rue" } }));
    assert.equal(removed.deleted, created.length);
  });

  test("prepares an ANSYS Workbench case with Revit geometry exported as SAT", async () => {
    const project = path.join(workspace, "vent.wbpj");
    fs.writeFileSync(project, "");
    const res = await client.callTool({
      name: "sim_create",
      arguments: { solver: "ansys_workbench", settings: { project, geometry: { filter: { categories: ["Mass"] } }, parameters: { P1: 3.5 }, ansys_root: path.join(workspace, "v242") } },
    });
    assert.ok(!res.isError, text(res));
    const c = json(res);
    assert.ok(fs.existsSync(path.join(c.dir, "geometry.sat")));
    const exp = bridge.state.calls.filter((x) => x.method === "revit.export").at(-1);
    assert.ok(exp.params.path.endsWith("geometry.sat"));
    assert.deepEqual(exp.params.categories, ["Mass"]);
    assert.match(fs.readFileSync(path.join(c.dir, "run.wbjn"), "utf8"), /geometry\.sat/);
  });

  test("sizes and draws a wind domain around the masses", async () => {
    const res = await client.callTool({ name: "sim_wind_domain", arguments: { buildings: { filter: { categories: ["Mass"] } }, direction_deg: 270, draw: true } });
    assert.ok(!res.isError, text(res));
    const d = json(res);
    assert.equal(d.h_max, 15);
    const draw = bridge.state.calls.filter((c) => c.method === "revit.create_geometry").at(-1);
    assert.deepEqual(draw.params.geometries.map((g) => g.type), ["extrusion", "line"]);
  });
});
