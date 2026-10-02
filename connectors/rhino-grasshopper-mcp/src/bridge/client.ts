import type { Config } from "../config.js";
import { discover, type DiscoveredInstance } from "./discovery.js";

export type BridgeErrorKind = "connection" | "auth" | "timeout" | "rpc" | "protocol";

/** Error raised by a bridge call. `data` carries hints such as the available parameter names. */
export class BridgeError extends Error {
  constructor(
    message: string,
    readonly kind: BridgeErrorKind,
    readonly code?: number,
    readonly data?: unknown,
  ) {
    super(message);
    this.name = "BridgeError";
  }
}

/** JSON-RPC error codes defined by the Rhino bridge (see rhino-bridge/src/.../RpcError.cs). */
export const RpcCodes = {
  NoActiveDocument: -32001,
  GrasshopperUnavailable: -32002,
  NotFound: -32003,
  Timeout: -32004,
  Ambiguous: -32005,
  OperationFailed: -32006,
  Unauthorized: -32010,
} as const;

interface Endpoint {
  host: string;
  port: number;
  token: string;
  pid?: number;
  document?: string | null;
  source: "config" | "discovery";
}

export interface CallOptions {
  timeoutMs?: number;
}

const NOT_RUNNING_HELP =
  "Rhino is not reachable. Check that Rhino 8 is open and that the RhinoMcpBridge plug-in is installed " +
  "(type McpBridgeStatus in the Rhino command line; McpBridgeStart starts it).";

/**
 * Client of the Rhino bridge. Finds the running Rhino automatically, retries once when Rhino was
 * restarted (new port or token), and turns JSON-RPC errors into BridgeError.
 */
export class BridgeClient {
  private endpoint?: Endpoint;
  private nextId = 1;

  constructor(private readonly config: Config) {}

  get current(): Endpoint | undefined {
    return this.endpoint;
  }

  /** Forces the next call to use this Rhino instance (pid). */
  async select(pid: number): Promise<DiscoveredInstance> {
    const { instances } = await discover(this.config.bridgeDirs);
    const inst = instances.find((i) => i.pid === pid);
    if (!inst) throw new BridgeError(`No Rhino instance with pid ${pid}.`, "connection");
    if (!inst.reachable) throw new BridgeError(`Rhino ${pid} does not answer.`, "connection");
    this.endpoint = { host: inst.host, port: inst.port, token: inst.token, pid: inst.pid, document: inst.document, source: "discovery" };
    return inst;
  }

  async instances(): Promise<DiscoveredInstance[]> {
    return (await discover(this.config.bridgeDirs, this.config.instance)).instances;
  }

  private async resolve(): Promise<Endpoint> {
    if (this.endpoint) return this.endpoint;
    if (this.config.port && this.config.token) {
      this.endpoint = { host: this.config.host, port: this.config.port, token: this.config.token, source: "config" };
      return this.endpoint;
    }
    const { selected, instances } = await discover(this.config.bridgeDirs, this.config.instance);
    if (!selected) {
      const stale = instances.length > 0 ? ` (${instances.length} stale discovery file(s) found, none answering)` : "";
      throw new BridgeError(NOT_RUNNING_HELP + stale, "connection", undefined, {
        searched: this.config.bridgeDirs,
      });
    }
    this.endpoint = {
      host: selected.host,
      port: selected.port,
      token: selected.token,
      pid: selected.pid,
      document: selected.document,
      source: "discovery",
    };
    return this.endpoint;
  }

  async call<T = any>(method: string, params: Record<string, unknown> = {}, options: CallOptions = {}): Promise<T> {
    try {
      return await this.callOnce<T>(method, params, options);
    } catch (err) {
      // Rhino restarted (new port/token) or was not started yet: rediscover and retry once.
      // Only for errors that guarantee the request was not executed.
      if (err instanceof BridgeError && (err.kind === "connection" || err.kind === "auth") && this.endpoint?.source !== "config") {
        this.endpoint = undefined;
        return await this.callOnce<T>(method, params, options);
      }
      throw err;
    }
  }

  private async callOnce<T>(method: string, params: Record<string, unknown>, options: CallOptions): Promise<T> {
    const ep = await this.resolve();
    const timeoutMs = options.timeoutMs ?? this.config.timeoutMs;
    const id = this.nextId++;
    let res: Response;
    try {
      res = await fetch(`http://${ep.host}:${ep.port}/rpc`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${ep.token}` },
        body: JSON.stringify({ jsonrpc: "2.0", id, method, params: stripUndefined(params) }),
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (err) {
      const e = err as Error & { cause?: { code?: string } };
      if (e.name === "TimeoutError" || e.name === "AbortError") {
        throw new BridgeError(
          `Rhino did not answer '${method}' within ${Math.round(timeoutMs / 1000)} s. It may still be computing; ` +
            "check Rhino, then retry or raise the timeout.",
          "timeout",
        );
      }
      const code = e.cause?.code;
      if (code === "ECONNREFUSED" || code === "ECONNRESET" || code === "EHOSTUNREACH" || e.message.includes("fetch failed")) {
        throw new BridgeError(NOT_RUNNING_HELP, "connection");
      }
      throw new BridgeError(`Bridge request failed: ${e.message}`, "connection");
    }

    if (res.status === 401) throw new BridgeError("The bridge refused the token (Rhino was probably restarted).", "auth");
    let body: any;
    try {
      body = await res.json();
    } catch {
      throw new BridgeError(`Invalid response from the bridge (HTTP ${res.status}).`, "protocol");
    }
    if (!res.ok && !body?.error) throw new BridgeError(body?.error ?? `HTTP ${res.status}`, "protocol");
    if (body.error) {
      const { code, message, data } = body.error;
      throw new BridgeError(String(message), "rpc", code, data);
    }
    return body.result as T;
  }
}

function stripUndefined(obj: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) if (v !== undefined) out[k] = v;
  return out;
}
