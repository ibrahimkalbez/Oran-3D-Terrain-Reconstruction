# Architecture technique

```
Claude (Claude Desktop / Claude Code)
   │  MCP (JSON-RPC sur stdio)
   ▼
Serveur MCP « rhino-grasshopper-connector »        connectors/rhino-grasshopper-mcp  (TypeScript → Node.js)
   │  39 outils · découverte de Rhino · variantes · tâches de fond
   │  (le connecteur 2 « fusion-rhino-grasshopper-ansys », connectors/fusion-rga-mcp, enregistre ces
   │   mêmes outils puis ajoute règles, exploration, arbres et simulations : 58 outils)
   │  HTTP + JSON-RPC 2.0 sur 127.0.0.1, jeton Bearer
   ▼
Plug-in Rhino « RhinoMcpBridge.rhp »                connectors/rhino-bridge  (C# · RhinoCommon · Grasshopper SDK)
   │  serveur HTTP interne → file d'appels → thread UI de Rhino
   ▼
Rhino 8  ◄──►  Grasshopper  (API Grasshopper : GH_Document, sliders, paramètres, solveur)
```

## Pourquoi cette architecture

| Choix | Raison |
|---|---|
| **Plug-in C# dans Rhino** | Seul moyen d'accéder en direct au document ouvert et à Grasshopper (RhinoCommon et l'API Grasshopper ne sont accessibles que dans le processus Rhino). Compilé contre le SDK Rhino 8.0 : chargé par toutes les versions 8.x, sous les deux runtimes de Rhino 8 (.NET Core et .NET Framework). |
| **Serveur MCP séparé** | Le serveur MCP est lancé et arrêté par Claude ; Rhino vit sa vie. Un plantage d'un côté n'emporte pas l'autre. Le serveur garde l'état propre à Claude (variantes, tâches longues). |
| **Node.js pour le serveur MCP** | Claude Desktop embarque Node.js : l'extension `.mcpb` s'installe d'un double-clic, sans installer Python. Le serveur est regroupé en un seul fichier (`server/index.cjs`). |
| **HTTP local sur TcpListener** | Pas de réservation d'URL `http.sys` ni de droits administrateur sous Windows ; testable avec `curl`. |
| **Rhino.Compute non utilisé par défaut** | Compute calcule des définitions *sans* le Rhino ouvert. L'objectif ici est de piloter le Rhino de l'utilisateur, visible, avec son modèle. Le protocole du bridge est indépendant du transport : un adaptateur Compute pourra être ajouté pour les calculs en lot sans interface (voir « Évolutions »). |

## Plug-in Rhino (RhinoMcpBridge)

```
src/RhinoMcpBridge/
  Plugin/      BridgePlugIn (chargé au démarrage de Rhino), BridgeHost (serveur + découverte),
               commandes McpBridgeStart/Stop/Status/Settings, EmbeddedAssemblies (Newtonsoft.Json intégré)
  Transport/   HttpRpcServer, RpcDispatcher, RpcError, InstanceRegistry   ← indépendants de Rhino, testés
  Core/        UiThread, Args (lecture tolérante des paramètres), GeometryFactory, GeometryInfo,
               ObjectQuery (filtres), RhinoUtil (calques, attributs), ImageUtil, J (JSON)
  Handlers/    RhinoHandlers, ViewHandlers (captures), GrasshopperHandlers → GhBridge
  Display/     PreviewConduit (affichage des résultats Grasshopper et surlignages pour les captures)
```

**Fil d'exécution.** RhinoCommon et Grasshopper ne sont pas thread-safe. Le serveur HTTP reçoit sur un
thread de fond, puis `UiThread.Invoke` poste l'appel sur le thread UI de Rhino (`RhinoApp.InvokeOnUiThread`)
et attend. Si Rhino ne prend pas la requête en 90 s (boîte de dialogue ouverte), elle est annulée proprement
et Claude reçoit un message explicite ; une fois commencé, un calcul peut durer jusqu'à 60 min.

**Annulation.** Chaque appel qui modifie le document est encadré par `BeginUndoRecord/EndUndoRecord` :
une demande de Claude = un `Ctrl+Z`.

**Chargement paresseux de Grasshopper.** Seul `GhBridge` référence `Grasshopper.dll`. Rhino démarre la
passerelle sans charger Grasshopper ; il est chargé au premier appel `grasshopper.*`.

**Captures.** `ViewCapture` rend la vue demandée à la taille demandée. Les résultats Grasshopper sont
dessinés par un `DisplayConduit` propre au bridge (maquette blanche à arêtes sombres) pendant que l'aperçu
de Grasshopper est suspendu : l'image ne dépend pas des réglages d'aperçu et chaque objet n'est dessiné
qu'une fois. La caméra et le mode d'affichage de l'utilisateur sont restaurés.

## Sécurité

| Menace | Protection |
|---|---|
| Accès depuis le réseau | Écoute sur `127.0.0.1` uniquement, port exclusif |
| Autre programme local | Jeton aléatoire de 256 bits par démarrage, comparé en temps constant, publié dans `%LOCALAPPDATA%` (profil de l'utilisateur) |
| Page web malveillante (CSRF) | Requêtes avec en-tête `Origin` refusées ; en-tête `Authorization` obligatoire (impossible sans pré-vol CORS, que le bridge ne sert pas) |
| DNS rebinding | En-tête `Host` limité à `127.0.0.1` / `localhost` / `::1` |
| Erreurs destructrices de Claude | Suppression par filtre plafonnée (`max_count`), `dry_run`, annulation Rhino, instructions du serveur MCP |

## Découverte automatique

Au démarrage, le plug-in écrit `%LOCALAPPDATA%\RhinoMcpBridge\instances\<pid>.json`
(`port`, `token`, `rhino_version`, `document`…) et le supprime à la fermeture. Le serveur MCP lit ce dossier,
ignore les processus disparus, sonde `/health` et choisit l'instance préférée (réglage) ou la plus récente.
Si Rhino redémarre (nouveau port ou jeton), le serveur redécouvre et rejoue la requête une fois.

## Protocole du bridge

`POST /rpc` — JSON-RPC 2.0, en-tête `Authorization: Bearer <token>`. `GET /health` — sonde sans secret.

| Méthode | Rôle |
|---|---|
| `bridge.info`, `bridge.ping` | Versions, document, méthodes disponibles |
| `rhino.get_document` | Fichier, unités, tolérances, comptages, calques, blocs, matériaux, sélection, vues, géoréférencement |
| `rhino.get_objects` | Recherche (ids, calque, types, nom, user text, sélection) + pagination + détail |
| `rhino.create_geometry` | 24 types de géométrie, attributs, création atomique |
| `rhino.transform_objects` | translate / rotate / scale / mirror / orient / matrix, copies et réseaux |
| `rhino.delete_objects` | Suppression par ids ou filtre, `dry_run`, plafond |
| `rhino.update_object` | Nom, calque, couleur, matériau, visibilité, verrouillage, remplacement de géométrie |
| `rhino.create_layer` | Calques imbriqués, couleur, état, calque courant |
| `rhino.set_object_data` | Nom, user text, métadonnées JSON, paramètres (objets ou document) |
| `rhino.select_objects` | Sélection pour l'utilisateur, zoom |
| `rhino.capture_viewport` | Capture d'écran avec caméra, mode d'affichage, aperçu Grasshopper, surlignage |
| `rhino.list_display_modes` | Modes d'affichage disponibles |
| `rhino.run_command` | Macro de commande Rhino, objets créés |
| `rhino.export`, `rhino.save_document`, `rhino.open_document` | Fichiers |
| `grasshopper.status`, `open_definition`, `close_definition`, `save_definition` | Définitions |
| `grasshopper.get_definition` | Graphe : objets, entrées/sorties, fils, messages d'erreur |
| `grasshopper.get_parameters`, `set_parameter` | Entrées exposées ; modification absolue/relative, validation avant écriture |
| `grasshopper.solve`, `get_results` | Solution (durée, erreurs) ; résultats, statistiques, métriques |
| `grasshopper.search_components`, `create_component`, `connect_components` | Édition du graphe |
| `grasshopper.export_geometry` | Bake dans Rhino (calques, user text, remplacement par étiquette) ou fichier `.3dm` |
| `grasshopper.capture_canvas` | Image du canevas Grasshopper |
| `analysis.footprints` *(1.1)* | Emprises au sol (contours projetés, cours intérieures), hauteurs, volumes — objets Rhino ou sorties Grasshopper |
| `analysis.curves` *(1.1)* | Courbes, bords de surfaces, hachures et maillages en polylignes (parcelles, voirie, espaces verts) |
| `analysis.drape_points` *(1.1)* | Altitude du relief sous des points (lancer de rayons vertical) |
| `analysis.ray_visibility` *(1.1)* | Visibilité pondérée de directions (heures d'ensoleillement, ombres) par lancer de rayons |

Codes d'erreur : `-32001` pas de document, `-32002` Grasshopper indisponible, `-32003` introuvable
(avec `data.available`), `-32004` Rhino occupé, `-32005` ambigu (avec `data.candidates`), `-32006` échec,
`-32010` jeton refusé. Les messages et les données d'erreur sont transmis à Claude pour qu'il se corrige.

## Serveur MCP

```
src/
  index.ts, server.ts         démarrage stdio, instructions pour Claude, prompts
  config.ts                   variables d'environnement (réglages de l'extension)
  bridge/discovery.ts         lecture des fichiers d'instances, sonde /health
  bridge/client.ts            appels JSON-RPC, délais, redécouverte, erreurs typées
  tools/rhino.ts              15 outils rhino_*
  tools/grasshopper.ts        14 outils grasshopper_*
  tools/variants.ts           variant_* et job_*
  variants/workflow.ts        création d'une variante (appliquer → résoudre → mesurer → capturer → enregistrer)
  variants/store.ts           stockage sur disque, numérotation V01…
  variants/compare.ts         écarts, tableaux, classement multicritère, balayages
  util/jobs.ts                tâches de fond séquentielles (séries de variantes, et simulations du connecteur 2)
```

`grasshopper_set_parameter` mesure les métriques **avant et après** la modification et renvoie les écarts
(valeur, delta, %) : Claude peut expliquer l'effet d'un changement sans appel supplémentaire.

`variant_generate` s'exécute en tâche de fond : au-delà de `wait_seconds`, Claude reçoit un identifiant et
suit la progression avec `job_status`. Aucun appel d'outil ne reste ouvert plusieurs minutes.

## Tests et validation

| Niveau | Ce qui est vérifié | Où |
|---|---|---|
| Compilation | Tout le plug-in compile sans avertissement contre RhinoCommon et Grasshopper 8.0 | `dotnet build -c Release` |
| Transport (14 tests xUnit) | HTTP, gros corps, chunked, jeton, Origin, Host, erreurs, ports, fichier de découverte ; structure du `.rhp` (classe plug-in, GUID, commandes, DLL intégrée) ; **chaque méthode appelée par le serveur MCP existe dans le plug-in** | `rhino-bridge/tests` |
| Serveur MCP (19 tests) | Client MCP réel sur stdio ↔ serveur ↔ faux bridge au protocole identique : outils, schémas, découverte, création, +10 %, erreurs, variantes, comparaison, tâches ; rejoués **sur le serveur extrait du `.mcpb`** | `rhino-grasshopper-mcp/test` |
| Connecteur 2 (36 tests) | 24 unitaires (règles, géométrie, arbres, exploration, soleil, vent, Workbench, solveur externe) + 12 de bout en bout (site, règlement, arbres, exploration sous contraintes, variantes sous règlement + simulations soleil et vent, optimisation, ensoleillement lié à une variante, script externe, domaine de vent), rejoués sur le `.mcpb` | `fusion-rga-mcp/test` |
| Rhino réel | Le premier lancement dans Rhino 8 sous Windows (non disponible dans l'environnement de construction) | voir `release/INSTALLATION.md`, §3 |

## Construire depuis les sources

```bash
# Plug-in Rhino (.NET SDK 8 ; fonctionne aussi sous Linux/macOS grâce aux assemblys de référence)
cd connectors/rhino-bridge/src/RhinoMcpBridge && dotnet build -c Release
cd ../../tests/RhinoMcpBridge.Transport.Tests && dotnet test

# Serveurs MCP + extensions Claude Desktop (espace de travail npm : un seul node_modules)
cd connectors && npm ci && npm test && npm run pack
#   → connectors/release/RhinoGrasshopperConnector-<version>.mcpb et FusionRhinoGrasshopperAnsys-<version>.mcpb
```

Utilisation avec Claude Code (sans `.mcpb`) :

```bash
claude mcp add rhino-grasshopper -- node <chemin>/rhino-grasshopper-mcp/dist/index.js
```

## Connecteur 2 (Fusion)

```
fusion-rga-mcp/src/
  server.ts            serveur « fusion-rhino-grasshopper-ansys » : outils du connecteur 1 + modules ci-dessous
  site.ts              modèle de site (bâti, parcelles, voirie, espaces verts) depuis Rhino ou Grasshopper
  geometry/            géométrie 2D (aires, distances, inclusion), échantillonnage (quinconce, Poisson, alignements)
  rules/               langage d'expressions sûr, moteur de règles, jeux d'exemples et stockage des règlements
  explore/             plans d'expériences (grille, aléatoire, hypercube latin), Pareto, algorithme génétique
  trees/               catalogue d'essences, placement avec obstacles, maillages fermés d'arbres
  sim/                 cas de simulation (dossier + case.json), exécution de processus, soleil (NOAA),
                       confort au vent (Lawson), domaine de calcul ; adaptateurs solar, ansys_workbench, command
  tools/               urban_*, design_*, trees_*, sim_*
```

Les calculs lourds de géométrie 3D (contours, lancer de rayons) se font dans Rhino (méthodes `analysis.*`
du plug-in 1.1) ; les règles, l'échantillonnage, l'optimisation et le post-traitement sont en TypeScript,
testés sans Rhino. Les solveurs ANSYS tournent dans des processus séparés, suivis comme tâches de fond.

## Évolutions prévues

- Adaptateur Rhino.Compute pour calculer des séries de variantes sans interface.
- Paquet Yak pour le gestionnaire de paquets de Rhino.
