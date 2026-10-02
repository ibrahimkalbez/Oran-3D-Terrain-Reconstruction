import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { extendContext, type FusionContext } from "../../fusion-rga-mcp/src/context.js";
import { registerExploreTools } from "../../fusion-rga-mcp/src/tools/explore.js";
import { registerSimTools } from "../../fusion-rga-mcp/src/tools/sim.js";
import { registerTreeTools } from "../../fusion-rga-mcp/src/tools/trees.js";
import { registerUrbanTools } from "../../fusion-rga-mcp/src/tools/urban.js";
import { createRevitContext, INSTRUCTIONS as REVIT_INSTRUCTIONS, registerRevitCoreTools } from "../../revit-dynamo-mcp/src/server.js";
import { loadRevitConfig, type RevitConfig } from "../../revit-dynamo-mcp/src/settings.js";

export const SERVER_NAME = "fusion-revit-dynamo-ansys";
export const SERVER_VERSION = "1.0.0";

export const INSTRUCTIONS = `${REVIT_INSTRUCTIONS}

Fusion modules (on top of the Revit / Dynamo tools above):
- Urban rules: urban_site_detect finds buildings (Mass category, or a 'buildings'/'bâti' layer), plots (Property Lines), roads (line styles 'Voirie', 'Rue'…) and green areas; urban_rules_check verifies heights, floors, CES (coverage), COS (FAR), green ratio, setbacks and spacing, plus rules on design metrics (Dynamo outputs, Revit quantities). Built-in rule sets are EXAMPLES: ask the user for the values of the applicable POS/PDAU and save them with urban_rules_save. In Revit, give sources as filters, e.g. {buildings: {filter: {categories: ["Mass"]}}, plots: {filter: {categories: ["Property Lines"]}}, roads: {layer: "LineStyle::Voirie"}}.
- Variant generator under urban AND physical constraints: design_explore (grid / random / Latin hypercube, Pareto front) and design_optimize (genetic algorithm), with definition = the Dynamo graph (or 'globals' for global parameters). For each design: apply the inputs and run the graph (elements updated in place) → urban rules ('rules') → physical simulations ('simulations': solar sun hours, ANSYS Workbench project, PyAnsys/journal script) → 'simulation_constraints' → objectives (which may use simulation metrics such as solar.sun_hours_mean). design_outputs names the Dynamo output nodes that return the building elements (default: every element the graph outputs, else the Mass category). Both run as background jobs.
- Tree generator: trees_generate plants trees (Planting DirectShapes) along streets or in areas, avoiding buildings and roads, on the terrain (Toposolid/Topography); trees_stats gives canopy cover.
- Simulation manager: sim_solvers lists solar (native sun hours in Revit, project true north applied), ansys_workbench (drives a validated ANSYS Workbench project: geometry swap, parameters, batch update, output parameters) and command (PyFluent / PyMAPDL scripts, Fluent/MAPDL journals). Revit exports geometry for ANSYS as .sat (ACIS solids: SpaceClaim / DesignModeler / Discovery) or .stl — set geometry_format 'sat' (STEP is not available from Revit). sim_run links results to a variant so variant_compare can rank on them. sim_wind_domain sizes a CFD domain.
Never present example rule values or indicative tree sizes as official; state the assumptions of every simulation (dates, wind speed, reference values).`;

export function createFusionRevitServer(config: RevitConfig = loadRevitConfig()): { server: McpServer; context: FusionContext } {
  const server = new McpServer({ name: SERVER_NAME, version: SERVER_VERSION }, { instructions: INSTRUCTIONS });
  const context = extendContext(createRevitContext(server, config));
  registerRevitCoreTools(context);
  registerUrbanTools(context);
  registerTreeTools(context);
  registerExploreTools(context);
  registerSimTools(context);

  server.registerPrompt(
    "etude_urbaine_revit",
    {
      title: "Étude urbaine Revit",
      description: "Complete study of the open Revit project: rules, Dynamo variants, trees and sun.",
      argsSchema: {
        graph: z.string().describe("Dynamo massing graph (.dyn) name or path"),
        objective: z.string().optional().describe("e.g. maximise floor area while respecting the POS"),
      },
    },
    ({ graph, objective }) => ({
      messages: [
        {
          role: "user",
          content: {
            type: "text",
            text:
              "Run an urban design study on the open Revit project. 1) revit_get_document and urban_site_detect; confirm the site sources with me " +
              "(masses, property lines, road line styles, green areas). 2) Ask me for the rule values (POS/PDAU) or use the example set if I say so, " +
              `then urban_rules_check. 3) dynamo_get_graph "${graph}", then design_explore with definition "${graph}" on its main inputs with the rules ` +
              `as constraints${objective ? ` and this objective: ${objective}` : ""}, a solar simulation (winter solstice) in 'simulations' and a minimum ` +
              "sunlight in 'simulation_constraints' — add an ANSYS Workbench simulation if I give you a project. 4) Compare the saved variants with " +
              "variant_compare. 5) Plant trees along the streets of the best variant (trees_generate) and summarise the recommendation with images.",
          },
        },
      ],
    }),
  );

  server.registerPrompt(
    "variantes_dynamo_contraintes_simulations",
    {
      title: "Variantes Dynamo sous contraintes urbaines et simulations",
      description: "Générer des variantes d'un graphe Dynamo qui respectent le règlement et des seuils de simulation physique (soleil, vent ANSYS).",
      argsSchema: {
        graphe: z.string().describe("Graphe Dynamo (.dyn) qui génère le projet"),
        parametres: z.string().describe("Entrées à faire varier et leurs plages, ex. Hauteur 12–30 m, Niveaux 3–8"),
        reglement: z.string().optional().describe("Nom du règlement enregistré ou valeurs (hauteur, CES, COS…)"),
        simulations: z.string().optional().describe("ex. ensoleillement au 21 décembre ≥ 2 h sur 50 % de l'espace public ; vent ANSYS projet C:\\…\\vent.wbpj"),
        objectif: z.string().optional().describe("ex. maximiser la surface de plancher"),
      },
    },
    ({ graphe, parametres, reglement, simulations, objectif }) => ({
      messages: [
        {
          role: "user",
          content: {
            type: "text",
            text:
              `Génère des variantes du graphe Dynamo "${graphe}" en faisant varier : ${parametres}. ` +
              "Commence par dynamo_get_graph (noms exacts, plages, nœuds de sortie qui renvoient les bâtiments) et urban_site_detect (masses, lignes de propriété, voirie). " +
              `Contraintes urbaines : ${reglement ?? "demande-moi le règlement applicable ou propose l'exemple"}. ` +
              `Simulations physiques : ${simulations ?? "ensoleillement au 21 décembre (solar) sur l'espace public"}. ` +
              `Objectif : ${objectif ?? "à me proposer"}. ` +
              `Lance design_explore avec definition "${graphe}", rules, simulations, simulation_constraints et objectives (save 'pareto'), suis la tâche avec job_status, ` +
              "puis présente le tableau, le front de Pareto, les images des meilleures variantes et ta recommandation ; applique la meilleure (variant_apply).",
          },
        },
      ],
    }),
  );
  return { server, context };
}
