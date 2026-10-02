#!/usr/bin/env node
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { loadConfig } from "./config.js";
import { createServer, SERVER_NAME, SERVER_VERSION } from "./server.js";

async function main(): Promise<void> {
  const config = loadConfig();
  const { server } = createServer(config);
  await server.connect(new StdioServerTransport());
  // stdout is the MCP channel: diagnostics go to stderr (shown in Claude Desktop's MCP logs).
  console.error(`${SERVER_NAME} ${SERVER_VERSION} ready — workspace ${config.workspace}`);
}

main().catch((err) => {
  console.error(`${SERVER_NAME} failed to start:`, err);
  process.exit(1);
});
