import fs from "node:fs";
import path from "node:path";

/**
 * Locates the ANSYS installation: explicit setting, then the AWP_ROOTxxx variables set by the
 * ANSYS installer (highest version first), then the default folder.
 */
export function findAnsysRoot(explicit?: string, env: NodeJS.ProcessEnv = process.env): { root?: string; version?: string; candidates: string[] } {
  if (explicit) return { root: explicit, version: /v(\d+)/i.exec(explicit)?.[1], candidates: [explicit] };
  const fromEnv = Object.entries(env)
    .filter(([k, v]) => /^AWP_ROOT\d+$/i.test(k) && v)
    .map(([k, v]) => ({ version: k.replace(/\D/g, ""), root: v as string }))
    .sort((a, b) => Number(b.version) - Number(a.version));
  const candidates = fromEnv.map((x) => x.root);
  const base = "C:\\Program Files\\ANSYS Inc";
  if (process.platform === "win32") {
    try {
      for (const d of fs.readdirSync(base).filter((d) => /^v\d+$/i.test(d)).sort().reverse()) candidates.push(path.join(base, d));
    } catch {
      // not installed in the default folder
    }
  }
  const root = candidates.find((c) => fs.existsSync(c));
  return { root, version: root ? /v?(\d{3})/i.exec(path.basename(root))?.[1] : fromEnv[0]?.version, candidates };
}

export function workbenchExecutable(root: string): string {
  return path.join(root, "Framework", "bin", process.platform === "win32" ? "Win64" : "Linux64", process.platform === "win32" ? "RunWB2.exe" : "runwb2");
}

export function fluentExecutable(root: string): string {
  return path.join(root, "fluent", "ntbin", "win64", "fluent.exe");
}
