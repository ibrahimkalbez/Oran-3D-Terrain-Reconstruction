#!/usr/bin/env node
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { createRevitServer, SERVER_NAME, SERVER_VERSION } from "./server.js";
import { loadRevitConfig } from "./settings.js";

async function main(): Promise<void> {
  const config = loadRevitConfig();
  const { server } = createRevitServer(config);
  await server.connect(new StdioServerTransport());
  // stdout is the MCP channel: diagnostics go to stderr (Claude Desktop's MCP logs).
  console.error(`${SERVER_NAME} ${SERVER_VERSION} ready — workspace ${config.workspace}, graphs ${config.graphFolders.join("; ")}`);
}

main().catch((err) => {
  console.error(`${SERVER_NAME} failed to start:`, err);
  process.exit(1);
});
