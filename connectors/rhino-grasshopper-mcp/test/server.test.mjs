// End-to-end tests: a real MCP client talks over stdio to the built server (dist/index.js),
// which discovers and calls the fake bridge exactly as it would call Rhino.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { after, before, describe, test } from "node:test";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { createFakeBridge, tempDir } from "./fake-bridge.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
// RGC_ENTRY lets the same tests run against the bundled server unpacked from the .mcpb.
const entry = process.env.RGC_ENTRY ?? path.join(here, "..", "dist", "index.js");

async function connect(env) {
  const client = new Client({ name: "test", version: "1.0.0" });
  const transport = new StdioClientTransport({ command: process.execPath, args: [entry], env: { ...process.env, ...env }, stderr: "pipe" });
  await client.connect(transport);
  return client;
}

const text = (res) => res.content.filter((c) => c.type === "text").map((c) => c.text).join("\n");
const json = (res) => {
  const t = text(res);
  const start = t.indexOf("{");
  return JSON.parse(t.slice(start));
};

describe("Rhino Grasshopper Connector", () => {
  let bridge;
  let client;
  let workspace;

  before(async () => {
    const bridgeDir = tempDir("rmb-");
    workspace = tempDir("rmw-");
    bridge = createFakeBridge();
    await bridge.start(bridgeDir);
    client = await connect({ RHINO_MCP_BRIDGE_DIR: bridgeDir, RHINO_MCP_WORKSPACE: workspace });
  });

  after(async () => {
    await client?.close();
    await bridge?.stop();
  });

  test("exposes every tool of the specification", async () => {
    const { tools } = await client.listTools();
    const names = tools.map((t) => t.name);
    for (const required of [
      "rhino_get_document", "rhino_get_objects", "rhino_create_geometry", "rhino_transform_objects",
      "rhino_delete_objects", "rhino_update_object", "rhino_create_layer", "rhino_set_object_data",
      "grasshopper_open_definition", "grasshopper_get_definition", "grasshopper_get_parameters",
      "grasshopper_set_parameter", "grasshopper_solve", "grasshopper_get_results", "grasshopper_create_component",
      "grasshopper_connect_components", "grasshopper_export_geometry", "rhino_capture_viewport",
      "variant_create", "variant_generate", "variant_compare", "variant_get", "variant_keep", "variant_list",
    ]) {
      assert.ok(names.includes(required), `missing tool ${required}`);
    }
    for (const t of tools) {
      assert.equal(t.inputSchema.type, "object", `${t.name} schema`);
      assert.ok(t.description && t.description.length > 30, `${t.name} needs a description`);
    }
    const create = tools.find((t) => t.name === "rhino_create_geometry");
    assert.ok(create.inputSchema.properties.geometries, "geometries property in schema");
  });

  test("server instructions explain the workflow", async () => {
    assert.match(client.getInstructions() ?? "", /grasshopper_set_parameter/);
  });

  test("reads the Rhino document through discovery", async () => {
    const res = await client.callTool({ name: "rhino_get_document", arguments: {} });
    assert.ok(!res.isError, text(res));
    const doc = json(res);
    assert.equal(doc.units, "m");
    assert.equal(doc.earth_anchor.latitude, 35.6971);
  });

  test("creates buildings by extrusion and finds them", async () => {
    const res = await client.callTool({
      name: "rhino_create_geometry",
      arguments: {
        defaults: { layer: "Urban::Buildings" },
        geometries: [
          { type: "extrusion", profile: [[0, 0], [20, 0], [20, 30], [0, 30]], height: 15, name: "B1" },
          { type: "extrusion", profile: [[30, 0], [50, 0], [50, 30], [30, 30]], height: 18, name: "B2" },
        ],
      },
    });
    assert.ok(!res.isError, text(res));
    assert.equal(json(res).created_count, 2);
    const found = json(await client.callTool({ name: "rhino_get_objects", arguments: { layer: "Urban" } }));
    assert.equal(found.total, 2);
  });

  test("invalid geometry returns a readable error", async () => {
    const res = await client.callTool({ name: "rhino_create_geometry", arguments: { geometries: [{ type: "extrusion", profile: [[0, 0], [1, 0], [1, 1]] }] } });
    assert.equal(res.isError, true);
    assert.match(text(res), /height/);
  });

  test("schema validation rejects unknown geometry types before reaching Rhino", async () => {
    const before = bridge.state.calls.length;
    const res = await client.callTool({ name: "rhino_create_geometry", arguments: { geometries: [{ type: "hexagon" }] } }).catch((e) => ({ isError: true, content: [{ type: "text", text: e.message }] }));
    assert.equal(res.isError, true);
    assert.equal(bridge.state.calls.length, before);
  });

  test("'increase building height by 10 %' reports old → new and metric changes", async () => {
    const res = await client.callTool({
      name: "grasshopper_set_parameter",
      arguments: { parameter: "building height", value: 10, mode: "percent" },
    });
    assert.ok(!res.isError, text(res));
    assert.match(text(res), /Building_Height: 15 → 16\.5/);
    const data = json(res);
    assert.equal(data.metrics_change.Height.delta_pct, 10);
    assert.equal(data.metrics_change.Volume.before, 9000);
    assert.equal(data.metrics_change.Volume.after, 9900);
    assert.equal(data.metrics_change.GFA, undefined, "unchanged metrics are omitted");
  });

  test("unknown parameter lists the available names", async () => {
    const res = await client.callTool({ name: "grasshopper_set_parameter", arguments: { parameter: "Hauteur", value: 3 } });
    assert.equal(res.isError, true);
    assert.match(text(res), /Building_Height/);
    assert.match(text(res), /Hint/);
  });

  test("three variants 12 / 15 / 18 m are created, saved, compared and the original restored", async () => {
    const res = await client.callTool({
      name: "variant_generate",
      arguments: { sweep: { Building_Height: [12, 15, 18] }, objectives: { GFA: "max", Shadow_Length: "min" } },
    });
    assert.ok(!res.isError, text(res));
    const images = res.content.filter((c) => c.type === "image");
    assert.equal(images.length, 3);
    const t = text(res);
    assert.match(t, /\| Variant \| Building_Height \|/);
    const data = JSON.parse(t.slice(t.indexOf("{\n")));
    assert.equal(data.created.length, 3);
    assert.deepEqual(data.created.map((c) => c.id), ["V01", "V02", "V03"]);
    assert.equal(data.comparison.best.id, "V01", "lowest shadow wins when GFA is equal");
    for (const c of data.created) {
      assert.ok(fs.existsSync(path.join(c.folder, "preview.png")));
      assert.ok(fs.existsSync(path.join(c.folder, "geometry.3dm")));
      assert.ok(fs.existsSync(path.join(c.folder, "variant.json")));
    }
    const height = bridge.state.definition.inputs.find((i) => i.name === "Building_Height").value;
    assert.equal(height, 16.5, "original value restored");
  });

  test("variant_get shows 'variante 03' with its image", async () => {
    const res = await client.callTool({ name: "variant_get", arguments: { variant: "03" } });
    assert.ok(!res.isError, text(res));
    assert.equal(res.content[0].type, "image");
    assert.match(text(res), /V03/);
    assert.equal(json(res).parameters.Building_Height, 18);
  });

  test("compare, keep and clean up variants", async () => {
    const cmp = await client.callTool({ name: "variant_compare", arguments: { definition: "urban_block", metrics: ["Volume", "Height"], baseline: "V02" } });
    assert.ok(!cmp.isError, text(cmp));
    assert.match(text(cmp), /V02 .*\(ref\)/);
    assert.match(text(cmp), /\(-20%\)/);

    const keep = await client.callTool({ name: "variant_keep", arguments: { variant: "V02", note: "best compromise" } });
    assert.ok(!keep.isError, text(keep));
    const del = await client.callTool({ name: "variant_delete", arguments: { definition: "urban_block", unkept_only: true } });
    assert.match(text(del), /2 variant\(s\) deleted/);
    const list = json(await client.callTool({ name: "variant_list", arguments: {} }));
    assert.deepEqual(list.variants.map((v) => v.id), ["V02"]);
    assert.equal(list.variants[0].kept, true);
  });

  test("variant_create with explicit changes and bake", async () => {
    const res = await client.callTool({ name: "variant_create", arguments: { name: "tall", parameters: { Building_Height: 24, Floors: 8 }, bake: true, save_definition: true } });
    assert.ok(!res.isError, text(res));
    const rec = json(res);
    assert.equal(rec.id, "V03", "numbering continues after the highest existing number");
    assert.equal(rec.metrics.GFA, 600 * 8);
    assert.equal(rec.baked.count, 4);
    assert.ok(fs.existsSync(path.join(rec.dir, "definition.gh")));
  });

  test("long series run as a job that can be followed", async () => {
    const res = await client.callTool({ name: "variant_generate", arguments: { sweep: { Floors: { min: 2, max: 4, steps: 3 } }, wait_seconds: 0, capture: false } });
    assert.ok(!res.isError, text(res));
    const jobId = /Job (job-[0-9a-f]+)/.exec(text(res))?.[1];
    assert.ok(jobId, text(res));
    const status = await client.callTool({ name: "job_status", arguments: { job_id: jobId, wait_seconds: 30 } });
    assert.ok(!status.isError, text(status));
    assert.match(text(status), /"status": "done"/);
  });

  test("bridge status hides the token", async () => {
    const res = await client.callTool({ name: "rhino_bridge_status", arguments: {} });
    const t = text(res);
    assert.match(t, /"connected": true/);
    assert.ok(!t.includes(bridge.token), "token must not be shown");
  });
});

describe("without Rhino", () => {
  test("tools explain how to start the bridge", async () => {
    const client = await connect({ RHINO_MCP_BRIDGE_DIR: tempDir("rmb-empty-"), RHINO_MCP_WORKSPACE: tempDir("rmw-") });
    try {
      const res = await client.callTool({ name: "rhino_get_document", arguments: {} });
      assert.equal(res.isError, true);
      assert.match(text(res), /Rhino is not reachable/);
      assert.match(text(res), /McpBridgeStatus/);
    } finally {
      await client.close();
    }
  });
});
