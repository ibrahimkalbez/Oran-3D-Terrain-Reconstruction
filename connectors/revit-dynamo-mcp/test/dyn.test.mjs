// Dynamo graph reading/writing and the value logic of the Revit backend, without Revit.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";
import { findInput, listGraphs, parseDyn, resolveGraph, writeDynInputs } from "../dist/revit-dynamo-mcp/src/dynamo/dyn.js";
import { combine, elementIds, modelMetrics, outputMetrics } from "../dist/revit-dynamo-mcp/src/backend.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const graphs = path.join(here, "fixtures", "graphs");
const file = path.join(graphs, "Massing_Oran.dyn");

describe("Dynamo graphs", () => {
  test("reads the Player inputs with their kinds, values and ranges", () => {
    const g = parseDyn(fs.readFileSync(file, "utf8"), file);
    assert.equal(g.name, "Massing_Oran");
    assert.deepEqual(g.inputs.map((i) => [i.name, i.kind]), [["Hauteur", "slider"], ["Niveaux", "slider"], ["Toiture végétale", "toggle"], ["Nom", "text"]]);
    const h = g.inputs[0];
    assert.equal(h.value, 15);
    assert.equal(h.min, 9);
    assert.equal(h.max, 45);
    assert.equal(h.decimals, 1);
    assert.equal(h.step_type, "float");
    assert.equal(g.inputs[1].step_type, "integer");
    assert.equal(g.inputs[2].value, false);
    assert.deepEqual(g.outputs.map((o) => o.name), ["Surface plancher", "Masses", "Volumes"]);
    assert.deepEqual(g.packages, [{ name: "Clockwork for Dynamo 2.x", version: "2.4.0" }]);
    assert.equal(findInput(g, "niveaux")?.name, "Niveaux"); // case, accents and punctuation are ignored
    assert.equal(findInput(g, "toiture_vegetale")?.name, "Toiture végétale");
    assert.equal(findInput(g, "3F2A9C1E-5B7D-4E8F-A1C2-D3E4F5A6B7C8")?.name, "Hauteur");
  });

  test("writes a copy with new values and extends slider ranges", () => {
    const text = fs.readFileSync(file, "utf8");
    const g = parseDyn(text, file);
    const out = parseDyn(writeDynInputs(text, { [g.inputs[0].id]: 60, [g.inputs[2].id]: true, [g.inputs[3].id]: "Ilot B" }), file);
    assert.equal(out.inputs[0].value, 60);
    assert.equal(out.inputs[0].max, 60);
    assert.equal(out.inputs[2].value, true);
    assert.equal(out.inputs[3].value, "Ilot B");
    assert.equal(out.inputs[1].value, 5); // untouched
  });

  test("rejects Dynamo 1.x XML graphs with a clear message", () => {
    assert.throws(() => parseDyn("<Workspace Version=\"1.3\"></Workspace>", "old.dyn"), /Dynamo 2\.x/);
  });

  test("finds graphs by path or by name in the graph folders", async () => {
    assert.deepEqual(await listGraphs([graphs]), [file]);
    assert.equal(await resolveGraph("massing_oran", [graphs]), file);
    assert.equal(await resolveGraph(file, []), file);
    await assert.rejects(resolveGraph("inconnu", [graphs]), /Available: Massing_Oran\.dyn/);
  });
});

describe("Revit backend values and metrics", () => {
  const slider = { id: "a", name: "Hauteur", kind: "slider", value: 15, min: 9, max: 45, decimals: 1, step_type: "float" };
  const integer = { id: "b", name: "Niveaux", kind: "slider", value: 5, min: 1, max: 15, decimals: 0, step_type: "integer" };

  test("relative changes like Dynamo Player users ask for them", () => {
    assert.equal(combine(slider, { parameter: "Hauteur", value: 10, mode: "percent" }).value, 16.5);
    assert.equal(combine(slider, { parameter: "Hauteur", value: 3, mode: "add" }).value, 18);
    assert.equal(combine(integer, { parameter: "Niveaux", value: 1.5, mode: "multiply" }).value, 8);
    assert.equal(combine({ id: "c", name: "T", kind: "toggle", value: false }, { parameter: "T", mode: "toggle" }).value, true);
    assert.equal(combine({ id: "c", name: "T", kind: "toggle", value: false }, { parameter: "T", value: "oui" }).value, true);
  });

  test("out-of-range values extend, clamp or fail", () => {
    const ext = combine(slider, { parameter: "Hauteur", value: 60 });
    assert.equal(ext.value, 60);
    assert.match(ext.note, /extends/);
    assert.equal(combine(slider, { parameter: "Hauteur", value: 60, on_out_of_range: "clamp" }).value, 45);
    assert.throws(() => combine(slider, { parameter: "Hauteur", value: 60, on_out_of_range: "error" }), /outside/);
    assert.throws(() => combine({ id: "d", name: "Nom", kind: "text", value: "x" }, { parameter: "Nom", value: 1, mode: "add" }), /only accepts mode 'set'/);
  });

  test("measures Dynamo outputs and the model", () => {
    const outputs = [
      { name: "Surface plancher", value: 3000 },
      { name: "Volumes", value: [4500, 4500] },
      { name: "Masses", value: [{ element_id: "1001" }, { element_id: "1002" }] },
      { name: "OK", value: true },
    ];
    assert.deepEqual(outputMetrics(outputs), {
      Surface_plancher: 3000,
      "Volumes.sum": 9000,
      "Volumes.count": 2,
      "Volumes.max": 4500,
      "Masses.count": 2,
      OK: 1,
    });
    assert.deepEqual([...elementIds(outputs)], ["1001", "1002"]);
    const m = modelMetrics({ totals: { count: 2, volume_m3: 9000, height_m: 15 }, floor_area: { total_m2: 0 }, mass_floor_area_m2: 3000, rooms: { area_m2: 0 }, groups: [{ key: "Generic Models", count: 3, volume_m3: 12 }] });
    assert.equal(m["revit.mass_floor_area_m2"], 3000);
    assert.equal(m["revit.Generic_Models.count"], 3);
    assert.equal(m["revit.Generic_Models.volume_m3"], 12);
  });
});
