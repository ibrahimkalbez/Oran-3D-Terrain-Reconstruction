// Fake Revit bridge for tests: same HTTP/JSON-RPC protocol, discovery file and response shapes as
// the C# add-in (connectors/revit-bridge), with a tiny Revit project (two masses on a plot) and a
// simulated Dynamo massing graph (fixtures/graphs/Massing_Oran.dyn): GFA = 600 m² × floors,
// volume = 600 m² × height.
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";

const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

export const IDS = {
  hauteur: "3f2a9c1e5b7d4e8fa1c2d3e4f5a6b7c8",
  niveaux: "4b9b4b6b2f0f4f6d9f1a3c2b1a0e9d8c",
  toiture: "5c1d2e3f4a5b4c6d8e9f0a1b2c3d4e5f",
  nom: "6d7e8f9a0b1c4d2e9f3a4b5c6d7e8f90",
};

const norm = (id) => String(id).replace(/[{}-]/g, "").toLowerCase();

export function createFakeRevitBridge({ token = "fake-revit-token" } = {}) {
  const state = {
    calls: [],
    graph: { [IDS.hauteur]: 15, [IDS.niveaux]: 5, [IDS.toiture]: false, [IDS.nom]: "Ilot A" },
    runs: 0,
    openGraph: null,
    globals: [
      { name: "Recul", value: 5, unit: "m" },
      { name: "Hauteur_Max", value: 30, unit: "m" },
    ],
    objects: new Map(),
    nextId: 5000,
  };
  const footprint = 600;
  const masses = () => [
    { id: "1001", x: 0, y: 0 },
    { id: "1002", x: 40, y: 0 },
  ];

  const err = (code, message, data) => {
    const e = new Error(message);
    e.rpc = { code, message, ...(data ? { data } : {}) };
    return e;
  };

  const combine = (current, value, mode = "set") => {
    switch (mode) {
      case "add": return current + value;
      case "multiply": return current * value;
      case "percent": return current * (1 + value / 100);
      default: return value;
    }
  };

  const height = () => Number(state.graph[IDS.hauteur]);
  const floors = () => Number(state.graph[IDS.niveaux]);

  const matches = (o, p) => {
    const f = p.filter ?? p;
    if (f.ids && !f.ids.includes(o.id)) return false;
    if (f.user_text && !Object.entries(f.user_text).every(([k, v]) => k in o.user_text && (v === "*" || String(o.user_text[k]) === String(v)))) return false;
    if (f.categories && !f.categories.some((c) => c.toLowerCase() === o.category.toLowerCase())) return false;
    return true;
  };

  const methods = {
    "bridge.info": () => ({ bridge_version: "1.0.0", host: "revit", revit_version: "2025 (fake)", pid: process.pid, dynamo: { available: true, state: "StartedUIless" }, methods: Object.keys(methods) }),
    "bridge.ping": () => ({ pong: true }),

    "revit.get_document": () => ({
      host: "revit",
      file: { name: "Oran_Ilot.rvt", path: "C:/projects/Oran_Ilot.rvt", modified: false },
      units: "meters",
      object_count: 3 + state.objects.size,
      objects_by_type: { Mass: 2, "Property Lines": 1 },
      layers: [
        { path: "Category::Mass", kind: "category", object_count: 2 },
        { path: "Category::Property Lines", kind: "category", object_count: 1 },
        { path: "LineStyle::Voirie", kind: "line_style", object_count: 1 },
      ],
      levels: [{ id: "311", name: "Niveau 0", elevation: 0 }],
      global_parameters: state.globals.map((g) => g.name),
      earth_anchor: { latitude: 35.6971, longitude: -0.6308, true_north_deg: 0 },
      dynamo: { available: true },
    }),

    "revit.get_global_parameters": () => ({ count: state.globals.length, parameters: state.globals.map((g, i) => ({ id: String(900 + i), ...g })) }),
    "revit.set_global_parameters": (p) => {
      const changes = p.changes ?? Object.entries(p.values ?? {}).map(([name, value]) => ({ name, value }));
      const out = [];
      for (const c of changes) {
        const g = state.globals.find((x) => x.name.toLowerCase() === String(c.name).toLowerCase());
        if (!g) throw err(-32003, `No global parameter '${c.name}'. Set create=true to create it.`);
        const before = g.value;
        g.value = typeof g.value === "number" ? combine(g.value, Number(c.value), c.mode) : c.value;
        out.push({ name: g.name, value: g.value, before, unit: g.unit });
      }
      return { changed: out.length, parameters: out };
    },

    "revit.metrics": (p) => {
      if (p.ids) {
        const n = p.ids.length;
        return { group_by: "none", groups: [{ key: "all", count: n, area_m2: footprint * n, volume_m3: footprint * height() * n / 2 }], totals: { count: n, area_m2: footprint * n, volume_m3: footprint * height() * n / 2, min_z: 0, max_z: height(), height_m: height() } };
      }
      return {
        group_by: p.group_by ?? "category",
        groups: [{ key: "Mass", count: 2, area_m2: 1200, volume_m3: footprint * height() }],
        totals: { count: 2, area_m2: 1200, volume_m3: footprint * height(), min_z: 0, max_z: height(), height_m: height() },
        floor_area: { total_m2: 0, by_level: {} },
        mass_floor_area_m2: footprint * floors(),
        rooms: { count: 0, area_m2: 0 },
      };
    },

    "revit.capture_viewport": (p) => {
      if (p.save_path) {
        fs.mkdirSync(path.dirname(p.save_path), { recursive: true });
        fs.writeFileSync(p.save_path, Buffer.from(PNG, "base64"));
      }
      return { view: p.view ?? `Claude capture (${p.direction ?? "iso_sw"})`, width: 1, height: 1, mime_type: "image/png", ...(p.save_path ? { saved_path: p.save_path } : {}), ...(p.return_image === false ? {} : { image_base64: PNG }) };
    },

    "revit.export": (p) => {
      fs.mkdirSync(path.dirname(p.path), { recursive: true });
      fs.writeFileSync(p.path, `# fake export of ${p.ids ? p.ids.join(",") : "model"}\n`);
      return { path: p.path, format: path.extname(p.path).slice(1), object_count: p.ids?.length ?? 2, bytes: 20 };
    },

    "revit.get_objects": (p) => {
      const list = [...state.objects.values()].filter((o) => matches(o, p));
      return { total: list.length, returned: list.length, objects: list.map((o) => ({ id: o.id, type: o.category, name: o.name, layer: o.layer, user_text: o.user_text, bbox: o.bbox })) };
    },
    "revit.create_geometry": (p) => {
      const created = [];
      for (const [i, g] of (p.geometries ?? [p]).entries()) {
        const id = String(state.nextId++);
        const pts = g.vertices ?? g.profile ?? g.points ?? [g.location ?? [0, 0, 0]];
        const xs = pts.map((q) => q[0]);
        const ys = pts.map((q) => q[1]);
        const zs = pts.map((q) => q[2] ?? 0);
        const bbox = { min: [Math.min(...xs), Math.min(...ys), Math.min(...zs)], max: [Math.max(...xs), Math.max(...ys), Math.max(...zs) + (g.height ?? 0)] };
        bbox.center = bbox.min.map((v, k) => (v + bbox.max[k]) / 2);
        state.objects.set(id, { id, category: g.category ?? "Generic Models", name: g.name ?? "", layer: g.layer ?? "Category::Generic Models", user_text: { ...(g.user_text ?? {}) }, bbox, spec: g });
        created.push({ index: i, id, geometry_type: g.type, layer: g.layer, bbox });
      }
      return { created_count: created.length, created };
    },
    "revit.delete_objects": (p) => {
      const list = [...state.objects.values()].filter((o) => matches(o, p));
      if (p.dry_run) return { dry_run: true, would_delete: list.length };
      for (const o of list) state.objects.delete(o.id);
      return { deleted: list.length, requested: list.length, ids: list.map((o) => o.id) };
    },

    "dynamo.status": () => ({ available: true, dynamo_revit_version: "3.0.0", state: state.runs ? "StartedUIless" : "NotStarted", ...(state.openGraph ? { workspace: { file: state.openGraph, evaluation_count: state.runs } } : {}) }),
    "dynamo.get_workspace": () => ({ file: state.openGraph, nodes: [] }),
    "dynamo.run": (p) => {
      if (!fs.existsSync(p.path)) throw err(-32003, "Graph not found: " + p.path);
      state.openGraph = p.path;
      const applied = [];
      for (const [k, v] of Object.entries(p.inputs ?? {})) {
        const id = norm(k);
        if (!(id in state.graph)) throw err(-32003, `No node '${k}' in ${path.basename(p.path)}.`);
        state.graph[id] = v;
        applied.push({ id, value: String(v) });
      }
      state.runs++;
      const h = height();
      const errors = h > 40 ? [{ node: "Form.ByLoftCrossSections", messages: ["Hauteur trop grande pour le gabarit"] }] : [];
      return {
        path: p.path,
        evaluated: true,
        evaluation_count: state.runs,
        duration_s: 0.4,
        inputs_applied: applied,
        outputs: [
          { name: "Surface plancher", id: "7e8f9a0b1c2d4e3f8a4b5c6d7e8f9a01", node_type: "Watch", state: "Active", value: footprint * floors() },
          { name: "Masses", id: "8f9a0b1c2d3e4f4a9b5c6d7e8f9a0b12", node_type: "DSFunction", state: "Active", value: masses().map((m) => ({ element_id: m.id, revit_class: "Form" })) },
          { name: "Volumes", id: "9a0b1c2d3e4f4a5b8c6d7e8f9a0b1c23", node_type: "DSFunction", state: "Active", value: [footprint * h / 2, footprint * h / 2] },
        ],
        errors,
        warnings: [],
        dynamo_state: "StartedUIless",
      };
    },

    // ---- analysis.* (same contract as the Rhino bridge)
    "analysis.footprints": (p) => {
      const wanted = p.ids ?? (p.filter?.ids);
      const items = masses()
        .filter((m) => !wanted || wanted.includes(m.id))
        .map((m) => ({
          id: m.id,
          name: `Masse ${m.id}`,
          layer: "Category::Mass",
          source: "revit",
          parts: [{ outer: [[m.x, m.y], [m.x + 20, m.y], [m.x + 20, m.y + 30], [m.x, m.y + 30]], holes: [] }],
          area: footprint,
          base_z: 0,
          top_z: height(),
          height: height(),
          volume: footprint * height(),
          centroid: [m.x + 10, m.y + 15],
          user_text: { floors: String(floors()) },
        }));
      return { count: items.length, total_area: footprint * items.length, items };
    },
    "analysis.curves": (p) => {
      const cats = (p.categories ?? p.filter?.categories ?? []).map((c) => c.toLowerCase());
      const layer = p.layer ?? "";
      if (cats.includes("property lines") || /property/i.test(layer)) {
        return { count: 1, items: [{ id: "2001", object_id: "2001", name: "Parcelle", layer: "Category::Property Lines", closed: true, points: [[-10, -10, 0], [90, -10, 0], [90, 70, 0], [-10, 70, 0]], length: 360, area: 8000, user_text: { zone: "UA" } }] };
      }
      if (/voirie|road/i.test(layer)) {
        return { count: 1, items: [{ id: "2101", object_id: "2101", name: "Rue", layer: "LineStyle::Voirie", closed: false, points: [[-10, -20, 0], [90, -20, 0]], length: 100, area: 0, user_text: {} }] };
      }
      return { count: 0, items: [] };
    },
    "analysis.drape_points": (p) => ({ points: p.points.map((q) => [q[0], q[1], 0]), hits: p.points.length, misses: 0 }),
    "analysis.ray_visibility": (p) => {
      const total = (p.weights ?? p.directions.map(() => 1)).reduce((a, b) => a + b, 0);
      const values = p.points.map((_, i) => Math.round(total * (i % 2 ? 0.6 : 0.9) * 1000) / 1000);
      return { values, visible_counts: values.map(() => p.directions.length), stats: { points: p.points.length, directions: p.directions.length, min: Math.min(...values), max: Math.max(...values), mean: values.reduce((a, b) => a + b, 0) / values.length } };
    },
  };

  const server = http.createServer((req, res) => {
    const send = (status, obj) => {
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(obj));
    };
    if (req.method === "GET" && req.url === "/health") return send(200, { ok: true, service: "revit-mcp-bridge", version: "1.0.0", auth: "bearer" });
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
    methods,
    token,
    async start(dir) {
      await new Promise((r) => server.listen(0, "127.0.0.1", r));
      const instances = path.join(dir, "instances");
      fs.mkdirSync(instances, { recursive: true });
      fs.writeFileSync(
        path.join(instances, `${process.pid}.json`),
        JSON.stringify({ pid: process.pid, port: server.address().port, host: "127.0.0.1", token, host_version: "Revit 2025", rhino_version: "Revit 2025", bridge_version: "1.0.0", started_at: new Date().toISOString(), document: "Oran_Ilot" }),
      );
      return server.address().port;
    },
    stop: () => new Promise((r) => server.close(r)),
  };
}

export function tempDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}
