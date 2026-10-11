# Oran — carte thermique du 10/07/2024 intégrée à la maquette blanche Rhino 8

*Rapport détaillé de traitement, d'analyse et de contrôle — généré automatiquement à partir des résultats.*

## 1. Objet

Projeter **uniquement** la carte de température de surface du **10 juillet 2024** (panneau inférieur central de la figure fournie) sur la maquette numérique blanche d'Oran (Rhino 8), sans reconstruire la ville, et livrer un paquet ZIP autonome (modèle Rhino, raster géoréférencé, SIG, statistiques, scripts, métadonnées, validation). Les sept autres dates de la figure (1984, 2000, 2004, 2010, 2015, 2020, 2023) n'ont pas été traitées.

## 2. Données d'entrée

| Élément | Valeur |
|---|---|
| Document | `Oran_LST_multidates_source.jpg` — 2000 × 1724 px, SHA-256 `7568f218b3fc367a…` |
| Origine | Figure issue de « Spatiotemporal Impact of Urbanization on Urban Heat Island Using Landsat Imagery in Oran, Algeria: 1984–2024 » (2025, https://www.researchgate.net/publication/390254230). Grandeur cartographiée : température de surface terrestre (LST) en °C, dérivée de Landsat (algorithme split-window pour OLI-TIRS). Pour le 10/07/2024 le capteur est Landsat 8/9 OLI-TIRS (infrarouge thermique natif 100 m, livré rééchantillonné à 30 m). Raster original non publié/accessible ici. |
| Grandeur | Temperature de surface terrestre (LST) en °C — **température de surface du sol, pas température de l'air** |
| Échelle de couleurs | 20 → 45 °C, bleu (froid) → vert → jaune → orange → rouge (chaud) |
| Panneau extrait | pixels [676, 1168, 1322, 1678] de l'image (x0, y0, x1, y1) |
| Maquette | `Oran_maquette_blanche_complete_Rhino8.3dm` — 2510 objets, mètres, SHA-256 `d3d456df560e528d…` |
| Repère de la maquette | Transverse Mercator local (lat₀ 35,699°, lon₀ −0,635°, WGS84), mètres vrais, point d'ancrage Rhino 35,699 N / 0,635 O |

Le raster Landsat original de l'étude n'étant pas accessible, les valeurs sont **reconstruites depuis l'image** (voir § 7, limites).

## 3. Extraction du panneau 2024 et conversion couleur → température

1. Découpe du seul panneau « 10/07/2024 » ; le titre, la flèche nord, l'échelle et les autres dates sont exclus.
2. **Étalonnage sur les graduations** de la légende : graduations 25, 30, 35, 40, 45 mesurées aux colonnes 1490, 1589, 1688.5, 1788, 1887 px (99,25 px pour 5 °C, ajustement linéaire exact). La barre colorée dépasse la graduation 45 : elle couvre 20,4 → 45,9 °C. 506 couleurs de référence, pas 0.050 °C.
3. Chaque pixel reçoit la valeur de la couleur de légende la plus proche (distance CIE-Lab ΔE). Écart médian couleur image ↔ légende : **ΔE 1,5** (imperceptible). Les pixels à ΔE > 12.0 (contour noir, texte, port dessiné) sont NoData, jamais interpolés.
4. Zone d'étude = plus grande région colorée, trous bouchés ; la bordure de 1 px mélangée au trait de contour est retirée (biais mesuré vers le froid).
5. Résultat : **123884 pixels valides**, valeurs 23,8 → 45,9 °C.

![Panneau source et valeurs reconstruites](comparaison_panneau_2024.png)

## 4. Géoréférencement sur la maquette

Aucune coordonnée n'a été saisie à la main : le panneau est calé **automatiquement sur la géométrie de la maquette**.

* **Estimation initiale** : échelle graphique 0–15 km = 377 px → 39,8 m/px ; nord en haut ; centre du « Petit Lac » (Dhayet Morsli) sur le lac de la maquette.
* **Ajustement** : 718 pixels du trait de contour côtier appariés au trait de côte de la maquette (courbe 0,5 m du MNT Copernicus), + centre du lac ; moindres carrés robustes (Huber), ré-appariement itératif.
* **Transformation retenue** : similitude, échelle **39.68 m/px** (échelle graphique : 39.79 → écart 0.3 %), rotation **-1.84°**. Une transformation affine n'améliore pas le calage (côte RMSE 161,1 m contre 174,6 m) et dégrade le lac → rejetée.
* **Test UTM 30N** (rotation imposée −1,38°, convergence des méridiens) : côte médiane 58,7 m mais lac à 149,5 m → moins bon, non retenu ; l'écart entre les deux solutions (moy. 103,5 m, max 171,1 m) sert d'**indicateur d'incertitude**.

| Contrôle de position | Valeur |
|---|---|
| Côte : écart médian | **51,8 m** (≈ 1.3 pixel) |
| Côte : écart à 90 % | 188,9 m |
| Centre du lac | 45,5 m ; surface lac image 1.12 km² / maquette 1.28 km² |
| **Contrôle indépendant** : pistes de l'aéroport d'Es Sénia (non utilisées au calage) → pixels chauds linéaires | médiane 80,5 m |
| Taille d'un pixel de l'image au sol | 39,7 m |

![Contrôle du calage : côte (bleu), port et pistes (noir), eau (magenta) de la maquette sur le panneau](controle_calage_2024.png)

**Raster produit** : `Oran_Thermal_2024.tif`, maille **40 m** (≈ pixel de l'image, aucune résolution inventée), 658 × 527 cellules, 121927 valides, plus proche voisin (les valeurs de la légende sont conservées), NoData = −9999, repère de la maquette ; copie `Oran_Thermal_2024_UTM30N.tif` (EPSG:32630) pour les SIG.

## 5. Intégration dans la maquette Rhino

Fichier `Oran_Thermal_2024_3D.3dm` (394,5 Mo) = maquette blanche **inchangée** (2510 objets d'origine) + 574 objets thermiques, tous valides. Copie intacte `Oran_Original_Backup.3dm` (SHA-256 identique : oui).

| Calque (sous `THERMAL_ORAN_2024_07_10`) | Contenu |
|---|---|
| `NUAGE_POINTS_THERMIQUE::NUAGE_a-b` | **Nuage de couleurs** : 1 point par cellule de 40 m, à 3 m au-dessus du relief, valeur °C exacte attachée à chaque point ; un sous-calque par classe de **1 °C** → **lecture zone par zone** |
| `SURFACE_THERMIQUE_TERRAIN::SURF_a-b` | Carreaux de 40 m posés sur le relief (+1 m, au-dessus des rues drapées), mêmes classes |
| `TOITURES_THERMIQUES::TOIT_a-b` | 536405 triangles de toits (+0,3 m), rangés selon la classe de la cellule sous le centre de chaque face |
| `ZONES_CHAUDES` / `ZONES_FRAICHES` | 16 contours drapés + étiquettes (moyenne, extrême, surface) |
| `GRILLE_1KM` / `GRILLE_1KM_VALEURS` | 217 carrés de 1 km drapés + étiquette de la moyenne (détail min/max/médiane dans le nom de l'objet) |
| `LIMITE_ZONE_ETUDIEE`, `LEGENDE_THERMIQUE` | Contour de l'étude, barre de couleurs et graduations à l'est du modèle |

**Couleurs portées par les calques Rhino** : chaque classe de 1 °C a son propre calque dont la couleur est la couleur exacte de la légende au centre de la classe, et un **matériau de rendu** de la même couleur (23 matériaux dans le fichier). Tous les objets thermiques sont en « couleur du calque » et « matériau du calque » : le rendu est identique en filaire, ombré et rendu, et chaque classe s'allume ou s'éteint d'un clic (filtrage chirurgical d'une plage de température).

Répartition des cellules et couleur (RVB) de chaque calque :

| Classe (°C) | Cellules | Couleur du calque (R, V, B) |
|---|---|---|
| 23-24 | 43 | 106, 176, 176 |
| 24-25 | 390 | 126, 190, 172 |
| 25-26 | 251 | 146, 203, 167 |
| 26-27 | 350 | 166, 218, 164 |
| 27-28 | 1026 | 179, 224, 167 |
| 28-29 | 3784 | 189, 229, 169 |
| 29-30 | 3922 | 199, 233, 173 |
| 30-31 | 6169 | 209, 237, 178 |
| 31-32 | 6260 | 221, 241, 180 |
| 32-33 | 9053 | 235, 247, 185 |
| 33-34 | 5291 | 252, 254, 189 |
| 34-35 | 14328 | 255, 248, 185 |
| 35-36 | 20641 | 255, 238, 172 |
| 36-37 | 19544 | 255, 226, 158 |
| 37-38 | 11015 | 254, 215, 145 |
| 38-39 | 6071 | 253, 199, 125 |
| 39-40 | 1966 | 254, 188, 112 |
| 40-41 | 1854 | 252, 175, 97 |
| 41-42 | 2316 | 245, 143, 81 |
| 42-43 | 914 | 239, 112, 69 |
| 43-44 | 747 | 233, 85, 57 |
| 44-45 | 348 | 223, 55, 42 |
| 45-46 | 274 | 218, 34, 32 |

Couverture : 95.6 % de la zone d'étude tombe sur le relief de la maquette ; le reste (extrémité est, au-delà de x = 13 650 m) dépasse l'emprise de la maquette et n'est présent que dans le raster et le GPKG.

![Maquette : relief + couche thermique](apercu_3d_thermique.png)

![Centre-ville : toitures colorées](apercu_3d_thermique_centre.png)

## 6. Analyse thermique du 10/07/2024 (valeurs approximatives, issues de l'image)

| Indicateur | Valeur |
|---|---|
| surface etudiee | 195,1 km2 |
| minimum | 23,8 °C |
| maximum | 45,9 °C |
| moyenne | 34,9 °C |
| ecart type | 3,2 °C |
| mediane | 35,3 °C |
| P5 | 29,1 °C |
| P10 | 30,3 °C |
| P25 | 32,8 °C |
| P75 | 36,7 °C |
| P90 | 38,3 °C |
| P95 | 40,0 °C |

Surfaces par classe :

| Classe (°C) | Surface (km²) | Part |
|---|---|---|
| 20-21 | 0,0 | 0.0 % |
| 21-22 | 0,0 | 0.0 % |
| 22-23 | 0,0 | 0.0 % |
| 23-24 | 0,1 | 0.0 % |
| 24-25 | 0,6 | 0.3 % |
| 25-26 | 0,4 | 0.2 % |
| 26-27 | 0,6 | 0.3 % |
| 27-28 | 1,7 | 0.8 % |
| 28-29 | 6,2 | 3.2 % |
| 29-30 | 6,4 | 3.3 % |
| 30-31 | 10,2 | 5.2 % |
| 31-32 | 10,6 | 5.4 % |
| 32-33 | 15,5 | 7.9 % |
| 33-34 | 9,3 | 4.8 % |
| 34-35 | 25,1 | 12.9 % |
| 35-36 | 35,3 | 18.1 % |
| 36-37 | 32,1 | 16.5 % |
| 37-38 | 17,7 | 9.1 % |
| 38-39 | 9,8 | 5.0 % |
| 39-40 | 3,2 | 1.6 % |
| 40-41 | 3,0 | 1.5 % |
| 41-42 | 3,7 | 1.9 % |
| 42-43 | 1,5 | 0.8 % |
| 43-44 | 1,2 | 0.6 % |
| 44-45 | 0,6 | 0.3 % |
| 45-46 | 0,4 | 0.2 % |

Zones chaudes (> P90) et fraîches (< P10), les plus étendues :

| Zone | Moyenne (°C) | Détail |
|---|---|---|
| chaude 1 | 41,4 | max 45.9; surface 2.13 km2; centre x=7725 y=-4865 m (modele), lon=-0.54970 lat=35.65513 |
| chaude 2 | 40,2 | max 45.9; surface 1.84 km2; centre x=7408 y=4936 m (modele), lon=-0.55310 lat=35.74346 |
| chaude 3 | 41,3 | max 45.9; surface 1.62 km2; centre x=4831 y=-5226 m (modele), lon=-0.58166 lat=35.65188 |
| chaude 4 | 40,2 | max 44.1; surface 0.95 km2; centre x=7029 y=2093 m (modele), lon=-0.55732 lat=35.71784 |
| chaude 5 | 40,3 | max 44.6; surface 0.62 km2; centre x=8481 y=4168 m (modele), lon=-0.54125 lat=35.73653 |
| chaude 6 | 41,1 | max 44.6; surface 0.58 km2; centre x=9507 y=3570 m (modele), lon=-0.52991 lat=35.73113 |
| chaude 7 | 42,2 | max 45.8; surface 0.58 km2; centre x=-1369 y=-6248 m (modele), lon=-0.65011 lat=35.64268 |
| chaude 8 | 40,3 | max 43.7; surface 0.48 km2; centre x=-2943 y=-7486 m (modele), lon=-0.66749 lat=35.63152 |
| fraiche 1 | 28,5 | min 24.5; surface 5.13 km2; centre x=-4789 y=-70 m (modele), lon=-0.68791 lat=35.69835 |
| fraiche 2 | 29,1 | min 27.2; surface 1.61 km2; centre x=5656 y=-1777 m (modele), lon=-0.57252 lat=35.68297 |
| fraiche 3 | 26,0 | min 23.8; surface 1.35 km2; centre x=2580 y=-3459 m (modele), lon=-0.60651 lat=35.66782 |
| fraiche 4 | 29,2 | min 27.2; surface 0.52 km2; centre x=7777 y=-944 m (modele), lon=-0.54908 lat=35.69046 |
| fraiche 5 | 29,3 | min 27.6; surface 0.52 km2; centre x=80 y=-5334 m (modele), lon=-0.63412 lat=35.65093 |
| fraiche 6 | 29,0 | min 26.7; surface 0.44 km2; centre x=1612 y=-7753 m (modele), lon=-0.61720 lat=35.62913 |
| fraiche 7 | 29,2 | min 27.6; surface 0.38 km2; centre x=-341 y=-6018 m (modele), lon=-0.63877 lat=35.64476 |
| fraiche 8 | 29,4 | min 27.6; surface 0.38 km2; centre x=-5086 y=-5039 m (modele), lon=-0.69116 lat=35.65357 |

Par catégorie de surface de la maquette (valeur du raster sous chaque face, pondérée par la surface) :

| Catégorie | Moyenne pondérée | Médiane pondérée | Surface couverte |
|---|---|---|---|
| batiments (toits pour les bâtiments) | 36,5 °C | 36,4 °C | 28.71 km2 |
| routes / voies (toits pour les bâtiments) | 35,9 °C | 36,0 °C | 28.59 km2 |
| espaces / verts (toits pour les bâtiments) | 31,8 °C | 31,5 °C | 17.11 km2 |
| eau (toits pour les bâtiments) | 28,3 °C | 26,0 °C | 1.82 km2 |

Grille de 1 km (217 carrés, détail complet dans `Oran_Thermal_2024_Grille_1km.csv`) — 5 carrés les plus chauds et les plus frais :

| Carré | lon, lat du centre | Moyenne | Min – Max |
|---|---|---|---|
| C+04_-06 | -0.58531, 35.64942 | 41,5 °C | 36,3 – 45,8 |
| C+07_-06 | -0.55218, 35.64940 | 40,2 °C | 33,4 – 45,9 |
| C+07_-05 | -0.55217, 35.65841 | 39,7 °C | 34,9 – 45,9 |
| C+07_-07 | -0.55219, 35.64039 | 39,6 °C | 34,2 – 44,4 |
| C+09_+03 | -0.52999, 35.73050 | 39,5 °C | 34,9 – 44,6 |
| C+02_-04 | -0.60739, 35.66745 | 26,4 °C | 23,8 – 37,7 |
| C-05_+00 | -0.68472, 35.70350 | 27,8 °C | 25,4 – 30,8 |
| C-07_-01 | -0.70682, 35.69447 | 28,0 °C | 25,2 – 30,6 |
| C-06_+00 | -0.69577, 35.70349 | 28,1 °C | 25,7 – 34,5 |
| C-04_+00 | -0.67367, 35.70350 | 28,9 °C | 24,5 – 34,7 |

![Zones chaudes et fraîches, moyennes par carré de 1 km](apercu_zones_grille_2024.png)

![Carte 2D géoréférencée](Oran_Thermal_2024_carte_2D.png)

![Distribution des valeurs](histogramme_2024.png)

Lecture : le tissu bâti (toits, voirie) est en moyenne nettement plus chaud que les espaces verts et l'eau ; les zones les plus chaudes sont de grandes emprises minéralisées au sud et à l'est, la frange littorale ouest et le lac sont les plus frais. Il s'agit de la température **des surfaces** au passage du satellite (fin de matinée), pas de la température de l'air ressentie.

## 7. Limites de précision

* **Valeurs** : reconstruites depuis les couleurs d'une figure (JPEG), pas depuis le raster Landsat ; quantifiées par la légende (pas ≈ 0,05 °C, mais fidélité limitée par la compression et le lissage de la figure). Les rouges les plus sombres saturent à 45,9 °C (bout de la barre) : la valeur réelle peut être supérieure.
* **Résolution** : 1 pixel de l'image ≈ 40 m ; l'infrarouge thermique Landsat est natif à 100 m. Une cellule couvre souvent plusieurs bâtiments : la couleur d'un toit est celle de sa cellule, **ce n'est pas une mesure par bâtiment**.
* **Position** : écart médian 51,8 m, 90 % < 188,9 m ; incertitude globale de l'ordre de 50 à 170 m selon le modèle de calage.
* **Emprise** : l'extrémité est de la zone d'étude dépasse la maquette (≈ 4 % de la surface).
* **Date unique** : 10/07/2024, une acquisition ; les statistiques ne décrivent pas une moyenne estivale.

## 8. Contrôle qualité

| Vérification | Résultat |
|---|---|
| fichier 3dm thermique s ouvre | OK |
| maquette originale conservee identique | OK |
| couche THERMAL ORAN 2024 07 10 presente | OK |
| objets originaux tous presents | OK |
| objets thermiques valides | OK |
| unites modele metres | OK |
| ancrage terrestre | OK |
| raster sur terrain de la maquette pct | 95.6 |
| calage ecart median cote m | 51.8 |
| calage ecart P90 cote m | 188.9 |
| calage ecart centre lac m | 45.5 |
| controle independant pistes aeroport mediane m | 80.5 |

L'archive ZIP est relue après écriture (test CRC de chaque fichier, réouverture des deux .3dm) : `zip_check.json`.

## 9. Logiciels et reproductibilité

Python 3 — rhino3dm 8 (lecture/écriture .3dm sans Rhino), NumPy, SciPy, Pillow, Rasterio/GDAL, PyProj, GeoPandas/Shapely, Matplotlib. Scripts `20` → `25` dans `05_SCRIPTS/` ; tous les réglages dans `thermal_config.json`.
