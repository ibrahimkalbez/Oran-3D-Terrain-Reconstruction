# Connecteurs IA pour Rhino / Grasshopper / ANSYS

Connecteurs MCP qui permettent à Claude de piloter **Rhino 8**, **Grasshopper** et (connecteur 2)
**ANSYS** en langage naturel, pour la conception paramétrique en architecture, urbanisme et environnement.

| Connecteur | État | Livrables |
|---|---|---|
| **1. Rhino Grasshopper Connector** | ✅ v1.0.0 | [`release/RhinoGrasshopperConnector-1.0.0.mcpb`](release/) (Claude Desktop) + [`release/RhinoMcpBridge.rhp`](release/) (Rhino 8) |
| **2. Fusion Rhino Grasshopper ANSYS** | en cours | s'appuie sur le connecteur 1 |

➡️ **Installation : [`release/INSTALLATION.md`](release/INSTALLATION.md)** (3 minutes, aucun prérequis hors Rhino 8 et Claude Desktop).

## Ce que Claude peut faire

> « Augmente la hauteur des bâtiments de 10 % et relance Grasshopper. »

Claude identifie le slider, applique +10 %, relance la solution, attend le résultat, puis explique ce qui a
changé : `Building_Height : 15 → 16,5 m · Volume +10 % · Ombre portée +10 %`.

> « Crée trois variantes avec une hauteur de 12, 15 et 18 mètres. »

Pour chaque valeur : slider modifié → solution → métriques → capture → géométrie `.3dm` enregistrée ;
puis tableau comparatif et classement, et retour aux valeurs d'origine.

> « Voici la variante 03 » · « Garde la V02 » · « Remets la V02 dans Grasshopper »

## Outils (39)

**Rhino** — `rhino_get_document` · `rhino_get_objects` · `rhino_create_geometry` · `rhino_transform_objects` ·
`rhino_delete_objects` · `rhino_update_object` · `rhino_create_layer` · `rhino_set_object_data` ·
`rhino_select_objects` · `rhino_capture_viewport` · `rhino_run_command` · `rhino_export` ·
`rhino_save_document` · `rhino_open_document` · `rhino_bridge_status`

**Grasshopper** — `grasshopper_open_definition` · `grasshopper_list_definitions` · `grasshopper_get_definition` ·
`grasshopper_get_parameters` · `grasshopper_set_parameter` · `grasshopper_solve` · `grasshopper_get_results` ·
`grasshopper_search_components` · `grasshopper_create_component` · `grasshopper_connect_components` ·
`grasshopper_export_geometry` · `grasshopper_save_definition` · `grasshopper_close_definition` ·
`grasshopper_capture_canvas`

**Variantes** — `variant_create` · `variant_generate` · `variant_list` · `variant_get` · `variant_compare` ·
`variant_apply` · `variant_keep` · `variant_delete` · `job_status` · `job_cancel`

Géométrie créable : point(s), ligne, polyligne, courbe, cercle, arc, ellipse, rectangle, polygone, surface,
surface plane, boîte, sphère, cylindre, cône, **extrusion d'emprise** (bâtiments), loft, tuyau, mesh, text dot,
texte, Brep JSON.

## Architecture

```
Claude ─MCP─► serveur MCP (Node.js, extension .mcpb) ─HTTP 127.0.0.1 + jeton─► plug-in RhinoMcpBridge (C#) ─► Rhino 8 ◄─► Grasshopper
```

Détails : [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — protocole, fil d'exécution, sécurité, tests.
Préparer ses définitions : [`docs/GRASSHOPPER_CONVENTIONS.md`](docs/GRASSHOPPER_CONVENTIONS.md).

## Arborescence

```
connectors/
  release/                  livrables prêts à installer + guide d'installation
  rhino-bridge/             plug-in Rhino (C#) et ses tests
  rhino-grasshopper-mcp/    serveur MCP du connecteur 1 (TypeScript) et ses tests
  docs/                     architecture, conventions Grasshopper
```

## Validation

- Plug-in : compile sans avertissement contre le SDK Rhino 8.0 / Grasshopper ; 14 tests (transport, sécurité,
  structure du `.rhp`, cohérence du protocole avec le serveur MCP).
- Serveur MCP : 19 tests de bout en bout avec un vrai client MCP, rejoués sur l'extension `.mcpb` empaquetée.
- **Non testé ici : l'exécution dans Rhino 8 sous Windows** (indisponible dans l'environnement de
  construction). Le premier essai du guide d'installation fait office de recette ; signalez toute erreur
  affichée par Claude ou par la ligne de commande Rhino.
