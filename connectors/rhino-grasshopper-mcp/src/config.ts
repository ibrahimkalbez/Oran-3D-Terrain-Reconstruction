import os from "node:os";
import path from "node:path";

/** Runtime configuration, read from environment variables (set by Claude Desktop from the extension settings). */
export interface Config {
  /** Fixed bridge endpoint (optional). Without it the bridge is discovered automatically. */
  host: string;
  port?: number;
  token?: string;
  /** Prefer the Rhino instance whose pid or document path contains this text. */
  instance?: string;
  /** Folders holding the bridge discovery files (one JSON file per running Rhino). */
  bridgeDirs: string[];
  /** Root folder for variants, captures and exports. */
  workspace: string;
  /** Default timeout of one bridge call. */
  timeoutMs: number;
  /** Timeout for solutions, captures and exports. */
  longTimeoutMs: number;
}

function intOrUndefined(value: string | undefined): number | undefined {
  if (!value || !value.trim()) return undefined;
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) && n > 0 ? n : undefined;
}

/** Ignores values that are unresolved MCPB placeholders such as "${user_config.port}". */
function clean(value: string | undefined): string | undefined {
  if (value === undefined) return undefined;
  const v = value.trim();
  if (!v || /^\$\{.*\}$/.test(v)) return undefined;
  return v;
}

export function defaultBridgeDirs(env: NodeJS.ProcessEnv = process.env): string[] {
  const override = clean(env.RHINO_MCP_BRIDGE_DIR);
  if (override) return [path.join(override, "instances")];
  const home = os.homedir();
  const dirs = [
    env.LOCALAPPDATA ? path.join(env.LOCALAPPDATA, "RhinoMcpBridge", "instances") : undefined,
    path.join(home, "AppData", "Local", "RhinoMcpBridge", "instances"),
    path.join(home, "Library", "Application Support", "RhinoMcpBridge", "instances"),
    path.join(home, ".local", "share", "RhinoMcpBridge", "instances"),
  ].filter((d): d is string => Boolean(d));
  return [...new Set(dirs)];
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const workspace =
    clean(env.RHINO_MCP_WORKSPACE) ?? path.join(os.homedir(), "Documents", "RhinoMCP");
  return {
    host: clean(env.RHINO_MCP_HOST) ?? "127.0.0.1",
    port: intOrUndefined(clean(env.RHINO_MCP_PORT)),
    token: clean(env.RHINO_MCP_TOKEN),
    instance: clean(env.RHINO_MCP_INSTANCE),
    bridgeDirs: defaultBridgeDirs(env),
    workspace: path.resolve(workspace),
    timeoutMs: (intOrUndefined(clean(env.RHINO_MCP_TIMEOUT_S)) ?? 120) * 1000,
    longTimeoutMs: (intOrUndefined(clean(env.RHINO_MCP_LONG_TIMEOUT_S)) ?? 1800) * 1000,
  };
}
