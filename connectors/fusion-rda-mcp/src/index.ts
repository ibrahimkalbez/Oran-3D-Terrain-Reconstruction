#!/usr/bin/env node
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { loadRevitConfig } from "../../revit-dynamo-mcp/src/settings.js";
import { createFusionRevitServer, SERVER_NAME, SERVER_VERSION } from "./server.js";

async function main(): Promise<void> {
  const config = loadRevitConfig();
  const { server } = createFusionRevitServer(config);
  await server.connect(new StdioServerTransport());
  console.error(`${SERVER_NAME} ${SERVER_VERSION} ready — workspace ${config.workspace}, graphs ${config.graphFolders.join("; ")}`);
}

main().catch((err) => {
  console.error(`${SERVER_NAME} failed to start:`, err);
  process.exit(1);
});
