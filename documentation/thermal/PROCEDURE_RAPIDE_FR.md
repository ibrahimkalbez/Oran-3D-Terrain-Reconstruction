# Procédure rapide — Carte thermique d'Oran sur la maquette Rhino

*Maquette blanche Rhino 8 + température de surface (LST, Landsat) 1984 → 2024*

## 1. Récupérer les livrables

| Livrable | Emplacement (branche `claude/wonderful-babbage-1yd7xy`) |
|---|---|
| Paquet complet 2024 (modèle Rhino, raster, SIG, stats, scripts, rapport) | `livraison/ORAN_THERMAL_2024_COMPLETE.zip.001` → `.019` |
| Vidéo 1984 → 2024 (maquette 3D + plans 2D) | `livraison/video/Oran_tache_chaleur_1984_2024_maquette3D_plans2D.mp4` |
| Rapport détaillé | `documentation/thermal/RAPPORT_THERMIQUE_2024_FR.pdf` |

**Ouvrir le ZIP :** placer les 19 morceaux dans un même dossier → clic droit sur `.001` → 7-Zip → *Extraire ici*.
Contrôle facultatif : l'empreinte SHA-256 doit correspondre à `ORAN_THERMAL_2024_COMPLETE.zip.sha256.txt`.

## 2. Ouvrir la maquette thermique dans Rhino 8

1. Ouvrir `01_RHINO_MODEL/Oran_Thermal_2024_3D.3dm` (mètres, ancrage 35,699 N / 0,635 O).
2. Mode d'affichage **Ombré** ou **Rendu**.
3. Panneau **Calques** → calque parent `THERMAL_ORAN_2024_07_10`.

| Calque | Usage |
|---|---|
| `NUAGE_POINTS_THERMIQUE::NUAGE_a-b` | Nuage de couleurs, 1 point / 40 m, valeur °C attachée au point |
| `SURFACE_THERMIQUE_TERRAIN::SURF_a-b` | Carreaux colorés posés sur le relief |
| `TOITURES_THERMIQUES::TOIT_a-b` | Toits colorés selon la cellule sous chaque face |
| `ZONES_CHAUDES` / `ZONES_FRAICHES` | Contours + étiquettes (moyenne, extrême, surface) |
| `GRILLE_1KM` / `GRILLE_1KM_VALEURS` | Moyenne °C par carré de 1 km |
| `LEGENDE_THERMIQUE` | Barre de couleurs à l'est du modèle |

**Gestes utiles**

* Maquette blanche seule : éteindre `THERMAL_ORAN_2024_07_10`.
* Isoler une plage de température : n'allumer que les sous-calques voulus (ex. `…_40-41` à `…_45-46` = zones > 40 °C). Une couleur de calque = une classe de 1 °C (couleur exacte de la légende + matériau de rendu).
* Lire une valeur : sélectionner un objet de la grille ou d'une zone → *Propriétés* → nom de l'objet (moyenne, min, max, médiane).
* Maquette d'origine intacte : `Oran_Original_Backup.3dm`.

## 3. Utiliser les données SIG (QGIS, ArcGIS)

* Raster : `02_THERMAL_RASTER/Oran_Thermal_2024_UTM30N.tif` (EPSG:32630, °C, NoData −9999).
* Vecteurs : `03_GIS_DATA/Oran_Thermal_2024.gpkg` — couches `study_area`, `thermal_classes`, `zones_chaudes_fraiches`, `grille_1km`, `gcp`.
* Tableaux : `04_STATISTICS/*.csv` (séparateur virgule, UTF-8).
* Série 1984 → 2024 : `results/thermal_evolution/Oran_LST_1984_2024_stack.tif` (8 bandes, une par date) et `indicateurs_1984_2024.csv`.

## 4. Refaire le traitement (Python 3)

```
pip install numpy scipy matplotlib pillow rasterio pyproj geopandas shapely rhino3dm
cd scripts/thermal
python 20_extract_panel_2024.py
python 21a_register_panel_2024.py  <maquette_blanche.3dm>
python 21_georeference_2024.py
python 22_thermal_to_rhino.py      <maquette_blanche.3dm> <sortie>
python 23_statistics_2024.py
python 24_previews_metadata_validation.py <sortie>
python 24b_rapport_2024.py
python 25_package_2024.py          <sortie> <paquet> 28
```

Tous les réglages : `data/thermal/thermal_config.json` (cadrage du panneau, graduations de la légende,
calage automatique, classes de température).

**Vidéo 1984 → 2024**

```
cd scripts/thermal_evolution
python 30_extract_all_years.py
python 31_scene_and_indicators.py <maquette_blanche.3dm>
python 32_maps_2d.py
# dans results/thermal_evolution/web : copier video/index.html et video/render.mjs,
# npm install three playwright-core, servir le dossier sur le port 8766, puis :
node render.mjs                     # images frames/fNNNN.png
ffmpeg -framerate 25 -i frames/f%04d.png -c:v libx264 -crf 23 -pix_fmt yuv420p video.mp4
```

## 5. À retenir

* Valeurs **reconstruites depuis les couleurs de la figure** (pas le raster Landsat) : approximatives.
* Température **de surface** (sol, toits), **pas de l'air**.
* Maille 40 m : la couleur d'un toit est celle de sa cellule, **pas une mesure par bâtiment**.
* Position : écart médian ≈ 50 m (calage sur la côte et le Petit Lac).
* Chaque date = une seule acquisition (météo du jour incluse).
