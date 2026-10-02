import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { BridgeClient } from "./bridge/client.js";
import { loadConfig, type Config } from "./config.js";
import type { ToolContext } from "./context.js";
import { registerGrasshopperTools } from "./tools/grasshopper.js";
import { registerRhinoTools } from "./tools/rhino.js";
import { registerVariantTools } from "./tools/variants.js";
import { JobManager } from "./util/jobs.js";
import { VariantStore } from "./variants/store.js";

export const SERVER_NAME = "rhino-grasshopper-connector";
export const SERVER_VERSION = "1.0.1";

export const INSTRUCTIONS = `Rhino Grasshopper Connector — drives the user's open Rhino 8 and Grasshopper through the RhinoMcpBridge plug-in.

How to work:
1. Discover before acting: rhino_get_document (units, layers, georeferencing), grasshopper_get_parameters (inputs with ranges, outputs).
2. Change the design with grasshopper_set_parameter. For relative requests use mode "percent" (e.g. +10 % → value 10), "add" or "multiply". It re-solves and returns how each metric changed: explain those changes to the user (old → new, %).
3. Variants: variant_create saves one (parameters, metrics, image, geometry); variant_generate makes a series from a list or a sweep and compares them; variant_compare ranks them with objectives; variant_keep stars the interesting ones; variant_get shows one ("variante 03" = V03) with its image; variant_apply puts one back into Grasshopper.
4. Show results with rhino_capture_viewport (and grasshopper_capture_canvas for the graph).
5. Geometry: rhino_create_geometry builds many objects in one undoable step (buildings = extrusion of a footprint with a height). Coordinates are in the document units.

Rules: every modification is one Undo step in Rhino. Never delete objects or variants without the user's intent; use dry_run for filter-based deletes. Use ids returned by previous calls rather than guessing. If a name is not found, the error lists the available names: pick the closest and retry. Long series run as background jobs: follow them with job_status.`;

export interface CreatedServer {
  server: McpServer;
  context: ToolContext;
}

/** Shared services for a server (also used by the Fusion connector, which extends this one). */
export function createContext(server: McpServer, config: Config): ToolContext {
  return {
    server,
    bridge: new BridgeClient(config),
    config,
    variants: new VariantStore(config.workspace),
    jobs: new JobManager(),
  };
}

/** Registers the Rhino, Grasshopper and variant tools and the prompts of this connector. */
export function registerCoreTools(context: ToolContext): void {
  registerRhinoTools(context);
  registerGrasshopperTools(context);
  registerVariantTools(context);
  registerPrompts(context.server);
}

export function createServer(config: Config = loadConfig()): CreatedServer {
  const server = new McpServer({ name: SERVER_NAME, version: SERVER_VERSION }, { instructions: INSTRUCTIONS });
  const context = createContext(server, config);
  registerCoreTools(context);
  return { server, context };
}

function registerPrompts(server: McpServer): void {
  server.registerPrompt(
    "variant_study",
    {
      title: "Variant study",
      description: "Explore a parameter of the open Grasshopper definition and compare the results.",
      argsSchema: {
        parameter: z.string().describe("Parameter to vary, e.g. Building_Height"),
        values: z.string().describe("Values, e.g. 12, 15, 18"),
        objective: z.string().optional().describe("What makes a variant better, e.g. maximise floor area"),
      },
    },
    ({ parameter, values, objective }) => ({
      messages: [
        {
          role: "user",
          content: {
            type: "text",
            text:
              `Study the parameter "${parameter}" of the open Grasshopper definition with the values ${values}. ` +
              "First read the parameters (grasshopper_get_parameters) to find the exact name and range. " +
              "Then run variant_generate with a sweep on these values, show the images, present the comparison table, " +
              (objective ? `rank the variants for this objective: ${objective}, ` : "") +
              "and tell me which variant you recommend and why. Keep (variant_keep) the best one.",
          },
        },
      ],
    }),
  );

  server.registerPrompt(
    "model_report",
    {
      title: "Model report",
      description: "Describe the open Rhino model: units, georeferencing, layers, object types, possible problems.",
      argsSchema: {},
    },
    () => ({
      messages: [
        {
          role: "user",
          content: {
            type: "text",
            text:
              "Analyse the open Rhino model: call rhino_get_document, then rhino_get_objects on the main layers with " +
              "detail 'full' where useful. Report units and tolerance, georeferencing (earth anchor, coordinate ranges), " +
              "dimensions, layers and what they contain (buildings, roads, terrain…), invalid or open geometry, and " +
              "missing elements. Finish with a capture of the model (rhino_capture_viewport, direction aerial).",
          },
        },
      ],
    }),
  );
}
