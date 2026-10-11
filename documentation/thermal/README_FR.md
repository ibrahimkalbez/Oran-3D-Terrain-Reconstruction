# Oran — carte thermique du 10/07/2024 sur la maquette blanche Rhino 8

## Contenu du paquet `ORAN_THERMAL_2024_PACKAGE/`

| Dossier | Fichiers |
|---|---|
| `01_RHINO_MODEL/` | `Oran_Thermal_2024_3D.3dm` (maquette blanche + couche thermique), `Oran_Original_Backup.3dm` (copie intacte, empreinte SHA-256 identique à l'original) |
| `02_THERMAL_RASTER/` | `Oran_Thermal_2024.tif` (repère de la maquette), `Oran_Thermal_2024_UTM30N.tif` (EPSG:32630, pour un SIG), panneau extrait, table de la légende, document source |
| `03_GIS_DATA/` | `Oran_Thermal_2024.gpkg` : points de calage (`gcp`), limite de la zone étudiée (`study_area`), zones par classe (`thermal_classes`), zones chaudes/fraîches, grille de 1 km |
| `04_STATISTICS/` | `Oran_Thermal_2024_Statistics.csv` (global, quantiles, classes, zones, catégories), `Oran_Thermal_2024_Grille_1km.csv` |
| `05_SCRIPTS/` | scripts Python 20 → 25 (reproductibles) |
| `06_METADATA_DOCUMENTATION/` | `Oran_Thermal_2024_Metadata.json`, ce fichier, **rapport détaillé** `RAPPORT_THERMIQUE_2024_FR.pdf` / `.md` |
| `07_PREVIEWS/` | carte 2D, histogramme, aperçus de la maquette (ensemble, centre-ville) |
| `08_VALIDATION_REPORT/` | `VALIDATION_REPORT_FR.md`, rapports JSON (extraction, géoréférencement, Rhino) |

## Ouvrir dans Rhino 8

1. Ouvrir `01_RHINO_MODEL/Oran_Thermal_2024_3D.3dm` (unités : mètres ; point d'ancrage terrestre 35,699 N / 0,635 O).
2. Passer la vue en mode **Ombré** ou **Rendu** : les couleurs par sommet et les couleurs du nuage de points s'affichent.
3. Calques :

```
THERMAL_ORAN_2024_07_10                 <- tout allumer / éteindre d'un clic
 ├─ NUAGE_POINTS_THERMIQUE
 │   ├─ NUAGE_22.5-25 … NUAGE_45-46    <- nuage de couleurs, classe par classe de 2,5 °C
 │                                        (1 point par cellule de 40 m, valeur °C attachée au point)
 ├─ SURFACE_THERMIQUE_TERRAIN
 │   ├─ SURF_22.5-25 … SURF_45-46      <- carreaux colorés de 40 m posés sur le relief (+1 m)
 ├─ TOITURES_THERMIQUES                 <- toits colorés (+0,3 m)
 ├─ ZONES_CHAUDES / ZONES_FRAICHES      <- contours + étiquettes des zones > P90 / < P10
 ├─ GRILLE_1KM / GRILLE_1KM_VALEURS     <- carrés de 1 km + moyenne °C (min/max/médiane dans le nom)
 ├─ LIMITE_ZONE_ETUDIEE
 └─ LEGENDE_THERMIQUE
```

Les calques d'origine de la maquette blanche (bâtiments, routes, terrain et socle, courbes de niveau…)
ne sont pas modifiés. Pour retrouver la maquette blanche seule, il suffit d'éteindre `THERMAL_ORAN_2024_07_10`.
Chaque point du nuage porte aussi sa **valeur thermique**, que l'on peut lire avec la commande `PointCloud` / Propriétés.

## Ce qu'il faut savoir sur les valeurs

* Les valeurs sont **reconstruites à partir des couleurs de l'image**, en comparant chaque couleur à celles de la légende du document.
  Ce ne sont pas les valeurs du raster satellite d'origine. Elles sont **approximatives** et limitées par la légende et la résolution de l'image.
* Il s'agit d'une température de **surface** (lue par satellite), pas d'une **température de l'air**.
* Les toitures reçoivent la valeur de la cellule du raster située dessous. Ce n'est **pas une mesure propre à chaque bâtiment** :
  une cellule couvre souvent plusieurs bâtiments.
* La précision de position est celle du calage automatique sur la côte et le Petit Lac (écart médian ≈ 50 m, 90 % < 190 m, contrôle indépendant sur les pistes de l'aéroport) — détails dans `08_VALIDATION_REPORT/` et le rapport.

## Refaire le traitement

```
pip install numpy scipy matplotlib pillow rasterio pyproj geopandas shapely rhino3dm
python scripts/thermal/20_extract_panel_2024.py
python scripts/thermal/21a_register_panel_2024.py <maquette_blanche.3dm>
python scripts/thermal/21_georeference_2024.py
python scripts/thermal/22_thermal_to_rhino.py <maquette_blanche.3dm> <dossier_sortie>
python scripts/thermal/23_statistics_2024.py
python scripts/thermal/24_previews_metadata_validation.py <dossier_sortie>
python scripts/thermal/24b_rapport_2024.py
python scripts/thermal/25_package_2024.py <dossier_sortie> <dossier_paquet> 45
```

Tous les réglages (cadrage du panneau, graduations de la légende, paramètres du calage automatique) se trouvent dans `data/thermal/thermal_config.json`.
