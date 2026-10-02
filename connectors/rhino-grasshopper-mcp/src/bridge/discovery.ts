import fs from "node:fs/promises";
import path from "node:path";

/** Content of %LOCALAPPDATA%\RhinoMcpBridge\instances\<pid>.json written by the Rhino plug-in. */
export interface BridgeInstance {
  pid: number;
  port: number;
  host: string;
  token: string;
  rhino_version?: string;
  bridge_version?: string;
  started_at?: string;
  document?: string | null;
  file?: string;
}

export interface DiscoveredInstance extends BridgeInstance {
  process_alive: boolean;
  reachable: boolean;
}

export async function readInstances(dirs: string[]): Promise<BridgeInstance[]> {
  const found: BridgeInstance[] = [];
  for (const dir of dirs) {
    let names: string[];
    try {
      names = await fs.readdir(dir);
    } catch {
      continue;
    }
    for (const name of names) {
      if (!name.endsWith(".json")) continue;
      const file = path.join(dir, name);
      try {
        const data = JSON.parse(await fs.readFile(file, "utf8"));
        if (typeof data.port === "number" && typeof data.token === "string") {
          found.push({ host: "127.0.0.1", ...data, file });
        }
      } catch {
        // Half-written or foreign file: ignore.
      }
    }
  }
  return found;
}

export function isProcessAlive(pid: number): boolean {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === "EPERM";
  }
}

export async function probe(host: string, port: number, timeoutMs = 1500): Promise<boolean> {
  try {
    const res = await fetch(`http://${host}:${port}/health`, { signal: AbortSignal.timeout(timeoutMs) });
    if (!res.ok) return false;
    const body = (await res.json()) as { service?: string };
    return body.service === "rhino-mcp-bridge";
  } catch {
    return false;
  }
}

/**
 * Lists the Rhino instances that published a discovery file, checks which ones answer,
 * and picks one: the instance matching `preference` (pid or part of the document path),
 * otherwise the most recently started reachable one.
 */
export async function discover(
  dirs: string[],
  preference?: string,
): Promise<{ selected?: DiscoveredInstance; instances: DiscoveredInstance[] }> {
  const raw = await readInstances(dirs);
  const instances: DiscoveredInstance[] = await Promise.all(
    raw.map(async (inst) => {
      const alive = isProcessAlive(inst.pid);
      const reachable = alive ? await probe(inst.host, inst.port) : false;
      return { ...inst, process_alive: alive, reachable };
    }),
  );
  const usable = instances
    .filter((i) => i.reachable)
    .sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? ""));

  let selected: DiscoveredInstance | undefined;
  if (preference) {
    const p = preference.toLowerCase();
    selected = usable.find(
      (i) => String(i.pid) === preference || (i.document ?? "").toLowerCase().includes(p),
    );
  }
  selected ??= usable[0];
  return { selected, instances };
}
