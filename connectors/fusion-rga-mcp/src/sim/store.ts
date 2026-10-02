import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import type { BridgeClient } from "../../../rhino-grasshopper-mcp/src/bridge/client.js";
import type { Config } from "../../../rhino-grasshopper-mcp/src/config.js";
import type { JobHandle } from "../../../rhino-grasshopper-mcp/src/util/jobs.js";

export type CaseStatus = "prepared" | "queued" | "running" | "done" | "failed" | "cancelled";

/** One simulation: inputs, command, status and results, stored in <workspace>/simulations/<id>/case.json. */
export interface SimCase {
  id: string;
  name: string;
  solver: string;
  status: CaseStatus;
  created_at: string;
  updated_at: string;
  dir: string;
  settings: Record<string, any>;
  variant?: { definition?: string; id: string } | null;
  inputs: Record<string, string>;
  command?: string[];
  job_id?: string;
  metrics?: Record<string, number>;
  results?: unknown;
  images?: string[];
  error?: string;
  log_file?: string;
  warnings?: string[];
}

export interface AdapterContext {
  bridge: BridgeClient;
  config: Config;
}

/** Contract implemented by every solver (native analyses and ANSYS products). */
export interface SolverAdapter {
  id: string;
  title: string;
  description: string;
  /** Settings documentation shown to Claude by sim_solvers. */
  settings: Record<string, string>;
  check(ctx: AdapterContext, settings?: Record<string, any>): Promise<{ available: boolean; detail: string }>;
  /** Writes the inputs into the case folder and sets case.command when an external program runs. */
  prepare(ctx: AdapterContext, c: SimCase): Promise<void>;
  /** Runs the case. Default: execute case.command (see runCommand). */
  run?(ctx: AdapterContext, c: SimCase, h: JobHandle): Promise<void>;
  /** Reads the results into case.metrics / case.results / case.images. */
  collect(ctx: AdapterContext, c: SimCase): Promise<void>;
}

export class SimulationStore {
  constructor(readonly workspace: string) {}

  get root(): string {
    return path.join(this.workspace, "simulations");
  }

  async create(solver: string, name: string, settings: Record<string, any>, variant?: SimCase["variant"]): Promise<SimCase> {
    const stamp = new Date().toISOString().replace(/[-:]/g, "").slice(0, 15);
    const id = `${solver}-${stamp}-${randomUUID().slice(0, 4)}`;
    const dir = path.join(this.root, id);
    await fsp.mkdir(dir, { recursive: true });
    const now = new Date().toISOString();
    const c: SimCase = { id, name, solver, status: "prepared", created_at: now, updated_at: now, dir, settings, variant: variant ?? null, inputs: {} };
    await this.save(c);
    return c;
  }

  async save(c: SimCase): Promise<void> {
    c.updated_at = new Date().toISOString();
    const file = path.join(c.dir, "case.json");
    // Unique temporary name: the job and a tool may save the same case at the same time.
    const tmp = `${file}.${randomUUID().slice(0, 8)}.tmp`;
    await fsp.writeFile(tmp, JSON.stringify(c, null, 2), "utf8");
    await fsp.rename(tmp, file);
  }

  async get(id: string): Promise<SimCase> {
    const file = path.join(this.root, path.basename(id), "case.json");
    try {
      return JSON.parse(await fsp.readFile(file, "utf8")) as SimCase;
    } catch {
      const all = await this.list();
      const hit = all.find((c) => c.id.startsWith(id) || c.name === id);
      if (!hit) throw new Error(`Unknown simulation '${id}'. Known: ${all.slice(0, 15).map((c) => c.id).join(", ")}`);
      return hit;
    }
  }

  async list(): Promise<SimCase[]> {
    let names: string[] = [];
    try {
      names = await fsp.readdir(this.root);
    } catch {
      return [];
    }
    const out: SimCase[] = [];
    for (const n of names) {
      try {
        out.push(JSON.parse(await fsp.readFile(path.join(this.root, n, "case.json"), "utf8")));
      } catch {
        // not a case folder
      }
    }
    return out.sort((a, b) => b.created_at.localeCompare(a.created_at));
  }
}

/**
 * Runs an external program for a case: output streamed to run.log, timeout, cancellation
 * between checks. Resolves on exit code 0, rejects otherwise with the end of the log.
 */
export async function runCommand(c: SimCase, h: JobHandle, timeoutMs: number): Promise<void> {
  if (!c.command?.length) throw new Error("The case has no command to run.");
  const logFile = path.join(c.dir, "run.log");
  c.log_file = logFile;
  const log = fs.createWriteStream(logFile, { flags: "a" });
  log.write(`$ ${c.command.map((a) => (a.includes(" ") ? `"${a}"` : a)).join(" ")}\n`);
  const child = spawn(c.command[0], c.command.slice(1), { cwd: c.dir, windowsHide: true, shell: false });
  let tail = "";
  const onData = (d: Buffer) => {
    const s = d.toString();
    log.write(s);
    tail = (tail + s).slice(-4000);
    const lastLine = s.trim().split(/\r?\n/).pop();
    if (lastLine) h.log(lastLine.slice(0, 200));
  };
  child.stdout.on("data", onData);
  child.stderr.on("data", onData);
  const started = Date.now();
  const watchdog = setInterval(() => {
    if (h.job.cancelRequested || Date.now() - started > timeoutMs) {
      child.kill();
      onData(Buffer.from(h.job.cancelRequested ? "\n[cancelled]\n" : `\n[timeout after ${Math.round(timeoutMs / 1000)} s]\n`));
    }
  }, 1000);
  try {
    const code: number | null = await new Promise((resolve, reject) => {
      child.on("error", reject);
      child.on("close", resolve);
    });
    if (h.job.cancelRequested) throw new Error("Cancelled by the user.");
    if (code !== 0) throw new Error(`Solver exited with code ${code}. End of log:\n${tail.slice(-1500)}`);
  } finally {
    clearInterval(watchdog);
    await new Promise((r) => log.end(r));
  }
}

/**
 * Exports geometry for a solver: a Rhino selection (layer/ids/filter) or Grasshopper outputs
 * (baked temporarily with a tag, exported, then removed). Returns the written file.
 */
export async function exportGeometry(
  ctx: AdapterContext,
  source: { layer?: string; ids?: string[]; filter?: Record<string, unknown>; grasshopper?: { definition?: string; outputs?: string[] } },
  file: string,
): Promise<string> {
  const long = { timeoutMs: ctx.config.longTimeoutMs };
  if (source.grasshopper) {
    const tag = `sim-export-${randomUUID().slice(0, 8)}`;
    await ctx.bridge.call("grasshopper.export_geometry", { ...source.grasshopper, layer: "MCP::SimulationExport", layer_per_output: false, bake_tag: tag, replace: true }, long);
    try {
      await ctx.bridge.call("rhino.export", { path: file, user_text: { "mcp.bake_tag": tag } }, long);
    } finally {
      await ctx.bridge.call("rhino.delete_objects", { user_text: { "mcp.bake_tag": tag }, max_count: 1_000_000 }, long).catch(() => undefined);
    }
    return file;
  }
  const filter = source.filter ?? (source.ids ? { ids: source.ids } : source.layer ? { layer: source.layer, include_sublayers: true } : undefined);
  if (!filter) throw new Error("Geometry source needs 'layer', 'ids', 'filter' or 'grasshopper'.");
  await ctx.bridge.call("rhino.export", { path: file, ...filter }, long);
  return file;
}
