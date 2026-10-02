// Bundles the server and its dependencies into one CommonJS file for the .mcpb package:
// build/mcpb/server/index.cjs (runs on the Node.js shipped with Claude Desktop, no npm install).
import { build } from "esbuild";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outfile = process.argv[2] ?? path.join(root, "build", "mcpb", "server", "index.cjs");

await build({
  entryPoints: [path.join(root, "src", "index.ts")],
  outfile,
  bundle: true,
  platform: "node",
  target: "node18",
  format: "cjs",
  sourcemap: false,
  minify: false,
  legalComments: "inline",
  logLevel: "warning",
});
console.log(`bundled → ${path.relative(root, outfile)}`);
