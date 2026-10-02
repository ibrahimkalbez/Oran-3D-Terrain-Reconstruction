// Fake Rhino bridge for tests: same HTTP/JSON-RPC protocol and response shapes as the C# plug-in
// (connectors/rhino-bridge), with a tiny in-memory model of a Rhino document and a parametric
// Grasshopper definition (a block of buildings: footprint × height).
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";

// 1×1 PNG
const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

export function createFakeBridge({ token = "fake-token" } = {}) {
  const state = {
    objects: new Map(),
    layers: new Map([["Default", { color: "#000000" }]]),
    nextId: 1,
    calls: [],
    definition: {
      id: "11111111-2222-3333-4444-555555555555",
      name: "urban_block.gh",
      path: "C:/projects/urban_block.gh",
      inputs: [
        { id: "aaaaaaaa-0000-0000-0000-000000000001", name: "Building_Height", kind: "slider", value: 15, min: 3, max: 30, decimals: 1, step_type: "float" },
        { id: "aaaaaaaa-0000-0000-0000-000000000002", name: "Floors", kind: "slider", value: 5, min: 1, max: 10, decimals: 0, step_type: "integer" },
        { id: "aaaaaaaa-0000-0000-0000-000000000003", name: "Green_Roof", kind: "toggle", value: false },
      ],
    },
  };

  const footprint = 20 * 30; // m²
  const input = (n) => state.definition.inputs.find((i) => i.name === n);
  const results = () => {
    const h = input("Building_Height").value;
    const floors = input("Floors").value;
    return {
      GFA: footprint * floors,
      Volume: footprint * h,
      Height: h,
      Shadow_Length: Math.round(h * 1.73 * 1000) / 1000,
    };
  };

  const err = (code, message, data) => {
    const e = new Error(message);
    e.rpc = { code, message, ...(data ? { data } : {}) };
    return e;
  };
  const findInput = (key) => {
    const norm = (s) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
    const hit = state.definition.inputs.filter((i) => i.id === key || i.name.toLowerCase() === key.toLowerCase() || norm(i.name) === norm(key));
    if (hit.length === 0) throw err(-32003, `No input parameter named '${key}'.`, { available: state.definition.inputs.map((i) => i.name) });
    return hit[0];
  };
  const docInfo = () => ({ id: state.definition.id, name: state.definition.name, path: state.definition.path, object_count: 12, enabled: true, active: true, modified: false });
  const solve = () => ({ solved: true, duration_ms: 12, state: "completed", errors: [], warnings: [] });
  const outputs = () =>
    Object.entries(results()).map(([name, v]) => ({ name: `OUT_${name}`, id: `out-${name}`, kind: "parameter", type: "Number", count: 1, preview: [v] }));

  const methods = {
    "bridge.info": () => ({ bridge_version: "1.0.0", rhino_version: "8.0.fake", pid: process.pid, port: server.address().port, grasshopper_loaded: true, methods: Object.keys(methods) }),
    "rhino.get_document": () => ({
      file: { name: "Oran.3dm", path: "C:/projects/Oran.3dm", modified: false },
      units: "m",
      tolerance: { absolute: 0.001, angle_degrees: 1, relative: 0 },
      object_count: state.objects.size,
      objects_by_type: {},
      layers: [...state.layers.entries()].map(([p, l]) => ({ path: p, color: l.color, visible: true, locked: false, current: p === "Default", object_count: [...state.objects.values()].filter((o) => o.layer === p).length })),
      earth_anchor: { latitude: 35.6971, longitude: -0.6308, elevation: 0 },
      grasshopper: { loaded: true, definitions: [docInfo()] },
    }),
    "rhino.get_objects": (p) => {
      let list = [...state.objects.values()];
      if (p.layer) list = list.filter((o) => o.layer === p.layer || o.layer.startsWith(p.layer + "::"));
      if (p.ids) list = list.filter((o) => p.ids.includes(o.id));
      if (p.types) list = list.filter((o) => p.types.includes(o.type));
      return { total: list.length, offset: 0, returned: list.length, truncated: false, objects: list };
    },
    "rhino.create_geometry": (p) => {
      const created = [];
      for (const [i, g] of (p.geometries ?? []).entries()) {
        if (!["point", "line", "box", "extrusion", "polyline", "circle", "rectangle"].includes(g.type)) throw err(-32602, `geometries[${i}] (${g.type}): Unknown geometry type`);
        if (g.type === "extrusion" && !(g.height > 0)) throw err(-32602, `geometries[${i}] (extrusion): 'height' is required.`);
        const layer = g.layer ?? p.defaults?.layer ?? "Default";
        if (!state.layers.has(layer)) state.layers.set(layer, { color: "#000000" });
        const id = `00000000-0000-0000-0000-${String(state.nextId++).padStart(12, "0")}`;
        const obj = { id, type: g.type === "extrusion" ? "extrusion" : g.type === "box" ? "brep" : "curve", name: g.name ?? "", layer, user_text: g.user_text ?? {} };
        state.objects.set(id, obj);
        created.push({ index: i, id, geometry_type: obj.type, layer });
      }
      return { created_count: created.length, created };
    },
    "rhino.delete_objects": (p) => {
      const ids = p.ids ?? [...state.objects.values()].filter((o) => p.layer && o.layer === p.layer).map((o) => o.id);
      if (p.dry_run) return { dry_run: true, would_delete: ids.length };
      let n = 0;
      for (const id of ids) if (state.objects.delete(id)) n++;
      return { deleted: n, requested: ids.length };
    },
    "rhino.capture_viewport": (p) => {
      if (p.save_path) {
        fs.mkdirSync(path.dirname(p.save_path), { recursive: true });
        fs.writeFileSync(p.save_path, Buffer.from(PNG, "base64"));
      }
      return { label: "viewport", width: p.width ?? 1280, height: p.height ?? 800, mime_type: "image/png", view: "Perspective", display_mode: p.display_mode ?? "Shaded", ...(p.save_path ? { saved_path: p.save_path } : {}), ...(p.return_image === false ? {} : { image_base64: PNG }) };
    },
    "grasshopper.status": () => ({ loaded: true, editor_visible: false, solver_enabled: true, definitions: [docInfo()] }),
    "grasshopper.get_parameters": () => ({ inputs: state.definition.inputs, outputs_mode: "tagged", outputs: outputs(), definition: docInfo() }),
    "grasshopper.set_parameter": (p) => {
      const reqs = p.parameters ?? [p];
      const plan = reqs.map((r) => [findInput(r.parameter ?? r.name ?? r.id), r]);
      const changes = plan.map(([inp, r]) => {
        const old = inp.value;
        const mode = r.mode ?? "set";
        const change = { name: inp.name, id: inp.id, kind: inp.kind, old };
        if (inp.kind === "toggle") inp.value = mode === "toggle" ? !inp.value : Boolean(r.value);
        else {
          let v = Number(r.value);
          if (mode === "add") v = old + v;
          if (mode === "multiply") v = old * v;
          if (mode === "percent") v = old * (1 + v / 100);
          v = inp.step_type === "integer" ? Math.round(v) : Math.round(v * 10) / 10;
          if (v > inp.max) {
            inp.max = v;
            change.range_extended = [inp.min, inp.max];
          }
          inp.value = v;
        }
        change.new = inp.value;
        return change;
      });
      return { definition: state.definition.name, changes, ...(p.solve === false ? {} : { solution: solve(), outputs: outputs() }) };
    },
    "grasshopper.solve": () => ({ ...solve(), definition: state.definition.name, outputs: outputs() }),
    "grasshopper.get_results": () => {
      const r = results();
      return {
        definition: docInfo(),
        outputs_mode: "tagged",
        outputs: [
          ...Object.entries(r).map(([n, v]) => ({ name: n, id: `out-${n}`, type: "Number", count: 1, branches: 1, number: v, values: [v], stats: { count: 1, min: v, max: v, sum: v, mean: v } })),
          { name: "Buildings", id: "out-geo", type: "Brep", count: 4, branches: 1, geometry: { count: 4, total_volume: r.Volume, total_area: 2400, types: { brep_solid: 4 } } },
        ],
        metrics: { ...r, "Buildings.volume": r.Volume, "Buildings.area": 2400, "Buildings.count": 4 },
        inputs: state.definition.inputs.map((i) => ({ name: i.name, value: i.value })),
      };
    },
    "grasshopper.export_geometry": (p) => {
      if (p.file_path) {
        fs.mkdirSync(path.dirname(p.file_path), { recursive: true });
        fs.writeFileSync(p.file_path, "3dm-placeholder");
        return { mode: "file", path: p.file_path, object_count: 4, outputs: [{ name: "Buildings", count: 4 }] };
      }
      return { mode: "bake", baked_count: 4, replaced: 0, outputs: [{ name: "Buildings", layer: p.layer, count: 4 }], ids: [] };
    },
    "grasshopper.save_definition": (p) => {
      fs.mkdirSync(path.dirname(p.path), { recursive: true });
      fs.writeFileSync(p.path, "gh-placeholder");
      return { path: p.path, copy: true, bytes: 14 };
    },
  };

  const server = http.createServer((req, res) => {
    const send = (status, obj) => {
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(obj));
    };
    if (req.method === "GET" && req.url === "/health") return send(200, { ok: true, service: "rhino-mcp-bridge", version: "1.0.0", auth: "bearer" });
    if (req.url !== "/rpc" || req.method !== "POST") return send(404, { ok: false });
    if (req.headers.authorization !== `Bearer ${token}`) return send(401, { jsonrpc: "2.0", id: null, error: { code: -32010, message: "Missing or invalid bridge token." } });
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      const msg = JSON.parse(body);
      state.calls.push({ method: msg.method, params: msg.params });
      const fn = methods[msg.method];
      if (!fn) return send(200, { jsonrpc: "2.0", id: msg.id, error: { code: -32601, message: `Unknown method '${msg.method}'.` } });
      try {
        send(200, { jsonrpc: "2.0", id: msg.id, result: fn(msg.params ?? {}) });
      } catch (e) {
        send(200, { jsonrpc: "2.0", id: msg.id, error: e.rpc ?? { code: -32603, message: e.message } });
      }
    });
  });

  return {
    state,
    server,
    token,
    async start(dir) {
      await new Promise((r) => server.listen(0, "127.0.0.1", r));
      const instances = path.join(dir, "instances");
      fs.mkdirSync(instances, { recursive: true });
      fs.writeFileSync(
        path.join(instances, `${process.pid}.json`),
        JSON.stringify({ pid: process.pid, port: server.address().port, host: "127.0.0.1", token, rhino_version: "8.0.fake", bridge_version: "1.0.0", started_at: new Date().toISOString(), document: "C:/projects/Oran.3dm" }),
      );
      return server.address().port;
    },
    stop: () => new Promise((r) => server.close(r)),
  };
}

export function tempDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}
