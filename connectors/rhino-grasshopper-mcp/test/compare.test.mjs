import assert from "node:assert/strict";
import { test } from "node:test";
import { compareVariants, diffMetrics, range, sweepCombinations } from "../dist/variants/compare.js";

test("diffMetrics reports deltas and percentages", () => {
  const d = diffMetrics({ a: 100, b: 5, same: 1 }, { a: 110, b: 4, same: 1, added: 3 });
  assert.deepEqual(d.a, { before: 100, after: 110, delta: 10, delta_pct: 10 });
  assert.equal(d.b.delta_pct, -20);
  assert.equal(d.same, undefined);
  assert.deepEqual(d.added, { before: null, after: 3 });
});

test("sweep makes every combination", () => {
  const c = sweepCombinations({ H: [12, 15], W: [8, 10, 12] });
  assert.equal(c.length, 6);
  assert.deepEqual(c[0], { H: 12, W: 8 });
});

test("range from steps or step", () => {
  assert.deepEqual(range({ min: 12, max: 18, steps: 3 }), [12, 15, 18]);
  assert.deepEqual(range({ start: 0, stop: 1, step: 0.25 }), [0, 0.25, 0.5, 0.75, 1]);
});

test("ranking normalises objectives", () => {
  const v = (id, area, shadow) => ({ id, name: id, parameters: { H: area / 10 }, metrics: { area, shadow } });
  const cmp = compareVariants([v("A", 100, 10), v("B", 200, 30), v("C", 150, 15)], { objectives: { area: "max", shadow: "min" } });
  assert.deepEqual(cmp.parameters, ["H"]);
  assert.equal(cmp.best.id, "C");
  assert.equal(cmp.rows.find((r) => r.id === "B").delta_pct.area, 100);
  assert.match(cmp.table, /\| A \(ref\) \|/);
});
