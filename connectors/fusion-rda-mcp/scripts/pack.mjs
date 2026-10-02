// Packs the Fusion Revit Dynamo ANSYS extension with the shared Revit packer.
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
process.argv[2] = root;
process.argv[3] = "FusionRevitDynamoAnsys";
await import("../../revit-dynamo-mcp/scripts/pack.mjs");
