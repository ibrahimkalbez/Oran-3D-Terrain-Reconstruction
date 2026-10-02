import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { BridgeClient } from "./bridge/client.js";
import type { Config } from "./config.js";
import type { JobManager } from "./util/jobs.js";
import type { DesignBackend } from "./variants/backend.js";
import type { VariantStore } from "./variants/store.js";

/** Shared services handed to every tool module. */
export interface ToolContext {
  server: McpServer;
  bridge: BridgeClient;
  config: Config;
  variants: VariantStore;
  jobs: JobManager;
  /** What variants and explorations drive: Grasshopper (Rhino) or Dynamo/global parameters (Revit). */
  backend: DesignBackend;
}
