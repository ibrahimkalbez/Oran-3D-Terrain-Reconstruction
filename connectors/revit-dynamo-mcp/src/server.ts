import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { ToolContext } from "../../rhino-grasshopper-mcp/src/context.js";
import { createContext } from "../../rhino-grasshopper-mcp/src/server.js";
import { registerVariantTools } from "../../rhino-grasshopper-mcp/src/tools/variants.js";
import { revitBackend } from "./backend.js";
import { loadRevitConfig, type RevitConfig } from "./settings.js";
import { registerDynamoTools } from "./tools/dynamo.js";
import { registerRevitTools } from "./tools/revit.js";

export const SERVER_NAME = "revit-dynamo-connector";
export const SERVER_VERSION = "1.0.0";

export const INSTRUCTIONS = `Revit Dynamo Connector — drives the user's open Revit (2022–2026) and runs Dynamo graphs like Dynamo Player, through the RevitMcpBridge add-in.

Units: everything exchanged is SI — meters, m², m³, degrees — in Revit's internal project coordinates (Revit stores feet internally; the add-in converts).

How to work:
1. Discover before acting: revit_get_document (levels, categories, global parameters, location, Dynamo availability), dynamo_list_graphs / dynamo_get_graph (the inputs Dynamo Player shows, with ranges).
2. Parametric changes: dynamo_run sets graph inputs (absolute, or relative with mode percent/add/multiply) and runs the graph — the elements it creates are updated, not duplicated. revit_set_global_parameters drives global parameters; revit_set_parameters changes element parameters. Explain the effect on the metrics (old → new, %).
3. Variants: variant_create / variant_generate with definition = the graph (or 'globals'): each variant applies the values, runs the graph, measures (Dynamo numeric outputs + model quantities: floor areas, volumes, counts by category + 'design.*' for the elements the graph outputs), captures an image and exports the geometry (OBJ). variant_compare ranks them, variant_apply restores one, variant_keep stars it.
4. Show results with revit_capture_view (temporary 3D views leave no trace in the project).
5. Native elements: revit_create_elements (levels, walls, floors, roofs, families, terrain from points); free-form geometry: revit_create_geometry (DirectShapes).

Rules: every modification is one Undo step in Revit ("Claude: …"). Revit warnings are returned, errors roll the change back. Never delete elements or variants without the user's intent; use dry_run. If Revit does not answer, a dialog or an edit mode is probably open: ask the user to close it. A graph must be saved in Dynamo 2.x or later; mark its result nodes "Is Output" so the connector can measure the elements it creates.`;

export interface CreatedRevitServer {
  server: McpServer;
  context: ToolContext;
}

/** Shared services with the Revit/Dynamo design backend (also used by the Fusion Revit connector). */
export function createRevitContext(server: McpServer, config: RevitConfig): ToolContext {
  return createContext(server, config, (bridge) => revitBackend(bridge, config));
}

export function registerRevitCoreTools(context: ToolContext): void {
  registerRevitTools(context);
  registerDynamoTools(context);
  registerVariantTools(context);
  registerPrompts(context.server);
}

export function createRevitServer(config: RevitConfig = loadRevitConfig()): CreatedRevitServer {
  const server = new McpServer({ name: SERVER_NAME, version: SERVER_VERSION }, { instructions: INSTRUCTIONS });
  const context = createRevitContext(server, config);
  registerRevitCoreTools(context);
  return { server, context };
}

function registerPrompts(server: McpServer): void {
  server.registerPrompt(
    "etude_variantes_dynamo",
    {
      title: "Étude de variantes Dynamo",
      description: "Explore an input of a Dynamo graph on the Revit model and compare the variants.",
      argsSchema: {
        graph: z.string().describe("Graph (.dyn) name or path"),
        parameter: z.string().describe("Input to vary, e.g. Hauteur"),
        values: z.string().describe("Values, e.g. 12, 15, 18"),
        objective: z.string().optional().describe("What makes a variant better"),
      },
    },
    ({ graph, parameter, values, objective }) => ({
      messages: [
        {
          role: "user",
          content: {
            type: "text",
            text:
              `Study the input "${parameter}" of the Dynamo graph "${graph}" on the open Revit project with the values ${values}. ` +
              "First read the graph (dynamo_get_graph) to find the exact input name and range. Then run variant_generate with " +
              `definition "${graph}" and a sweep on these values, show the images, present the comparison table, ` +
              (objective ? `rank the variants for this objective: ${objective}, ` : "") +
              "and recommend one. Keep (variant_keep) the best one and apply it (variant_apply).",
          },
        },
      ],
    }),
  );

  server.registerPrompt(
    "rapport_maquette_revit",
    {
      title: "Rapport de maquette Revit",
      description: "Describe the open Revit project: location, levels, categories, quantities, possible problems.",
      argsSchema: {},
    },
    () => ({
      messages: [
        {
          role: "user",
          content: {
            type: "text",
            text:
              "Analyse the open Revit project: revit_get_document, then revit_metrics grouped by category and by level. Report " +
              "the location (latitude/longitude, true north, base points), levels and elevations, the main categories and " +
              "quantities (floor areas per level, volumes), global parameters, the Dynamo graphs available (dynamo_list_graphs), " +
              "and anything missing or suspicious. Finish with revit_capture_view direction aerial.",
          },
        },
      ],
    }),
  );
}
