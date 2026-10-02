import os from "node:os";
import path from "node:path";
import { RHINO, type HostProfile } from "./host.js";

/** Runtime configuration, read from environment variables (set by Claude Desktop from the extension settings). */
export interface Config {
  /** Design application behind the bridge (Rhino by default). */
  profile: HostProfile;
  /** Fixed bridge endpoint (optional). Without it the bridge is discovered automatically. */
  host: string;
  port?: number;
  token?: string;
  /** Prefer the Rhino/Revit instance whose pid or document path contains this text. */
  instance?: string;
  /** Folders holding the bridge discovery files (one JSON file per running Rhino/Revit). */
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

export function defaultBridgeDirs(env: NodeJS.ProcessEnv = process.env, profile: HostProfile = RHINO): string[] {
  const override = clean(env[`${profile.envPrefix}_BRIDGE_DIR`]);
  if (override) return [path.join(override, "instances")];
  const home = os.homedir();
  const dirs = [
    env.LOCALAPPDATA ? path.join(env.LOCALAPPDATA, profile.appFolder, "instances") : undefined,
    path.join(home, "AppData", "Local", profile.appFolder, "instances"),
    path.join(home, "Library", "Application Support", profile.appFolder, "instances"),
    path.join(home, ".local", "share", profile.appFolder, "instances"),
  ].filter((d): d is string => Boolean(d));
  return [...new Set(dirs)];
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env, profile: HostProfile = RHINO): Config {
  const v = (name: string) => clean(env[`${profile.envPrefix}_${name}`]);
  const workspace = v("WORKSPACE") ?? path.join(os.homedir(), "Documents", profile.workspaceName);
  return {
    profile,
    host: v("HOST") ?? "127.0.0.1",
    port: intOrUndefined(v("PORT")),
    token: v("TOKEN"),
    instance: v("INSTANCE"),
    bridgeDirs: defaultBridgeDirs(env, profile),
    workspace: path.resolve(workspace),
    timeoutMs: (intOrUndefined(v("TIMEOUT_S")) ?? 120) * 1000,
    longTimeoutMs: (intOrUndefined(v("LONG_TIMEOUT_S")) ?? 1800) * 1000,
  };
}
