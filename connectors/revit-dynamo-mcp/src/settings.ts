import os from "node:os";
import path from "node:path";
import { loadConfig, type Config } from "../../rhino-grasshopper-mcp/src/config.js";
import { REVIT } from "../../rhino-grasshopper-mcp/src/host.js";

/** Revit connector configuration: the shared bridge settings plus the Dynamo graph folders. */
export interface RevitConfig extends Config {
  /** Folders searched for Dynamo graphs (.dyn): the Dynamo Player folder(s) and the workspace. */
  graphFolders: string[];
}

function clean(value: string | undefined): string | undefined {
  if (value === undefined) return undefined;
  const v = value.trim();
  if (!v || /^\$\{.*\}$/.test(v)) return undefined;
  return v;
}

export function loadRevitConfig(env: NodeJS.ProcessEnv = process.env): RevitConfig {
  const base = loadConfig(env, REVIT);
  const configured = (clean(env.REVIT_MCP_GRAPHS) ?? "")
    .split(/[;\n]/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => path.resolve(s.replace(/^~(?=$|[\\/])/, os.homedir())));
  const graphFolders = [...new Set([...configured, path.join(base.workspace, "graphs")])];
  return { ...base, graphFolders };
}
