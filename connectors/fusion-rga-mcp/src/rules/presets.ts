import fs from "node:fs/promises";
import path from "node:path";
import type { RuleSet } from "./engine.js";

/**
 * Built-in rule sets. The values are EXAMPLES chosen to exercise every rule type; they are not
 * the regulation of any real zone. Replace them with the values of the applicable urban plan
 * (PDAU / POS, permis de construire) through urban_rules_save.
 */
export const PRESETS: RuleSet[] = [
  {
    name: "exemple_zone_urbaine",
    description:
      "Exemple de règlement de zone urbaine mixte (valeurs indicatives à remplacer par celles du POS/PDAU applicable).",
    reference: "Exemple pédagogique — non opposable",
    floor_height: 3,
    rules: [
      { id: "H_MAX", type: "max_height", value: 30, label: "Hauteur maximale 30 m" },
      { id: "NIV_MAX", type: "max_floors", value: 9, label: "Gabarit R+8 maximum" },
      { id: "CES", type: "max_coverage", value: 0.6, label: "Coefficient d'emprise au sol (CES) ≤ 0,6" },
      { id: "COS", type: "max_far", value: 3.0, label: "Coefficient d'occupation du sol (COS) ≤ 3,0" },
      { id: "EV", type: "min_green_ratio", value: 0.15, label: "Espaces verts ≥ 15 % de la parcelle", severity: "warning" },
      {
        id: "RECUL_LIM",
        type: "min_boundary_setback",
        ratio: 0.5,
        min: 4,
        allow_contiguous: true,
        label: "Recul sur limites séparatives ≥ H/2 (min. 4 m) ou implantation en limite",
      },
      {
        id: "PROSPECT",
        type: "min_building_spacing",
        ratio: 1.0,
        min: 6,
        allow_contiguous: true,
        label: "Distance entre bâtiments ≥ hauteur du plus haut (min. 6 m)",
      },
    ],
  },
  {
    name: "exemple_objectifs_variantes",
    description: "Exemple de contraintes portant sur les métriques Grasshopper d'une variante (noms à adapter à vos sorties).",
    reference: "Exemple",
    rules: [
      { id: "SURF_MIN", type: "metric_min", metric: "gfa", value: 5000, label: "Surface de plancher ≥ 5 000 m²" },
      { id: "DENSITE", type: "expression", expression: "far <= 3 and coverage <= 0.6", label: "COS ≤ 3 et CES ≤ 0,6" },
      { id: "HAUTEUR", type: "metric_max", metric: "height_max", value: 30, label: "Hauteur max ≤ 30 m" },
    ],
  },
];

/** Rule sets saved by the user in <workspace>/rules/*.json, plus the presets. */
export class RuleStore {
  constructor(private readonly workspace: string) {}

  get dir(): string {
    return path.join(this.workspace, "rules");
  }

  async list(): Promise<Array<RuleSet & { source: "preset" | "workspace"; file?: string }>> {
    const out: Array<RuleSet & { source: "preset" | "workspace"; file?: string }> = PRESETS.map((p) => ({ ...p, source: "preset" as const }));
    try {
      for (const f of await fs.readdir(this.dir)) {
        if (!f.endsWith(".json")) continue;
        try {
          const rs = JSON.parse(await fs.readFile(path.join(this.dir, f), "utf8")) as RuleSet;
          const i = out.findIndex((o) => o.name === rs.name);
          const entry = { ...rs, source: "workspace" as const, file: path.join(this.dir, f) };
          if (i >= 0) out[i] = entry;
          else out.push(entry);
        } catch {
          // Ignore malformed files.
        }
      }
    } catch {
      // No rules folder yet.
    }
    return out;
  }

  async get(name: string): Promise<RuleSet> {
    const all = await this.list();
    const rs = all.find((r) => r.name.toLowerCase() === name.toLowerCase());
    if (!rs) throw new Error(`Unknown rule set '${name}'. Available: ${all.map((r) => r.name).join(", ")}`);
    return rs;
  }

  async save(rs: RuleSet): Promise<string> {
    await fs.mkdir(this.dir, { recursive: true });
    const safe = rs.name.replace(/[^A-Za-z0-9_-]+/g, "_");
    const file = path.join(this.dir, `${safe}.json`);
    await fs.writeFile(file, JSON.stringify(rs, null, 2), "utf8");
    return file;
  }
}
