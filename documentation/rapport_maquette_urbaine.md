# Maquette urbaine détaillée d'Oran : voiries, trottoirs, bâti

Fichier livré : `Oran_maquette_urbaine_detaillee_Rhino8.3dm` (Rhino 8, mètres), avec le dossier
`textures/`. Il est construit à partir de `Oran_maquette_blanche_complete_Rhino8.3dm`.

Scripts (reproductibles, dans l'ordre) :

| Script | Rôle |
|---|---|
| `scripts/13b_fetch_overture.py` | Télécharge les données Overture Maps (bâtiments OSM, Google et Microsoft ; axes de voirie OSM) |
| `scripts/14_bati_georef_hauteurs.py` | Vérifie et corrige le géoréférencement, relit le relief Copernicus, corrige les formes et les hauteurs du bâti |
| `scripts/15_maquette_urbaine_detaillee.py` | Hiérarchie des voiries, trottoirs, bordures, îlots, passages piétons, marquages, mobilier, matériaux, calques, vues |
| `scripts/16_apercus_maquette_urbaine.py` | Aperçus PNG (perspective et plan) |

Les chiffres ci-dessous proviennent de `results/bati_georef_hauteurs_report.json` et de
`results/maquette_urbaine_report.json`.

---

## 1. Règle de base : la morphologie n'est pas modifiée

- Le tracé XY de chaque route, parcelle, corridor et bâtiment est celui du fichier d'origine.
- Les routes existantes ne sont ni redessinées ni fusionnées : chaque couche OSM d'origine
  devient un sous-calque de sa catégorie (exemple : `ROAD_PRIMARY::HIGHWAY_PRIMARY`).
- Les éléments nouveaux (trottoirs, bordures, îlots, marquages, mobilier) sont **ajoutés**
  dans leurs propres calques. Ils ne remplacent aucune géométrie existante.
- Exceptions demandées explicitement, toutes bornées et mesurées :
  - **altitudes** : relief Copernicus relu au bon endroit (§ 2) ;
  - **forme du bâti** : nettoyage géométrique des emprises (§ 4) ;
  - **hauteur du bâti** : hauteurs fondées sur des données réelles (§ 5).

## 2. Découverte : décalage de géoréférencement de 35 à 75 m

Les emprises du modèle ont été comparées aux emprises réelles de 132 121 bâtiments
(OpenStreetMap, Google Open Buildings, Microsoft ; WGS84) :

- **Méthode** : rasterisation à 0,5 m sur des tuiles de 1,2 km (tous les 1,5 km), puis
  corrélation croisée FFT des deux masques. Le pic de corrélation, affiné au sous-pixel par
  une parabole, donne le décalage local réel − modèle.
- **Ajustement** : moindres carrés pondérés et robustes sur 136 tuiles, affine par axe :
  - dx = 3,58 − 0,0000984·x − 0,000625·y (m), résidu RMS 1,64 m (132 tuiles) ;
  - dy = −32,69 − 0,000135·x + 0,004518·y (m), résidu RMS 2,27 m (133 tuiles).
- **Ampleur** : sur les bâtiments, le décalage médian est de **39 m** (de 1,5 à 89 m).
  Le modèle était donc posé sur un relief lu 40 m trop au sud en moyenne, plus encore au sud
  de la ville. Le terme 0,0045·y traduit une échelle nord-sud du modèle trop grande de
  0,45 %. Elle est **conservée** (les dimensions ne sont pas modifiées) et n'est corrigée
  que dans le lien modèle → Terre.

Validations indépendantes :

| Contrôle | Sans correction | Avec correction |
|---|---|---|
| Axes routiers réels (3 740 km) situés sur une chaussée de la maquette | 14,7 % | 53,9 % (79 % dans le centre) |
| Signal bâti visible dans le DSM Copernicus (m de DSM par unité de couverture) | 0,83 | 2,48 |

Conséquence : chaque nœud de la grille de terrain de 25 m relit Copernicus GLO-30 à sa
position corrigée, avec exactement l'échantillonnage du script 05 (contrôle : l'ancien
terrain est reproduit à 0,0 m près). La variation d'altitude est de ±6 m pour 90 % des nœuds
et de ±14 m pour 98 % ; elle atteint 56 m sur les falaises. Toutes les couches drapées sont
déplacées verticalement de dT, calculé sur la **même triangulation** : elles restent drapées
exactement (route = terrain + 1,0 m). Le point d'ancrage terrestre devient
lon −0,6349604 / lat 35,6987054.

## 3. Copernicus et la hauteur des bâtiments : ce qui est mesurable

La couverture exacte de chaque pixel DSM par les emprises (intersection polygone/pixel) a été
comparée au DSM moins le sol local, en zone plane (301 173 pixels) :

| Couverture bâtie du pixel | DSM − sol (médiane) |
|---|---|
| 0–5 % | 0,0 m |
| 5–30 % | 0,3 m |
| 30–60 % | 0,7 m |
| 60–100 % | 1,4 m |

Le GLO-30 (pixels de 30 m) ne conserve que **2,5 m de signal pour un pixel entièrement
bâti**, alors que les immeubles d'Oran font 12 à 20 m. Les tours de 104 à 111 m sont absentes
du DSM. Une inversion par moindres carrés (DSM = sol + Σ couverture × hauteur) a été testée :
elle ne retrouve pas les hauteurs connues. Copernicus ne permet donc **pas** de mesurer la
hauteur des bâtiments.

Il sert en revanche au **calage altimétrique exact** de chaque bâtiment :
- toiture = sol médian de l'emprise + hauteur ;
- base = point le plus bas du sol sous l'emprise (fondation : rien ne flotte dans les pentes).

## 4. Forme des bâtiments (235 760 emprises)

Corrections bornées et mesurées, sur chaque contour :

| Opération | Règle | Résultat |
|---|---|---|
| Pics dégénérés | sommet où le contour revient sur lui-même (> 170°) | 871 supprimés |
| Sommets inutiles | angle < 1° et écart à la corde < 3 cm | 48 038 supprimés |
| Orthogonalisation | angles à 0,5–6° de l'angle droit : moindres carrés, arêtes ∥ ou ⊥ à l'axe dominant θ = arg(Σ L·e^{4iφ})/4, sommets partagés avec un voisin bloqués | 5 246 contours, déplacement max **0,30 m** |
| Garde-fous | rejet si déplacement > 0,30 m, variation d'aire > 1 % ou contour invalide | 44 rejets |

Au total, **27 146 bâtiments** sont améliorés. Variation d'aire maximale : 0,99 %. Chaque
bâtiment est reconstruit en prisme fermé (triangulation de Delaunay contrainte, murs en
quadrangles, 0 arête ouverte). Le volume est contrôlé à 10⁻⁷ près (volume = aire × hauteur).

## 5. Hauteur des bâtiments (étages de 3,0 m)

Constat : la couche `EXTRA_BUILD_BY_TURBOCG_COM` (231 656 bâtiments, 98 %) avait des hauteurs
**aléatoires**, uniformément réparties entre 7 et 22 m. Ses emprises sont générées : elles ne
recouvrent pas les bâtiments réels (IoU médian 0,03 après recalage).

Règles appliquées, par ordre de priorité :

| Source | Bâtiments |
|---|---|
| Calques `HEIGHT_xx` (hauteurs mesurées) : inchangés | 22 |
| Bâtiment couvert à ≥ 50 % par un bâtiment OSM avec hauteur ou nombre d'étages (étages × 3,0 m) | 6 166 |
| Médiane des étages OSM réels dans un rayon de 150 m (au moins 5 bâtiments) | 11 340 |
| Sinon : hauteur d'origine arrondie à l'étage | 218 232 |

Fiabilité de l'imputation (validation croisée « leave-one-out » sur les 2 965 bâtiments OSM
renseignés) : erreur moyenne de **0,64 étage**, 85,7 % à ±1 étage près. Une médiane globale
ferait 1,21 étage d'erreur. Toutes les hauteurs sont des multiples de 3 m (5 % = 6 m,
médiane = 15 m, 95 % = 21 m).

**Variante** (calque masqué `BUILDINGS_EMPRISES_REELLES_OSM_GOOGLE_MICROSOFT`) : les
132 121 emprises **réelles** (61 713 OSM, 44 259 Google, 26 149 Microsoft), recalées dans le
repère du modèle, sur le même relief et avec les mêmes règles de hauteur. On peut l'afficher à
la place du bâti d'origine pour une présentation plus fidèle à la réalité.

## 6. Voiries : hiérarchie et matériaux

| Calque | Couches OSM | Couleur | Matériau |
|---|---|---|---|
| `ROAD_PRIMARY` | motorway, trunk, primary (+ links) | noir anthracite (38,38,40) | asphalte anthracite |
| `ROAD_SECONDARY` | secondary, tertiary (+ links) | gris foncé (70,71,74) | asphalte gris foncé |
| `ROAD_LOCAL` | residential, unclassified, living street, road | gris moyen (110,111,114) | asphalte gris moyen |
| `ROAD_MINOR` | service, track, construction, accès | gris clair (156,157,159) | enrobé gris clair |
| `PEDESTRIAN_ZONE` | footway, pedestrian, path, steps | pierre claire (198,192,180) | pavés de pierre |

Tous les matériaux sont mats (réflexion 0, brillance 0). Les textures (`textures/*.png`,
répétées tous les 4 m en projection planaire) donnent une légère variation : grain d'asphalte,
dalles de béton de 1 × 0,5 m, pavés de 0,5 m, granit, herbe.

Les couches `ROUTE_*` et `RESTRICTION_*` sont des relations OSM qui doublent les routes et
provoquent des scintillements. Elles sont regroupées dans `OSM_RELATIONS`, **masqué**, sans
suppression.

## 7. Trottoirs, bordures, îlots

- **Trottoirs** : uniquement là où la géométrie prouve l'existence d'un espace piéton, c'est-à-dire
  entre le bord de chaussée et une façade située à 30 m au plus. Largeur nominale : 4,0 m
  (primaire), 3,0 m (secondaire), 2,0 m (locale), limitée par les façades, les autres
  chaussées et les voies piétonnes. Les bandes de moins de 1,2 m sont supprimées (ouverture
  morphologique). Pas de trottoir sur les autoroutes, les bretelles, les voies de service ni en
  rase campagne. Surface obtenue : 0,54 km² (primaire), 1,14 km² (secondaire), 4,26 km²
  (locale).
- **Hauteurs** (référence = dessus de chaussée) : trottoir +0,15 m, bordure +0,17 m, bordure
  de 0,20 m de large côté chaussée. Ce sont des dalles fermées drapées sur le terrain
  (contrôle d'écart au cm).
- **Îlots** (trous du réseau de chaussées sans bâtiment, de 15 à 6 000 m²) : 273 centres de
  ronds-points (circularité > 0,7), 683 terre-pleins centraux (largeur moyenne < 6 m),
  613 îlots directionnels. Ils sont surélevés, ceinturés d'une bordure, et végétalisés
  lorsqu'ils font plus de 2 m de large.

## 8. Intersections, passages piétons, marquages

- Le graphe des axes de voirie réels (OSM via Overture) est recalé dans le repère du modèle.
  Les carrefours sont les nœuds de degré ≥ 3.
- **Passages piétons** : sur chaque branche d'un carrefour comprenant une voie primaire ou
  secondaire, placés juste après le rayon du carrefour (demi-largeur de chaussée mesurée
  + 1,5 m). Ils ne sont créés que si un trottoir existe aux **deux** extrémités de la ligne de
  traversée. S'y ajoutent les passages cartographiés dans OSM. Bandes de 0,50 m, espacement de
  0,50 m, longueur de 3,0 m, à 0,5 m de chaque bordure.
- **Marquage axial** : tirets T1 de 3 m tous les 10 m (0,15 m de large), sur les chaussées
  primaires et secondaires d'au moins 6,5 m. Ils sont placés au **centre mesuré** de la
  chaussée (milieu de la corde perpendiculaire) et interrompus dans les carrefours.
- **Mobilier aux traversées** : 2 potelets de chaque côté et un panneau de passage piéton.

## 9. Mobilier urbain (blocs Rhino, sobre)

Les blocs sont `ARBRE_ALIGNEMENT`, `ARBRE_PARC`, `LAMPADAIRE`, `BANC`, `POTELET`,
`PANNEAU_PASSAGE_PIETON` et `ABRIBUS`. Ce sont des instances : le fichier reste léger et une
modification du bloc s'applique partout.

- Lampadaires de 8 m tous les 24 m sur les trottoirs primaires et secondaires (à 0,6 m de la
  bordure).
- Arbres d'alignement tous les 8 m sur les trottoirs d'au moins 3 m de large.
- Arbres de parc en semis régulier perturbé, espacés de 12 m (à 2,5 m des voies et du bâti).
- Bancs le long des allées des parcs.
- Abribus sur les quais de transport public (couche `PUBLIC_TRA`).

## 10. Organisation Rhino

```
ROAD_PRIMARY / ROAD_SECONDARY / ROAD_LOCAL / ROAD_MINOR / PEDESTRIAN_ZONE  (sous-calques = couches OSM d'origine)
SIDEWALK::SIDEWALK_PRIMARY|SECONDARY|LOCAL     CURB::CURB_PRIMARY|SECONDARY|LOCAL|ILOTS
CROSSWALK     ROAD_MARKING::AXE_DISCONTINU_T1     ROAD_ISLAND::ROND_POINT|TERRE_PLEIN_CENTRAL|ILOT
GREEN_SPACE::(parcs, jardins, bois, prairies, vergers, îlots végétalisés…)
URBAN_FURNITURE::LAMPADAIRES|ARBRES_ALIGNEMENT|ARBRES_PARCS|BANCS|POTELETS|PANNEAUX|ABRIBUS
BUILDINGS::(couches d'origine)     BUILDINGS_EMPRISES_REELLES_OSM_GOOGLE_MICROSOFT (masqué)
CONTEXTE::EAU|FERROVIAIRE_TRAM|AEROPORT|EQUIPEMENTS|OCCUPATION_DU_SOL|OUVRAGES_RESEAUX|LIEUX_NOMMES
OSM_RELATIONS (masqué)     TERRAIN_ET_SOCLE     COURBES_NIVEAU_10m / 50m (recalculées sur le relief recalé)
```

Les calques vides du fichier d'origine sont supprimés. Vues nommées : `01 Vue generale
(plan)`, `02 Vue aerienne`, `03 Perspective 3D centre-ville`, `04 Zoom rue`,
`05 Zoom carrefour et trottoirs`.

Conseil d'affichage : mode *Rendu* ou *Arctic* pour les matériaux et les textures, *Ombré* pour
la lecture par couleurs de calque. Le dossier `textures/` doit rester à côté du `.3dm`.

## 11. Limites

- Le bâti `EXTRA_BUILD` reste un bâti **généré** (emprises non réelles). Sa forme a été
  nettoyée et ses hauteurs fondées sur les étages OSM réels là où ils existent (17 506
  bâtiments). Ailleurs, la hauteur d'origine est seulement arrondie à l'étage. Les emprises
  réelles sont fournies en variante.
- Les trottoirs, les passages piétons générés et le mobilier sont des **implantations
  types**, déduites de règles géométriques. Ce n'est pas un relevé. Seuls 45 passages piétons
  et 12 trottoirs sont cartographiés dans OSM pour Oran.
- Le recalage est affine (résidu de 1,6 à 2,3 m). Les écarts locaux plus fins entre la
  maquette TurboCG et OSM subsistent.
- Copernicus GLO-30 a une résolution de 30 m et une précision verticale d'environ 2 à 4 m.
  Le relief urbain fin (talus, escaliers) n'est pas représenté.

## 12. Impression 3D (même chaîne que la livraison précédente)

`scripts/17_source_impression_urbaine.py` puis `scripts/06_print_export.py --cache results/cache/urbaine_print_parts.npz --name Oran_maquette_urbaine`.
Le solide d'impression comprend le terrain recalé, le socle et les 235 760 bâtiments reconstruits, fusionnés par
manifold3d en **un seul solide**. Les voiries, trottoirs et mobilier sont trop fins à cette échelle
(< 0,01 mm) : ils restent dans le fichier Rhino et ne sont pas dans le solide d'impression, comme pour la livraison précédente.

| | HD | OPT |
|---|---|---|
| Triangles | 8 645 600 | 4 692 154 |
| Dimensions (mm) | 200 × 150,1 × 12,4 | 200 × 150,1 × 12,4 |
| Échelle / exagération Z | 1:145 375 / ×2 | idem |
| Étanche, arêtes ouvertes, non-manifold | oui, 0, 0 | oui, 0, 0 |
| Orientation cohérente, corps | oui, 1 | oui, 1 |
| Volume | 175,65 cm³ | 175,65 cm³ |
| STL relu depuis le disque | OK | OK |

Archives : `livraison/Oran_maquette_urbaine_impression3D_HD.7z.001–006` et `..._OPT.7z.001–004` (STL + OBJ).

## 13. Contrôle qualité du fichier Rhino (`scripts/18_controle_qualite_rhino.py`, relu depuis le disque)

- **0 maillage invalide** (validité Rhino), aucun calque vide, unités en mètres, point d'ancrage
  terrestre lon −0,6349604 / lat 35,6987054, 5 vues nommées, 24 matériaux, 9 textures présentes.
- **Bâtiments** (235 760) et variante d'emprises réelles (132 121), terrain et socle, passages piétons
  (92 438 bandes) et marquages (39 708 tirets) : 100 % de solides fermés, 0 arête ouverte,
  0 arête non-manifold, normales vers l'extérieur.
- **Trottoirs, bordures et îlots** (≈ 100 000 dalles) : fermés, sauf moins de 0,1 % de pièces avec des
  micro-défauts (≈ 230 arêtes ouvertes en tout) dus à la soudure au millimètre. Le stockage des sommets
  en simple précision dans Rhino ne distingue plus deux points à moins d'1 mm à 10–15 km de l'origine.
- **Couches d'origine** conservées telles quelles (routes TurboCG, nappes d'occupation du sol, relations
  OSM) : valides pour Rhino, normales réorientées (2,3 M faces retournées vers le haut ou l'extérieur).
  Certaines nappes planes sont ouvertes par nature, et des rubans de routes se superposent dans le modèle
  d'origine. Rien de cela n'entre dans le solide d'impression.
- 107 402 blocs de mobilier (7 définitions) : 25 941 lampadaires, 40 943 arbres d'alignement, 160 arbres
  de parc, 32 261 potelets, 8 089 panneaux, 8 abribus.
