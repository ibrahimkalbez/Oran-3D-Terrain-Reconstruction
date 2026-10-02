/**
 * Tree species catalogue. Dimensions are typical mature values (indicative): adjust them
 * per project with the "overrides" of trees_generate or by adding species to
 * <workspace>/trees/species.json. `lad` (leaf area density, m²/m³) and `cd` (drag coefficient)
 * are used by the ANSYS wind adapter to model crowns as porous zones.
 */
export type CrownShape = "round" | "oval" | "umbrella" | "cone" | "columnar" | "palm";

export interface Species {
  id: string;
  name: string;
  latin: string;
  height: number;
  crown_diameter: number;
  trunk_height: number;
  shape: CrownShape;
  evergreen: boolean;
  lad: number;
  cd: number;
  notes?: string;
}

export const SPECIES: Species[] = [
  { id: "ficus", name: "Ficus (laurier d'Inde)", latin: "Ficus microcarpa", height: 10, crown_diameter: 10, trunk_height: 2.5, shape: "round", evergreen: true, lad: 2.0, cd: 0.2, notes: "Arbre d'alignement courant, ombre dense" },
  { id: "olivier", name: "Olivier", latin: "Olea europaea", height: 7, crown_diameter: 6, trunk_height: 1.8, shape: "round", evergreen: true, lad: 1.2, cd: 0.2, notes: "Résistant à la sécheresse" },
  { id: "pin_alep", name: "Pin d'Alep", latin: "Pinus halepensis", height: 15, crown_diameter: 8, trunk_height: 7, shape: "umbrella", evergreen: true, lad: 1.0, cd: 0.2, notes: "Essence méditerranéenne des reliefs (Murdjadjo)" },
  { id: "palmier_canaries", name: "Palmier des Canaries", latin: "Phoenix canariensis", height: 12, crown_diameter: 8, trunk_height: 9, shape: "palm", evergreen: true, lad: 0.6, cd: 0.15, notes: "Fronts de mer, places" },
  { id: "washingtonia", name: "Palmier Washingtonia", latin: "Washingtonia robusta", height: 18, crown_diameter: 4, trunk_height: 16, shape: "palm", evergreen: true, lad: 0.5, cd: 0.15 },
  { id: "jacaranda", name: "Jacaranda", latin: "Jacaranda mimosifolia", height: 10, crown_diameter: 8, trunk_height: 3, shape: "umbrella", evergreen: false, lad: 1.0, cd: 0.2 },
  { id: "melia", name: "Mélia (lilas des Indes)", latin: "Melia azedarach", height: 10, crown_diameter: 8, trunk_height: 2.5, shape: "umbrella", evergreen: false, lad: 1.0, cd: 0.2 },
  { id: "platane", name: "Platane", latin: "Platanus × acerifolia", height: 20, crown_diameter: 12, trunk_height: 4, shape: "oval", evergreen: false, lad: 1.5, cd: 0.2 },
  { id: "tipuana", name: "Tipuana", latin: "Tipuana tipu", height: 15, crown_diameter: 12, trunk_height: 3, shape: "umbrella", evergreen: false, lad: 1.0, cd: 0.2 },
  { id: "bigaradier", name: "Bigaradier (oranger amer)", latin: "Citrus × aurantium", height: 5, crown_diameter: 4, trunk_height: 1.5, shape: "round", evergreen: true, lad: 2.0, cd: 0.2, notes: "Petit arbre d'alignement" },
  { id: "caroubier", name: "Caroubier", latin: "Ceratonia siliqua", height: 8, crown_diameter: 8, trunk_height: 2, shape: "round", evergreen: true, lad: 1.5, cd: 0.2 },
  { id: "cypres", name: "Cyprès de Provence", latin: "Cupressus sempervirens", height: 15, crown_diameter: 2.5, trunk_height: 1, shape: "columnar", evergreen: true, lad: 2.5, cd: 0.25, notes: "Brise-vent" },
];

export function findSpecies(key: string, extra: Species[] = []): Species {
  const all = [...extra, ...SPECIES];
  const k = key.toLowerCase();
  const s = all.find((x) => x.id === k || x.name.toLowerCase() === k || x.latin.toLowerCase() === k) ?? all.find((x) => x.name.toLowerCase().includes(k) || x.latin.toLowerCase().includes(k));
  if (!s) throw new Error(`Unknown species '${key}'. Available: ${all.map((x) => x.id).join(", ")}`);
  return s;
}
