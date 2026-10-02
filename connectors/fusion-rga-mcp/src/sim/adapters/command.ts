import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { exportGeometry, runCommand, type SimCase, type SolverAdapter } from "../store.js";
import { collectWindComfort } from "./workbench.js";

/** Replaces {case_dir}, {geometry}, {parameters} and {name} (a variant parameter) in a command argument. */
export function expandArgument(arg: string, c: SimCase): string {
  const params: Record<string, unknown> = c.settings._variant_parameters ?? {};
  return arg.replace(/\{([^{}]+)\}/g, (whole, key: string) => {
    if (key === "case_dir") return c.dir;
    if (key === "geometry") return c.inputs.geometry ? path.join(c.dir, c.inputs.geometry) : whole;
    if (key === "parameters") return path.join(c.dir, "parameters.json");
    if (key in params) return String(params[key]);
    return whole;
  });
}

/**
 * Any solver driven by a command line: a PyFluent or PyMAPDL script written by your engineer,
 * Fluent with a journal (fluent 3ddp -g -i run.jou), MAPDL in batch, Ansys Discovery, or another
 * tool. The script receives the case folder, the exported geometry and parameters.json, and
 * writes results.json ({"metric": number, …}) and optionally a velocity CSV.
 */
export const commandAdapter: SolverAdapter = {
  id: "command",
  title: "Commande / script de simulation (PyFluent, PyMAPDL, journal Fluent, autre)",
  description:
    "Exécute une ligne de commande avec la géométrie exportée et les paramètres de la variante, puis lit results.json. " +
    "Convient aux scripts PyAnsys (ansys-fluent-core, ansys-mapdl-core) et aux journaux Fluent/MAPDL en mode batch.",
  settings: {
    command: "Commande et arguments, ex. [\"python\", \"C:/sim/wind.py\", \"{case_dir}\", \"{geometry}\"]. Jetons: {case_dir} {geometry} {parameters} {<paramètre>}",
    geometry: "Géométrie à exporter: {layer|ids|filter|grasshopper} (facultatif)",
    geometry_format: "Extension: stl (défaut), step, igs, obj, 3dm…",
    results_file: "Fichier de résultats JSON écrit par le script (défaut results.json)",
    velocity_csv: "Fichier x,y,z,vitesse (vent au niveau piéton) → métriques de confort Lawson",
    reference_speed: "Vitesse de référence du vent simulé (m/s)",
    timeout_minutes: "Durée maximale (défaut 240)",
  },

  async check(_ctx, settings) {
    const exe = settings?.command?.[0];
    if (!exe) return { available: true, detail: "Donnez 'command' dans les réglages du cas." };
    const looksLikePath = /[\\/]/.test(exe);
    if (looksLikePath && !fs.existsSync(exe)) return { available: false, detail: `Exécutable introuvable: ${exe}` };
    return { available: true, detail: looksLikePath ? exe : `${exe} (recherché dans le PATH au lancement)` };
  },

  async prepare(ctx, c) {
    const s = c.settings;
    if (!Array.isArray(s.command) || s.command.length === 0) throw new Error("'command' must be a list: [program, arg1, …].");
    if (s.geometry) {
      const ext = String(s.geometry_format ?? "stl").replace(/^\./, "");
      const file = await exportGeometry(ctx, s.geometry, path.join(c.dir, `geometry.${ext}`));
      c.inputs.geometry = path.basename(file);
    }
    await fsp.writeFile(
      path.join(c.dir, "parameters.json"),
      JSON.stringify({ case: c.id, variant: c.variant, parameters: s._variant_parameters ?? {}, settings: s }, null, 2),
    );
    c.inputs.parameters = "parameters.json";
    c.command = (s.command as string[]).map((a) => expandArgument(String(a), c));
  },

  async run(_ctx, c, h) {
    await runCommand(c, h, Number(c.settings.timeout_minutes ?? 240) * 60000);
  },

  async collect(_ctx, c) {
    const file = path.join(c.dir, c.settings.results_file ?? "results.json");
    if (fs.existsSync(file)) {
      const data = JSON.parse(await fsp.readFile(file, "utf8"));
      const metrics: Record<string, number> = {};
      for (const [k, v] of Object.entries(data.metrics ?? data)) if (typeof v === "number" && Number.isFinite(v)) metrics[k] = v;
      c.metrics = metrics;
      c.results = data;
    } else if (!c.settings.velocity_csv) {
      throw new Error(`The script wrote no ${path.basename(file)} in ${c.dir}.`);
    }
    await collectWindComfort(c);
  },
};
