import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { loadConfig, type Config } from "../../rhino-grasshopper-mcp/src/config.js";
import { createContext, INSTRUCTIONS as CORE_INSTRUCTIONS, registerCoreTools } from "../../rhino-grasshopper-mcp/src/server.js";
import { extendContext, type FusionContext } from "./context.js";
import { registerExploreTools } from "./tools/explore.js";
import { registerSimTools } from "./tools/sim.js";
import { registerTreeTools } from "./tools/trees.js";
import { registerUrbanTools } from "./tools/urban.js";

export const SERVER_NAME = "fusion-rhino-grasshopper-ansys";
export const SERVER_VERSION = "1.0.0";

export const INSTRUCTIONS = `${CORE_INSTRUCTIONS}

Fusion modules (on top of the Rhino/Grasshopper tools above):
- Urban rules: urban_site_detect finds the buildings/plots/roads/green layers; urban_rules_check verifies heights, floors, CES (coverage), COS (FAR), green ratio, setbacks and spacing, plus rules on Grasshopper metrics. Built-in rule sets are EXAMPLES: ask the user for the values of the applicable POS/PDAU and save them with urban_rules_save.
- Variant generator: design_explore (grid / random / Latin hypercube, rules as feasibility, Pareto front) and design_optimize (genetic algorithm). Both run as background jobs.
- Tree generator: trees_generate plants trees along streets or in areas, avoiding buildings and roads, on the terrain; trees_stats gives canopy cover.
- Simulation manager: sim_solvers lists solar (native sun hours), ansys_workbench (drives a validated ANSYS Workbench project: geometry swap, parameters, batch update, output parameters) and command (PyFluent / PyMAPDL scripts, Fluent/MAPDL journals). sim_run links results to a variant so variant_compare can rank on them. sim_wind_domain sizes a CFD domain.
Never present example rule values or indicative tree sizes as official; state the assumptions of every simulation (dates, wind speed, reference values).`;

export function createFusionServer(config: Config = loadConfig()): { server: McpServer; context: FusionContext } {
  const server = new McpServer({ name: SERVER_NAME, version: SERVER_VERSION }, { instructions: INSTRUCTIONS });
  const context = extendContext(createContext(server, config));
  registerCoreTools(context);
  registerUrbanTools(context);
  registerTreeTools(context);
  registerExploreTools(context);
  registerSimTools(context);

  server.registerPrompt(
    "urban_study",
    {
      title: "Urban design study",
      description: "Complete study of the open model: rules, variants, trees and sun.",
      argsSchema: { objective: z.string().optional().describe("e.g. maximise floor area while respecting the POS") },
    },
    ({ objective }) => ({
      messages: [
        {
          role: "user",
          content: {
            type: "text",
            text:
              "Run an urban design study on the open model. 1) urban_site_detect and confirm the layers with me. " +
              "2) Ask me for the rule values (POS/PDAU) or use the example set if I say so, then urban_rules_check. " +
              "3) grasshopper_get_parameters, then design_explore on the main parameters with the rules as constraints" +
              (objective ? ` and this objective: ${objective}` : "") +
              ". 4) For the best variants, run a solar analysis (sim_run solver 'solar', winter solstice) and compare with variant_compare. " +
              "5) Summarise the recommendation with images.",
          },
        },
      ],
    }),
  );
  return { server, context };
}
