# Connecteurs Revit — Revit Dynamo Connector et Fusion Revit Dynamo ANSYS

Même concept que les connecteurs Rhino, transposé à **Revit 2022–2026** et **Dynamo** :

| Composant | Fichier | Rôle |
|---|---|---|
| Add-in Revit **RevitMcpBridge** 1.0.0 | `release/RevitMcpBridge-1.0.0.zip` | Passerelle locale sécurisée vers Revit et Dynamo (nécessaire aux deux connecteurs) |
| **Revit Dynamo Connector** 1.0.0 | `release/RevitDynamoConnector-1.0.0.mcpb` | 34 outils : maquette Revit, paramètres et paramètres globaux, création d'éléments, graphes Dynamo exécutés comme **Dynamo Player**, variantes |
| **Fusion Revit Dynamo ANSYS** 1.0.0 | `release/FusionRevitDynamoAnsys-1.0.0.mcpb` | 53 outils : tout le connecteur précédent + règles d'urbanisme, variantes sous contraintes urbaines **et** simulations physiques, arbres, gestionnaire de simulations (ensoleillement natif, ANSYS Workbench, PyAnsys) |

Installez l'add-in, puis **un seul** des deux connecteurs (le Fusion contient déjà tout le Revit Dynamo Connector).
Les connecteurs Rhino et Revit peuvent être installés côte à côte : leurs outils ont des noms différents
(`rhino_*`/`grasshopper_*` contre `revit_*`/`dynamo_*`) ; seuls les outils communs (`variant_*`, `design_*`,
`urban_*`, `trees_*`, `sim_*`) existeraient en double — n'activez alors qu'un Fusion à la fois.

---

## 1. Architecture

```
Claude Desktop ──stdio/MCP──► serveur MCP (Node, dans le .mcpb)
                                   │  JSON-RPC 2.0 sur HTTP, 127.0.0.1, jeton
                                   ▼
                       Revit ◄── add-in RevitMcpBridge ──► Dynamo for Revit (réflexion)
```

- **Add-in** (C#) : deux builds dans le zip — .NET Framework 4.8 pour Revit 2022/2023/2024 (API 2022) et
  .NET 8 pour Revit 2025/2026 (API 2025). Il démarre avec Revit, ajoute un panneau *MCP Bridge* à l'onglet
  *Compléments* (état, marche/arrêt) et publie un fichier de découverte
  `%LOCALAPPDATA%\RevitMcpBridge\instances\<pid>.json` (port + jeton).
- **File d'appels** : l'API Revit n'est utilisable que depuis le thread principal de Revit. Chaque requête
  passe par un `ExternalEvent` que Revit exécute quand il est disponible ; si une boîte de dialogue, un mode
  esquisse ou une commande bloque Revit, la requête expire au bout de 90 s avec un message clair.
- **Transactions** : chaque modification est **une seule transaction** nommée « Claude: … » (une étape
  d'annulation). Les avertissements Revit sont renvoyés à Claude au lieu d'ouvrir des boîtes de dialogue ;
  une erreur annule toute la modification.
- **Transport partagé** : le serveur HTTP/JSON-RPC, l'authentification et la découverte sont le **même code**
  que le plug-in Rhino (`connectors/shared/McpBridge.Transport`). Le protocole est identique : mêmes filtres,
  mêmes méthodes `analysis.*` — c'est ce qui permet de réutiliser tels quels les modules Fusion
  (règles, arbres, ensoleillement, ANSYS).
- **Moteur de variantes** : un *backend de conception* abstrait ce que l'on fait varier — les curseurs d'une
  définition Grasshopper (Rhino) ou les entrées d'un graphe Dynamo et les paramètres globaux (Revit).
  Variantes, exploration (grille, hypercube latin, Pareto) et optimisation génétique sont communes.

### Unités et coordonnées

Tout ce qui est échangé est en **SI** : mètres, m², m³, degrés. Revit stocke en pieds : l'add-in convertit
(facteurs exacts 0,3048). Les coordonnées sont les **coordonnées internes du projet**, en mètres.
`revit_get_document` donne la localisation du site : latitude/longitude, **angle du nord géographique**,
point de base du projet et point topographique.

### « Calques » dans Revit

Revit n'a pas de calques : les filtres et la détection du site utilisent des **pseudo-calques** :
`Category::Mass`, `Category::Property Lines`, `LineStyle::Voirie` (styles de lignes de modèle) et les chemins
`mcp.layer` écrits par le connecteur (`Vegetation::Trees::Ficus`, `Analysis::Sun hours::…`). Les données
attachées aux éléments par Claude (« user text ») sont stockées dans le **stockage extensible** de Revit
(schéma `ClaudeMcpUserText`), sans ajouter de paramètres au projet.

---

## 2. Dynamo comme Dynamo Player

`dynamo_run` exécute un graphe `.dyn` sur le projet ouvert **exactement comme Dynamo Player** :

1. Dynamo est démarré **sans fenêtre** (ou réutilisé s'il tourne déjà) et le graphe est ouvert en mode manuel ;
2. les entrées sont réglées par la commande `UpdateModelValue` — celle qu'utilise Dynamo Player ;
3. le graphe est évalué **une fois**, de façon synchrone, dans une seule opération Revit ;
4. l'espace de travail reste ouvert : à l'exécution suivante, les **liaisons d'éléments** sont conservées et les
   éléments créés par le graphe sont **mis à jour au lieu d'être dupliqués** (comportement de Dynamo Player) ;
5. les nœuds de sortie (« Is Output ») et les nœuds *Watch* sont relus et renvoyés à Claude (nombres, textes,
   listes, éléments Revit `{element_id}`, points), avec les erreurs et avertissements des nœuds.

Le fichier `.dyn` d'origine n'est **jamais modifié**. `dynamo_save_graph_copy` écrit une copie avec les valeurs
choisies (un « preset » à partager ou à rouvrir dans Dynamo).

### Préparer un graphe pour Claude

- Graphe enregistré avec **Dynamo 2.0 ou plus** (format JSON).
- Entrées : cochez **« Is Input »** (clic droit sur le nœud) — curseurs numériques/entiers, booléens, nombres,
  textes, chemins de fichiers. Donnez-leur des noms parlants (`Hauteur`, `Niveaux`, `Recul`) : ce sont les noms
  que Claude utilise. Réglez des bornes min/max réalistes : l'exploration de variantes les utilise.
- Sorties : cochez **« Is Output »** sur les nœuds qui renvoient les **éléments créés** (formes de volume,
  DirectShapes, sols…) et les **indicateurs** (surface de plancher, volume, nombre de logements). Les éléments
  en sortie deviennent les « bâtiments du projet » pour les règles d'urbanisme et les métriques `design.*`.
- Les packages Dynamo utilisés par le graphe doivent être installés (listés par `dynamo_get_graph`).
- Mettez vos graphes dans le **dossier des graphes** réglé dans l'extension (par exemple celui de Dynamo
  Player) ou dans `<dossier de travail>\graphs` : Claude les trouve par leur nom.

### Paramètres globaux

Les **paramètres globaux** du projet sont aussi des entrées : `revit_set_global_parameters` les modifie
(valeurs absolues ou relatives), et dans les variantes on les désigne par leur nom ou `global:<nom>`.
Avec `definition: "globals"`, une variante ne pilote que les paramètres globaux (sans graphe).

---

## 3. Variantes

`variant_create` / `variant_generate` avec `definition` = le graphe (nom ou chemin) :

1. applique les valeurs (entrées Dynamo et/ou paramètres globaux, modes `set`, `add`, `multiply`, `percent`) ;
2. exécute le graphe (éléments mis à jour en place) ;
3. mesure : sorties numériques du graphe (`Surface_plancher`, `Volumes.sum`…), quantités du modèle
   (`revit.mass_floor_area_m2`, `revit.floor_area_m2`, `revit.volume_m3`, `revit.Mass.count`…) et quantités des
   éléments produits par le graphe (`design.volume_m3`, `design.area_m2`, `design.height_m`) ;
4. capture une image (vue 3D temporaire, sans trace dans le projet) et exporte la géométrie en **OBJ** (mètres) ;
5. enregistre tout dans `<dossier de travail>\variants\<graphe>\V01_…` ; les valeurs d'origine sont restaurées à la fin d'une série.

`variant_compare` classe les variantes sur des objectifs, `variant_apply` remet une variante dans Revit,
`variant_keep` la marque ★.

---

## 4. Modules Fusion sur Revit

| Module | Particularités Revit |
|---|---|
| **Règles d'urbanisme** (`urban_*`) | Sources détectées : bâtiments = catégorie *Volume/Mass* (ou un style/chemin « bâti »), parcelles = *Lignes de propriété*, voirie = styles de lignes « Voirie », « Rue »… Sources explicites : `{"buildings": {"filter": {"categories": ["Mass"]}}, "plots": {"filter": {"categories": ["Property Lines"]}}, "roads": {"layer": "LineStyle::Voirie"}}`. Le nombre de niveaux vient des **niveaux de volume** (Mass Floors) ou d'un paramètre `Niveaux`/`floors`. |
| **Variantes sous contraintes** (`design_explore`, `design_optimize`) | `definition` = graphe Dynamo ; chaque design : entrées → exécution du graphe → règles → simulations → contraintes → objectifs. Les bâtiments contrôlés sont les éléments en sortie du graphe (`design_outputs` pour choisir les nœuds). |
| **Arbres** (`trees_*`) | Arbres créés comme éléments *Plantes* (DirectShape maillé, tronc + houppier), données d'espèce dans le stockage extensible ; pose sur Toposolid/Topographie. |
| **Ensoleillement** (`solar`) | Lancer de rayons natif dans l'add-in ; les vecteurs solaires (géographiques) sont **tournés vers le nord du projet** automatiquement. Les obstacles par défaut sont toute la maquette (bâtiments, toits, terrain, arbres), sans les cartes d'analyse. Carte colorée = DirectShape avec matériaux de couleur. |
| **ANSYS Workbench / PyAnsys** | La géométrie est exportée en **SAT** (solides ACIS, lus par SpaceClaim, DesignModeler et Discovery) — Revit ne sait pas exporter en STEP. STL/OBJ (maillages en mètres) disponibles aussi. Le reste est identique au connecteur Rhino (journal batch `RunWB2 -B -R`, paramètres d'entrée/sortie, confort au vent Lawson). |

Voir [FUSION.md](FUSION.md) pour le détail des règles, solveurs et de la préparation d'un projet Workbench.

---

## 5. Outils

**Revit** : `revit_get_document`, `revit_get_objects`, `revit_get_parameters`, `revit_set_parameters`,
`revit_get_global_parameters`, `revit_set_global_parameters`, `revit_get_types`, `revit_create_elements`
(niveaux, quadrillages, murs, sols, toits, familles, lignes de modèle, pièces, volumes, **terrain à partir de
points** — Toposolid en 2024+, Topographie avant), `revit_create_geometry` (DirectShapes : boîtes, extrusions,
maillages colorés…), `revit_transform_objects`, `revit_delete_objects`, `revit_set_object_data`,
`revit_select_objects`, `revit_capture_view`, `revit_export` (.stl .obj .sat .ifc .dwg .fbx), `revit_metrics`,
`revit_save_document`, `revit_open_document`, `revit_bridge_status`.

**Dynamo** : `dynamo_list_graphs`, `dynamo_get_graph`, `dynamo_run`, `dynamo_status`, `dynamo_save_graph_copy`.

**Variantes** : `variant_create`, `variant_generate`, `variant_list`, `variant_get`, `variant_compare`,
`variant_apply`, `variant_keep`, `variant_delete`, `job_status`, `job_cancel`.

**Fusion** (connecteur 2) : `urban_site_detect`, `urban_rules_list`, `urban_rules_save`, `urban_rules_check`,
`design_explore`, `design_optimize`, `trees_species`, `trees_generate`, `trees_stats`, `trees_remove`,
`sim_solvers`, `sim_create`, `sim_run`, `sim_status`, `sim_results`, `sim_list`, `sim_cancel`, `sim_sun_path`, `sim_wind_domain`.

---

## 6. Exemples de demandes

1. « **Que contient mon projet Revit ?** » → niveaux, catégories, quantités, paramètres globaux, localisation.
2. « **Quels graphes Dynamo ai-je ? Que demande Massing_Oran ?** » → entrées, bornes, sorties.
3. « **Lance Massing_Oran avec une hauteur de 18 m et 6 niveaux, et montre-moi le résultat.** »
4. « **Augmente le recul de 10 %.** » → paramètre global modifié, tous les éléments liés régénérés.
5. « **Crée trois variantes avec 4, 6 et 8 niveaux et compare la surface de plancher.** »
6. « **Vérifie le règlement exemple sur les volumes du projet.** » (connecteur Fusion)
7. « **Fais varier la hauteur de 12 à 30 m dans Massing_Oran, respecte le règlement, calcule l'ensoleillement au
   21 décembre et garde les variantes qui ont 2 h de soleil sur la moitié de l'espace public.** »
8. « **Plante des jacarandas tous les 8 m le long de la voirie.** »
9. « **Crée le terrain d'Oran à partir de ces points du MNT Copernicus.** » → `revit_create_elements` kind `terrain`.

---

## 7. Dépannage

| Problème | Solution |
|---|---|
| « Revit is not reachable » | Revit fermé, ou add-in non chargé : onglet *Compléments* → *MCP Bridge* → *Claude Bridge*. Si le panneau n'existe pas, relancez `Installer.cmd` et choisissez « Toujours charger » au démarrage. |
| « Revit did not pick up the request in time » | Une boîte de dialogue, un mode esquisse/édition ou une commande est en cours : terminez-la (Échap) et redemandez. |
| « Dynamo for Revit is not available » | Dynamo n'est pas installé pour cette version de Revit, ou n'a jamais été initialisé : ouvrez Dynamo une fois manuellement. |
| « Dynamo did not open the graph » | La fenêtre Dynamo est ouverte avec un graphe modifié non enregistré : enregistrez-le ou fermez-le. |
| Le graphe s'exécute mais aucune métrique `design.*` | Marquez « Is Output » le nœud qui renvoie les éléments créés. |
| Graphe Dynamo 1.x | Ouvrez-le et réenregistrez-le dans Dynamo 2 ou plus (format JSON). |
| Plusieurs Revit ouverts | « statut de la connexion Revit » → Claude liste les sessions et peut changer (`select_pid`), ou réglez *Session Revit préférée*. |

## 8. Limites

- Revit est **Windows uniquement** ; les connecteurs Revit sont déclarés `win32`.
- L'add-in n'a pas pu être exécuté dans Revit pendant le développement (pas de Revit sur le serveur de
  build) : il est **compilé contre les API Revit 2022 et 2025**, ses parties indépendantes de Revit (lancer de
  rayons, polygones, protocole) sont testées unitairement, et les deux serveurs MCP sont testés de bout en bout
  contre une passerelle simulée qui reproduit le protocole. Le pilotage de Dynamo s'appuie sur l'API de
  `DynamoRevitDS` (vérifiée sur Dynamo 2.19 et 3.0) par réflexion. Signalez tout écart observé dans Revit.
- Les éléments ne peuvent pas être mis à l'échelle (`scale`) dans Revit : modifiez leurs paramètres ou les
  entrées du graphe.
- STEP n'est pas exportable depuis Revit : utilisez SAT (solides) ou STL.
- Les valeurs des règles d'urbanisme fournies sont des **exemples** : utilisez le règlement applicable (POS/PDAU).
