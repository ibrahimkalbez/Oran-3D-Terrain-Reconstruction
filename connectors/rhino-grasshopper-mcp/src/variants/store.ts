import fs from "node:fs/promises";
import path from "node:path";

/**
 * A saved design variant. Stored as <workspace>/variants/<definition>/<id>_<name>/variant.json
 * next to preview.png (viewport capture), geometry.3dm (Grasshopper outputs) and,
 * optionally, definition.gh (copy of the definition with these parameter values).
 */
export interface VariantRecord {
  id: string;
  number: number;
  name: string;
  description?: string;
  definition: { key: string; name?: string; path?: string | null };
  created_at: string;
  /** Every input of the definition at the time of the variant. */
  parameters: Record<string, unknown>;
  /** The changes requested for this variant. */
  changes: Array<{ parameter: string; value: unknown; mode?: string }>;
  metrics: Record<string, unknown>;
  outputs: unknown[];
  solution?: { duration_ms?: number; errors?: unknown[]; warnings?: unknown[] };
  dir: string;
  files: { preview?: string | null; geometry?: string | null; definition?: string | null };
  baked?: { layer: string; tag: string; count: number } | null;
  kept: boolean;
  note?: string;
}

export function slug(text: string, max = 40): string {
  const s = text
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^A-Za-z0-9._-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, max);
  return s || "variant";
}

export function variantId(n: number): string {
  return `V${String(n).padStart(2, "0")}`;
}

export class VariantStore {
  constructor(readonly workspace: string) {}

  get root(): string {
    return path.join(this.workspace, "variants");
  }

  definitionDir(definitionKey: string): string {
    return path.join(this.root, slug(definitionKey, 60));
  }

  async definitions(): Promise<string[]> {
    try {
      const entries = await fs.readdir(this.root, { withFileTypes: true });
      return entries.filter((e) => e.isDirectory()).map((e) => e.name).sort();
    } catch {
      return [];
    }
  }

  async list(definitionKey?: string): Promise<VariantRecord[]> {
    const defs = definitionKey ? [slug(definitionKey, 60)] : await this.definitions();
    const records: VariantRecord[] = [];
    for (const def of defs) {
      const dir = path.join(this.root, def);
      let entries: string[];
      try {
        entries = await fs.readdir(dir);
      } catch {
        continue;
      }
      for (const entry of entries) {
        const file = path.join(dir, entry, "variant.json");
        try {
          const rec = JSON.parse(await fs.readFile(file, "utf8")) as VariantRecord;
          rec.dir = path.join(dir, entry);
          records.push(rec);
        } catch {
          // Not a variant folder.
        }
      }
    }
    return records.sort((a, b) => a.definition.key.localeCompare(b.definition.key) || a.number - b.number);
  }

  /** Reserves the next number for a definition by creating its folder (safe against concurrent creation). */
  async allocate(definitionKey: string, name: string): Promise<{ id: string; number: number; dir: string }> {
    const defDir = this.definitionDir(definitionKey);
    await fs.mkdir(defDir, { recursive: true });
    const existing = await this.list(definitionKey);
    let n = existing.reduce((m, r) => Math.max(m, r.number), 0) + 1;
    for (;;) {
      const id = variantId(n);
      const dir = path.join(defDir, `${id}_${slug(name)}`);
      const clash = (await fs.readdir(defDir)).some((e) => e.startsWith(`${id}_`));
      if (!clash) {
        try {
          await fs.mkdir(dir);
          return { id, number: n, dir };
        } catch (err) {
          if ((err as NodeJS.ErrnoException).code !== "EEXIST") throw err;
        }
      }
      n++;
    }
  }

  async save(record: VariantRecord): Promise<void> {
    await fs.mkdir(record.dir, { recursive: true });
    const file = path.join(record.dir, "variant.json");
    const tmp = file + ".tmp";
    await fs.writeFile(tmp, JSON.stringify(record, null, 2), "utf8");
    await fs.rename(tmp, file);
  }

  /** Finds a variant by "V03", "3", "03", its name or folder name; newest definition wins on ties. */
  async find(ref: string, definitionKey?: string): Promise<VariantRecord> {
    const all = await this.list(definitionKey);
    if (all.length === 0) throw new Error(definitionKey ? `No variants saved for '${definitionKey}'.` : "No variants saved yet.");
    const r = ref.trim();
    const asNumber = /^v?0*(\d+)$/i.exec(r);
    let matches = asNumber ? all.filter((v) => v.number === Number(asNumber[1])) : [];
    if (matches.length === 0) matches = all.filter((v) => v.name.toLowerCase() === r.toLowerCase() || path.basename(v.dir) === r);
    if (matches.length === 0) matches = all.filter((v) => v.name.toLowerCase().includes(r.toLowerCase()));
    if (matches.length === 0) {
      throw new Error(`Variant '${ref}' not found. Saved: ${all.map((v) => `${v.definition.key}/${v.id} ${v.name}`).join(", ")}`);
    }
    if (matches.length > 1 && !definitionKey) {
      const defs = new Set(matches.map((m) => m.definition.key));
      if (defs.size > 1) {
        // Prefer the definition used most recently.
        matches.sort((a, b) => b.created_at.localeCompare(a.created_at));
      }
    }
    return matches[0];
  }

  async remove(record: VariantRecord): Promise<void> {
    const resolved = path.resolve(record.dir);
    if (!resolved.startsWith(path.resolve(this.root) + path.sep)) throw new Error("Refusing to delete outside the variants folder.");
    await fs.rm(resolved, { recursive: true, force: true });
  }
}
