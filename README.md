# Oran-3D-Terrain-Reconstruction
3D reconstruction of Oran using Rhino models, Copernicus DEM data fusion and preparation for 3D printing.

## Connecteurs IA (Claude ↔ Rhino / Grasshopper / Revit / Dynamo / ANSYS)

Le dossier [`connectors/`](connectors/) contient les connecteurs MCP qui permettent à Claude de piloter :

- **Rhino 8 + Grasshopper** — *Rhino Grasshopper Connector* et *Fusion Rhino Grasshopper ANSYS*.
  Installation : [`connectors/release/INSTALLATION.md`](connectors/release/INSTALLATION.md).
- **Revit 2022–2026 + Dynamo** (graphes exécutés comme Dynamo Player) — *Revit Dynamo Connector* et
  *Fusion Revit Dynamo ANSYS* (règles d'urbanisme, variantes sous contraintes urbaines et simulations
  physiques, arbres, ensoleillement, ANSYS). Installation :
  [`connectors/release/INSTALLATION_REVIT.md`](connectors/release/INSTALLATION_REVIT.md) ·
  guide : [`connectors/docs/REVIT.md`](connectors/docs/REVIT.md).
