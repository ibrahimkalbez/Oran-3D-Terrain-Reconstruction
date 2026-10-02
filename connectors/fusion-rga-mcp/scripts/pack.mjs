// Builds the Fusion connector extension: one-file bundle (connector 1 sources included) +
// manifest (tool list read from the server) + icon, validated and packed with the mcpb CLI.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const pkg = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
const stage = path.join(root, "build", "mcpb");
const release = path.resolve(root, "..", "release");
const out = path.join(release, `FusionRhinoGrasshopperAnsys-${pkg.version}.mcpb`);
const mcpb = path.join(path.dirname(createRequire(import.meta.url).resolve("@anthropic-ai/mcpb")), "cli", "cli.js");

fs.rmSync(stage, { recursive: true, force: true });
fs.mkdirSync(path.join(stage, "server"), { recursive: true });
await build({
  entryPoints: [path.join(root, "src", "index.ts")],
  outfile: path.join(stage, "server", "index.cjs"),
  bundle: true,
  platform: "node",
  target: "node18",
  format: "cjs",
  legalComments: "inline",
  logLevel: "warning",
});
fs.copyFileSync(path.join(root, "assets", "icon.png"), path.join(stage, "icon.png"));

const client = new Client({ name: "pack", version: "1" });
await client.connect(new StdioClientTransport({ command: process.execPath, args: [path.join(stage, "server", "index.cjs")], stderr: "ignore" }));
const { tools } = await client.listTools();
await client.close();

const firstSentence = (s = "") => (s.split(/(?<=\.)\s/)[0] ?? s).slice(0, 200);
const manifest = JSON.parse(fs.readFileSync(path.join(root, "manifest.template.json"), "utf8").replace("{{VERSION}}", pkg.version));
manifest.tools = tools.map((t) => ({ name: t.name, description: firstSentence(t.description) }));
fs.writeFileSync(path.join(stage, "manifest.json"), JSON.stringify(manifest, null, 2));

execFileSync(process.execPath, [mcpb, "validate", path.join(stage, "manifest.json")], { stdio: "inherit" });
fs.mkdirSync(release, { recursive: true });
fs.rmSync(out, { force: true });
execFileSync(process.execPath, [mcpb, "pack", stage, out], { stdio: "inherit" });
console.log(`\n${tools.length} tools → ${path.relative(path.resolve(root, ".."), out)}`);
