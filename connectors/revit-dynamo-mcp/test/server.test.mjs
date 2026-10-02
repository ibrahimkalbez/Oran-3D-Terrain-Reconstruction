// End-to-end: an MCP client talks over stdio to the built server (dist/…/index.js), which discovers
// and calls the fake Revit bridge exactly as it would call the Revit add-in.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { after, before, describe, test } from "node:test";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { createFakeRevitBridge, IDS, tempDir } from "./fake-revit-bridge.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
// RDC_ENTRY lets the same tests run against the bundled server unpacked from the .mcpb.
const entry = process.env.RDC_ENTRY ?? path.join(here, "..", "dist", "revit-dynamo-mcp", "src", "index.js");
const graphs = path.join(here, "fixtures", "graphs");
const graph = path.join(graphs, "Massing_Oran.dyn");

const text = (res) => res.content.filter((c) => c.type === "text").map((c) => c.text).join("\n");
const json = (res) => {
  const t = text(res);
  return JSON.parse(t.slice(t.indexOf("{")));
};

describe("Revit Dynamo Connector", () => {
  let bridge;
  let client;
  let workspace;

  before(async () => {
    const bridgeDir = tempDir("rvb-");
    workspace = tempDir("rvw-");
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

  test("exposes the Revit, Dynamo Player and variant tools", async () => {
    const { tools } = await client.listTools();
    const names = tools.map((t) => t.name);
    for (const required of [
      "revit_get_document", "revit_get_objects", "revit_get_parameters", "revit_set_parameters", "revit_get_global_parameters",
      "revit_set_global_parameters", "revit_get_types", "revit_create_elements", "revit_create_geometry", "revit_transform_objects",
      "revit_delete_objects", "revit_set_object_data", "revit_select_objects", "revit_capture_view", "revit_export", "revit_metrics",
      "revit_save_document", "revit_open_document", "revit_bridge_status",
      "dynamo_list_graphs", "dynamo_get_graph", "dynamo_run", "dynamo_status", "dynamo_save_graph_copy",
      "variant_create", "variant_generate", "variant_compare", "variant_get", "variant_keep", "variant_list", "variant_apply", "job_status",
    ]) assert.ok(names.includes(required), `missing tool ${required}`);
    assert.ok(!names.some((n) => n.startsWith("rhino_") || n.startsWith("grasshopper_")), "no Rhino tools in the Revit connector");
    for (const t of tools) assert.ok(t.description && t.description.length > 30, `${t.name} needs a description`);
    const variant = tools.find((t) => t.name === "variant_create");
    assert.match(variant.description, /Revit\/Dynamo/);
    assert.match(client.getInstructions() ?? "", /Dynamo Player/);
  });

  test("finds Revit through its own discovery folder", async () => {
    const res = await client.callTool({ name: "revit_bridge_status", arguments: {} });
    const s = json(res);
    assert.equal(s.connected, true);
    assert.equal(s.info.host, "revit");
    const doc = json(await client.callTool({ name: "revit_get_document", arguments: {} }));
    assert.equal(doc.earth_anchor.latitude, 35.6971);
  });

  test("lists and reads Dynamo graphs like Dynamo Player", async () => {
    const list = json(await client.callTool({ name: "dynamo_list_graphs", arguments: {} }));
    assert.equal(list.graphs.length, 1);
    assert.deepEqual(list.graphs[0].inputs, ["Hauteur (slider)", "Niveaux (slider)", "Toiture végétale (toggle)", "Nom (text)"]);
    const g = json(await client.callTool({ name: "dynamo_get_graph", arguments: { graph: "Massing_Oran" } }));
    assert.equal(g.inputs[0].max, 45);
  });

  test("runs a graph with relative inputs and reports the outputs", async () => {
    const res = await client.callTool({ name: "dynamo_run", arguments: { graph: "Massing_Oran", inputs: [{ parameter: "Hauteur", value: 20, mode: "percent" }, { parameter: "niveaux", value: 6 }], capture: true } });
    assert.ok(!res.isError, text(res));
    assert.ok(res.content.some((c) => c.type === "image"), "image returned");
    assert.match(text(res), /Graph run/);
    assert.equal(bridge.state.graph[IDS.hauteur], 18);
    assert.equal(bridge.state.graph[IDS.niveaux], 6);
    const call = bridge.state.calls.filter((c) => c.method === "dynamo.run").at(-1);
    assert.equal(call.params.path, graph);
    // A second relative change starts from the applied value, and all tracked values are re-sent.
    await client.callTool({ name: "dynamo_run", arguments: { graph: "Massing_Oran", inputs: { Hauteur: { value: 2, mode: "add" } } } });
    assert.equal(bridge.state.graph[IDS.hauteur], 20);
    const last = bridge.state.calls.filter((c) => c.method === "dynamo.run").at(-1);
    assert.equal(last.params.inputs[IDS.niveaux], 6);
  });

  test("unknown inputs list the available names", async () => {
    const res = await client.callTool({ name: "dynamo_run", arguments: { graph: "Massing_Oran", inputs: { Largeur: 12 } } });
    assert.ok(res.isError);
    assert.match(text(res), /Hauteur/);
    assert.match(text(res), /Recul/); // global parameters are inputs too
  });

  test("generates and compares variants on a Dynamo input and a global parameter", async () => {
    const res = await client.callTool({
      name: "variant_generate",
      arguments: { definition: "Massing_Oran", sweep: { Hauteur: [12, 24], "global:Recul": [3] }, objectives: { "revit.mass_floor_area_m2": "max", "design.volume_m3": "min" }, wait_seconds: 60 },
    });
    assert.ok(!res.isError, text(res));
    const body = text(res);
    assert.match(body, /V01/);
    assert.match(body, /V02/);
    const list = json(await client.callTool({ name: "variant_list", arguments: {} }));
    assert.equal(list.variants.length, 2);
    const v1 = list.variants[0];
    assert.equal(v1.definition, "Massing_Oran");
    assert.equal(v1.metrics.Surface_plancher, 3600);
    assert.equal(v1.metrics["design.volume_m3"], 600 * 12);
    assert.equal(v1.files.geometry, "geometry.obj");
    assert.ok(fs.existsSync(path.join(v1.folder, "preview.png")));
    assert.ok(fs.existsSync(path.join(v1.folder, "geometry.obj")));
    // The original values are restored at the end (Hauteur 20 from the previous test, Recul 5).
    assert.equal(bridge.state.graph[IDS.hauteur], 20);
    assert.equal(bridge.state.globals.find((g) => g.name === "Recul").value, 5);
    const cmp = await client.callTool({ name: "variant_compare", arguments: { definition: "Massing_Oran", objectives: { "design.volume_m3": "min" } } });
    assert.match(text(cmp), /Best: V01/);
  });

  test("restores a variant and saves a graph preset without touching the original", async () => {
    const res = await client.callTool({ name: "variant_apply", arguments: { variant: "V02", definition: "Massing_Oran" } });
    assert.ok(!res.isError, text(res));
    assert.equal(bridge.state.graph[IDS.hauteur], 24);
    const before = fs.readFileSync(graph, "utf8");
    const copy = json(await client.callTool({ name: "dynamo_save_graph_copy", arguments: { graph: "Massing_Oran", values: { Nom: "Ilot B" } } }));
    assert.equal(copy.values.Hauteur, 24);
    assert.equal(copy.values.Nom, "Ilot B");
    assert.ok(fs.readFileSync(copy.path, "utf8").includes("Ilot B"));
    assert.equal(fs.readFileSync(graph, "utf8"), before);
  });

  test("passes global parameter changes to Revit with their mode", async () => {
    const res = await client.callTool({ name: "revit_set_global_parameters", arguments: { changes: [{ name: "Hauteur_Max", value: 10, mode: "percent" }] } });
    assert.ok(!res.isError, text(res));
    assert.equal(bridge.state.globals.find((g) => g.name === "Hauteur_Max").value, 33);
  });
});
