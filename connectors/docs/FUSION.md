# Connecteur 2 — Fusion Rhino Grasshopper ANSYS

Le connecteur Fusion **contient tous les outils du connecteur 1** (Rhino, Grasshopper, variantes) et ajoute
cinq modules. Installez l'un **ou** l'autre dans Claude Desktop : le Fusion suffit seul.

```
Claude
  ↓
Fusion MCP (58 outils)
  ├── Rhino ─────────────────┐
  ├── Grasshopper ───────────┤  outils du connecteur 1
  ├── Variantes ─────────────┘
  ├── Règles d'urbanisme       urban_*
  ├── Générateur de variantes  design_explore, design_optimize
  ├── Générateur d'arbres      trees_*
  ├── Gestionnaire de simulations  sim_*
  └── ANSYS (Workbench, scripts PyAnsys, journaux Fluent/MAPDL)
```

Prérequis : plug-in **RhinoMcpBridge 1.2** (fourni dans `release/`) ; ANSYS seulement pour les simulations ANSYS.

---

## 1. Règles d'urbanisme

| Outil | Rôle |
|---|---|
| `urban_site_detect` | Trouve les calques bâti / parcelles / voirie / espaces verts d'après leurs noms, et résume le site |
| `urban_rules_list` | Jeux de règles disponibles (exemples intégrés + les vôtres) |
| `urban_rules_save` | Enregistre un règlement (POS, PDAU, cahier des charges) dans `RhinoMCP\rules\` |
| `urban_rules_check` | Vérifie le bâti (calque Rhino **ou** sorties Grasshopper) ; tableau ✅/❌, bâtiments fautifs en rouge sur une capture |

Types de règles :

| Type | Contrôle | Paramètres |
|---|---|---|
| `max_height`, `min_height` | Hauteur de chaque bâtiment | `value` (m) |
| `max_floors` | Gabarit R+n (user text `floors`/`niveaux`, sinon hauteur ÷ hauteur d'étage) | `value` |
| `max_footprint` | Emprise d'un bâtiment | `value` (m²) |
| `max_coverage` | **CES** : emprise ÷ surface de parcelle | `value` |
| `max_far` | **COS** : surface de plancher ÷ surface de parcelle | `value` |
| `min_green_ratio` | Part d'espaces verts dans la parcelle | `value` |
| `min_boundary_setback` | Recul sur limites séparatives | `ratio` (× H), `min`, `allow_contiguous` |
| `min_street_setback` | Recul par rapport aux voies | `value` |
| `min_building_spacing` | Prospect entre bâtiments | `ratio` (× H du plus haut), `min`, `allow_contiguous` |
| `metric_max`, `metric_min`, `metric_range` | Toute métrique Grasshopper ou du site | `metric`, `value` / `min`, `max` |
| `expression` | Règle libre | `expression`, ex. `far <= 3 and coverage <= 0.6` |

Variables du site utilisables dans les règles : `building_count`, `footprint_area`, `gfa`, `height_max`,
`height_mean`, `floors_max`, `site_area`, `coverage`, `far`, `green_area`, `green_ratio`, `road_length`,
plus toutes les métriques Grasshopper (`OUT_GFA`, `Buildings.volume`…).

> ⚠️ Les jeux `exemple_zone_urbaine` et `exemple_objectifs_variantes` sont des **exemples pédagogiques**.
> Donnez à Claude les valeurs du règlement applicable (« enregistre le POS de la zone UA : hauteur 21 m,
> R+6, CES 0,7, COS 3,2… ») : il les enregistre avec `urban_rules_save`.

## 2. Générateur de variantes — contraintes urbaines + simulations physiques

Pour **chaque variante** générée, le connecteur enchaîne automatiquement :

```
paramètres Grasshopper → solution → métriques
   → règles d'urbanisme (rules)               ✗ → variante non conforme, non simulée
   → simulations physiques (simulations)       soleil · projet ANSYS Workbench · script PyAnsys / journal
   → seuils sur les résultats (simulation_constraints)   ✗ → variante non conforme
   → objectifs (Grasshopper, règles et simulations) → classement, front de Pareto
   → meilleures variantes enregistrées avec image, géométrie et résultats de simulation
```

Exemple de demande :

> « Fais varier Building_Height de 12 à 30 m et Floors de 4 à 10. Respecte le POS de la zone UA. Pour chaque
> variante, calcule l'ensoleillement au 21 décembre avec la ville d'Oran autour, et le vent avec mon projet
> ANSYS `C:\Etudes\vent.wbpj` (paramètre P1 = hauteur). Garde seulement les variantes avec au moins 2 h de
> soleil sur la moitié de l'espace public et moins de 15 % de zone inconfortable au vent ; maximise la surface
> de plancher. »

Claude appelle alors `design_explore` avec :

```json
{
  "space": { "Building_Height": { "min": 12, "max": 30, "steps": 4 }, "Floors": { "min": 4, "max": 10, "steps": 3 } },
  "design_outputs": ["OUT_Buildings"],
  "rules": { "rule_set": "POS_UA" },
  "simulations": [
    { "solver": "solar", "settings": { "dates": ["2026-12-21"], "context": { "layer": "Oran::Bâti" } } },
    { "solver": "ansys_workbench", "name": "wind",
      "settings": { "project": "C:\\Etudes\\vent.wbpj", "parameters": { "P1": "{Building_Height}" },
                    "velocity_csv": "pedestrian.csv", "reference_speed": 6 } }
  ],
  "simulation_constraints": [
    { "metric": "solar.area_pct_above_threshold", "min": 50 },
    { "metric": "wind.wind_pct_uncomfortable", "max": 15 }
  ],
  "objectives": { "OUT_GFA": "max" },
  "save": "pareto"
}
```

- Les bâtiments de la variante (sorties Grasshopper `design_outputs`) sont transmis automatiquement aux
  simulations : obstacles pour le soleil, géométrie exportée (STEP) pour ANSYS.
- `context` ajoute la ville environnante comme obstacle au soleil.
- Les variantes qui enfreignent déjà le règlement ne sont pas simulées (gain de temps ; `simulate_infeasible`
  pour les simuler quand même).
- Chaque simulation est un cas traçable dans `RhinoMCP\simulations\` ; les métriques sont préfixées par le
  nom de la simulation (`solar.sun_hours_mean`, `wind.wind_pct_uncomfortable`, `wind.P5`…).
- `design_optimize` accepte les mêmes options : l'algorithme génétique cherche l'optimum sous contraintes
  urbaines **et** physiques.
- Prompt prêt à l'emploi : `variantes_contraintes_simulations`.

| Outil | Méthode |
|---|---|
| `design_explore` | Plan d'expériences : grille complète, aléatoire ou **hypercube latin** sur les plages des sliders ; règles = faisabilité ; **front de Pareto** ; meilleur(s) enregistrés comme variantes avec image ; CSV/JSON dans `RhinoMCP\explorations\` |
| `design_optimize` | **Algorithme génétique** (type Galapagos) : sélection par tournoi, croisement, mutation, élitisme ; les solutions non conformes sont classées après les conformes ; l'optimum est appliqué et enregistré |

Exemple : « Explore la hauteur (12 à 30 m) et le nombre de blocs ; respecte le POS ; maximise la surface de
plancher et minimise l'ombre portée. »

## 3. Générateur d'arbres

| Outil | Rôle |
|---|---|
| `trees_species` | Palette méditerranéenne (ficus, olivier, pin d'Alep, palmiers, jacaranda, mélia, platane, tipuana, bigaradier, caroubier, cyprès…) avec hauteur, houppier, tronc, forme, densité foliaire |
| `trees_generate` | Alignements le long des rues (pas, décalage, un ou deux côtés), plantations dans des zones (quinconce, grille, aléatoire naturel), ou points donnés ; évite façades et chaussées ; posé sur le relief ; mélange d'essences pondéré ; variation de taille ; un maillage fermé par arbre |
| `trees_stats` | Nombre, essences, **surface de canopée** (et % d'une zone) |
| `trees_remove` | Supprime une plantation (`set_name`) ou toutes |

Chaque arbre porte du user text (`species`, `height`, `crown_diameter`, `lad`, `cd`) réutilisable par une
simulation de vent (houppiers en milieu poreux). Les dimensions du catalogue sont **indicatives** ; ajoutez
vos essences dans `RhinoMCP\trees\species.json`.

## 4. Gestionnaire de simulations

| Outil | Rôle |
|---|---|
| `sim_solvers` | Solveurs disponibles et détection d'ANSYS sur la machine |
| `sim_create` | Prépare un cas (export géométrie, journaux, paramètres) **sans le lancer**, pour relecture |
| `sim_run` | Lance (tâche de fond) ; résultats → métriques ; liés à une variante, ils s'ajoutent à ses métriques (`solar.sun_hours_mean`, `ansys_workbench.P5`…) et `variant_compare` peut classer les variantes dessus |
| `sim_status`, `sim_results`, `sim_list`, `sim_cancel` | Suivi |
| `sim_sun_path` | Positions du soleil (Oran par défaut, UTC+1) |
| `sim_wind_domain` | Dimensionne le domaine de calcul au vent (5 H amont et latéral, 15 H aval, 6 H de haut, blocage < 3 %) et le dessine dans Rhino |

### Solveurs

**`solar` — ensoleillement natif (sans ANSYS).** Positions du soleil (algorithme NOAA) pour les jours choisis,
grille de points sur l'espace public (posée sur le relief si fourni), lancer de rayons contre les bâtiments
dans Rhino. Métriques : heures d'ensoleillement moyenne/min/max, % de surface au-dessus d'un seuil, % toujours
à l'ombre. Carte colorée créée dans `Analysis::Sun hours` et capture.

**`ansys_workbench` — projet Workbench paramétré (toutes physiques).** Méthode robuste pour ANSYS : vous
préparez **une fois** un projet Workbench validé ; le connecteur, pour chaque variante :
1. exporte la géométrie de la variante (STEP par défaut) au chemin attendu par le projet ;
2. écrit un journal `run.wbjn` (Open → SetFile → paramètres d'entrée → Update → lecture de tous les paramètres) ;
3. lance `RunWB2.exe -B -R run.wbjn` (Workbench en mode batch, détecté via `AWP_ROOTxxx`) ;
4. récupère les paramètres de sortie comme métriques, et le confort au vent si le projet exporte un CSV.

**`command` — scripts et journaux.** Pour un script PyFluent / PyMAPDL écrit par votre ingénieur, un journal
Fluent (`fluent 3ddp -g -i run.jou`), MAPDL en batch ou tout autre outil : la commande reçoit le dossier du
cas, la géométrie exportée et `parameters.json`, et écrit `results.json` (`{"metric": valeur}`).

### Préparer un projet Workbench « confort au vent » (une fois)

1. `sim_wind_domain` sur vos bâtiments et la direction de vent étudiée → dimensions du domaine.
2. Dans Workbench : *Geometry* (DesignModeler ou SpaceClaim) qui **importe** `geometry.step` + un
   *Enclosure* aux dimensions données ; *Named Selections* stables sur l'enclosure : `inlet`, `outlet`,
   `sides`, `top`, `ground`, et `buildings`.
3. *Fluent* : k-ω SST, profil d'entrée logarithmique (expression `Uref*ln((z+z0)/z0)/ln((zref+z0)/z0)`),
   sortie en pression, parois rugueuses au sol.
4. Paramètres de sortie (*Output Parameters*) : vitesse max/moyenne au niveau piéton (plan z = 1,5 m), etc.
   Optionnel : un export `pedestrian.csv` (x, y, z, vitesse) des valeurs sur le plan piéton → le connecteur
   calcule les classes de confort de Lawson (`velocity_csv`).
5. Testez une mise à jour manuelle, puis donnez à Claude le chemin du `.wbpj` :
   « Lance la simulation de vent ANSYS sur les variantes V02 et V05, projet `C:\Etudes\vent.wbpj`, paramètre P1 = hauteur. »

> Les classes de Lawson calculées pour **un** vent simulé décrivent ce vent seulement ; le confort annuel
> demande les statistiques de vent du site (toutes directions et fréquences).

## 5. Exemples de demandes

- « Détecte les calques du site et vérifie le règlement exemple. »
- « Enregistre ce règlement : hauteur 24 m, R+7, CES 0,6, COS 3, recul H/2 minimum 4 m, prospect H. »
- « Explore Building_Height de 12 à 30 m et Floors de 4 à 10 avec ce règlement, maximise `OUT_GFA`. »
- « Optimise la hauteur et l'emprise pour maximiser la surface de plancher sous contrainte du POS. »
- « Plante des ficus tous les 8 m des deux côtés du boulevard, à 4 m de l'axe, en évitant les immeubles. »
- « Calcule l'ensoleillement au 21 décembre sur l'espace public pour V02 et V05, puis compare-les. »
- « Dimensionne le domaine pour un vent d'ouest et dessine-le. »

## Limites

- Le plug-in et les outils ont été validés par compilation et par tests automatisés (faux Rhino au protocole
  identique) ; **la première exécution dans Rhino 8 sous Windows et avec ANSYS reste à faire chez vous**.
- Les empreintes viennent du contour des maillages projetés : les porte-à-faux et toitures débordantes
  augmentent l'emprise.
- Distances et surfaces dans les unités du modèle Rhino (mètres attendus pour les règles et les simulations).
