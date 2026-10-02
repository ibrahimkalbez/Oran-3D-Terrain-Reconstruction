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
export const SERVER_VERSION = "1.1.0";

export const INSTRUCTIONS = `${CORE_INSTRUCTIONS}

Fusion modules (on top of the Rhino/Grasshopper tools above):
- Urban rules: urban_site_detect finds the buildings/plots/roads/green layers; urban_rules_check verifies heights, floors, CES (coverage), COS (FAR), green ratio, setbacks and spacing, plus rules on Grasshopper metrics. Built-in rule sets are EXAMPLES: ask the user for the values of the applicable POS/PDAU and save them with urban_rules_save.
- Variant generator under urban AND physical constraints: design_explore (grid / random / Latin hypercube, Pareto front) and design_optimize (genetic algorithm). For each design: solve → urban rules ('rules') → physical simulations ('simulations': solar sun hours, ANSYS Workbench project, PyAnsys/journal script) → 'simulation_constraints' → objectives (which may use simulation metrics such as solar.sun_hours_mean). Give design_outputs (the Grasshopper outputs holding the buildings) and, for solar, 'context' (the surrounding city layer). Both run as background jobs.
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
              ", with a solar simulation (winter solstice) in 'simulations' and a minimum sunlight in 'simulation_constraints'" +
              (" — add an ANSYS Workbench simulation if I give you a project. 4) Compare the saved variants with variant_compare. ") +
              "5) Summarise the recommendation with images.",
          },
        },
      ],
    }),
  );
  server.registerPrompt(
    "variantes_contraintes_simulations",
    {
      title: "Variantes sous contraintes urbaines et simulations",
      description: "Générer des variantes Grasshopper qui respectent le règlement et des seuils de simulation physique (soleil, vent ANSYS).",
      argsSchema: {
        parametres: z.string().describe("Paramètres à faire varier et leurs plages, ex. Building_Height 12–30 m"),
        reglement: z.string().optional().describe("Nom du règlement enregistré ou valeurs (hauteur, CES, COS…)"),
        simulations: z.string().optional().describe("ex. ensoleillement au 21 décembre ≥ 2 h sur 50 % de l'espace public ; vent ANSYS projet C:\\…\\vent.wbpj"),
        objectif: z.string().optional().describe("ex. maximiser la surface de plancher"),
      },
    },
    ({ parametres, reglement, simulations, objectif }) => ({
      messages: [
        {
          role: "user",
          content: {
            type: "text",
            text:
              `Génère des variantes en faisant varier : ${parametres}. ` +
              "Commence par grasshopper_get_parameters (noms exacts, plages, sortie qui contient les bâtiments) et urban_site_detect (calques du site et de la ville environnante). " +
              `Contraintes urbaines : ${reglement ?? "demande-moi le règlement applicable ou propose l'exemple"}. ` +
              `Simulations physiques : ${simulations ?? "ensoleillement au 21 décembre (solar) avec la ville environnante en contexte"}. ` +
              `Objectif : ${objectif ?? "à me proposer"}. ` +
              "Lance design_explore avec rules, simulations, simulation_constraints et objectives (save 'pareto'), suis la tâche avec job_status, " +
              "puis présente le tableau, le front de Pareto, les images des meilleures variantes et ta recommandation.",
          },
        },
      ],
    }),
  );
  return { server, context };
}
