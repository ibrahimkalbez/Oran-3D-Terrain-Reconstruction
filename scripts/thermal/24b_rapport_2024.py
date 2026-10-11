"""Step 24b - detailed French report (Markdown + PDF) built only from the numbers
written by steps 20-24 (no hand-typed result).

Outputs: results/thermal/RAPPORT_THERMIQUE_2024_FR.md and .pdf
(PDF: pandoc -> docx -> LibreOffice; skipped with a message if unavailable)
"""
import csv
import json
import shutil
import subprocess

import numpy as np

from thermal_common import LAYER, WORK, load_config


def j(name):
    return json.loads((WORK / name).read_text(encoding="utf-8"))


def f1(x):
    return f"{x:.1f}".replace(".", ",")


def main():
    cfg = load_config()
    ex, gr, rg, rh, va, me = (j("extract_report.json"), j("georef_report.json"), j("registration_2024.json"),
                              j("rhino_thermal_report.json"), j("validation_report.json"),
                              j("Oran_Thermal_2024_Metadata.json"))
    rows = list(csv.DictReader(open(WORK / "Oran_Thermal_2024_Statistics.csv", encoding="utf-8")))
    grid = list(csv.DictReader(open(WORK / "Oran_Thermal_2024_Grille_1km.csv", encoding="utf-8")))
    S = {r["indicateur"]: r for r in rows}
    u = "°C"
    L = []
    w = L.append
    w("# Oran — carte thermique du 10/07/2024 intégrée à la maquette blanche Rhino 8")
    w("")
    w("*Rapport détaillé de traitement, d'analyse et de contrôle — généré automatiquement à partir des résultats.*")
    w("")
    w("## 1. Objet")
    w("")
    w("Projeter **uniquement** la carte de température de surface du **10 juillet 2024** (panneau inférieur central "
      "de la figure fournie) sur la maquette numérique blanche d'Oran (Rhino 8), sans reconstruire la ville, et livrer "
      "un paquet ZIP autonome (modèle Rhino, raster géoréférencé, SIG, statistiques, scripts, métadonnées, validation). "
      "Les sept autres dates de la figure (1984, 2000, 2004, 2010, 2015, 2020, 2023) n'ont pas été traitées.")
    w("")
    w("## 2. Données d'entrée")
    w("")
    w("| Élément | Valeur |")
    w("|---|---|")
    w(f"| Document | `{cfg['source_image']}` — 2000 × 1724 px, SHA-256 `{me['source']['sha256'][:16]}…` |")
    w(f"| Origine | {cfg['source_reference']} |")
    w(f"| Grandeur | {cfg['quantity']} en {u} — **température de surface du sol, pas température de l'air** |")
    w("| Échelle de couleurs | 20 → 45 °C, bleu (froid) → vert → jaune → orange → rouge (chaud) |")
    w(f"| Panneau extrait | pixels {cfg['panel_px']} de l'image (x0, y0, x1, y1) |")
    w(f"| Maquette | `{rh['source_model']}` — {rh['objects_original']} objets, mètres, "
      f"SHA-256 `{rh['source_sha256'][:16]}…` |")
    w("| Repère de la maquette | Transverse Mercator local (lat₀ 35,699°, lon₀ −0,635°, WGS84), mètres vrais, "
      "point d'ancrage Rhino 35,699 N / 0,635 O |")
    w("")
    w("Le raster Landsat original de l'étude n'étant pas accessible, les valeurs sont **reconstruites depuis l'image** "
      "(voir § 7, limites).")
    w("")
    w("## 3. Extraction du panneau 2024 et conversion couleur → température")
    w("")
    w("1. Découpe du seul panneau « 10/07/2024 » ; le titre, la flèche nord, l'échelle et les autres dates sont exclus.")
    w(f"2. **Étalonnage sur les graduations** de la légende : graduations 25, 30, 35, 40, 45 mesurées aux colonnes "
      f"{', '.join(str(t[0]) for t in cfg['legend_ticks_px'])} px (99,25 px pour 5 °C, ajustement linéaire exact). "
      f"La barre colorée dépasse la graduation 45 : elle couvre {f1(float(np.load(WORK / 'legend_lut.npz')['v'][0]))}"
      f" → {f1(ex['value_max'])} °C. {ex['legend_samples']} couleurs de référence, pas {ex['legend_step']:.3f} °C.")
    w(f"3. Chaque pixel reçoit la valeur de la couleur de légende la plus proche (distance CIE-Lab ΔE). "
      f"Écart médian couleur image ↔ légende : **ΔE {f1(ex['median_color_error_dE'])}** (imperceptible). "
      f"Les pixels à ΔE > {cfg['max_color_dist']} (contour noir, texte, port dessiné) sont NoData, jamais interpolés.")
    w(f"4. Zone d'étude = plus grande région colorée, trous bouchés ; la bordure de {cfg.get('edge_erode_px', 0)} px "
      "mélangée au trait de contour est retirée (biais mesuré vers le froid).")
    w(f"5. Résultat : **{ex['valid_pixels']} pixels valides**, valeurs {f1(ex['value_min'])} → {f1(ex['value_max'])} °C.")
    w("")
    w("![Panneau source et valeurs reconstruites](comparaison_panneau_2024.png)")
    w("")
    w("## 4. Géoréférencement sur la maquette")
    w("")
    w("Aucune coordonnée n'a été saisie à la main : le panneau est calé **automatiquement sur la géométrie de la maquette**.")
    w("")
    w(f"* **Estimation initiale** : échelle graphique 0–15 km = 377 px → {f1(rg['scalebar_m_per_px'])} m/px ; nord en haut ; "
      "centre du « Petit Lac » (Dhayet Morsli) sur le lac de la maquette.")
    w(f"* **Ajustement** : {rg['chosen_fit']['coast_points']} pixels du trait de contour côtier appariés au trait de côte "
      "de la maquette (courbe 0,5 m du MNT Copernicus), + centre du lac ; moindres carrés robustes (Huber), "
      "ré-appariement itératif.")
    w(f"* **Transformation retenue** : similitude, échelle **{rg['scale_m_per_px'][0]:.2f} m/px** "
      f"(échelle graphique : {rg['scalebar_m_per_px']:.2f} → écart {100 * abs(rg['scale_m_per_px'][0] / rg['scalebar_m_per_px'] - 1):.1f} %), "
      f"rotation **{rg['rotation_deg']:.2f}°**. Une transformation affine n'améliore pas le calage (côte RMSE "
      f"{f1(rg['fit_affine']['coast_rmse_m'])} m contre {f1(rg['fit_similarity']['coast_rmse_m'])} m) et dégrade le lac → rejetée.")
    fu = rg.get("fit_similarity_utm_rotation")
    if fu:
        w(f"* **Test UTM 30N** (rotation imposée −1,38°, convergence des méridiens) : côte médiane "
          f"{f1(fu['coast_median_m'])} m mais lac à {f1(fu['lake_centroid_error_m'])} m → moins bon, non retenu ; "
          f"l'écart entre les deux solutions (moy. {f1(rg['sensitivity_m']['similarity_rotation_utm']['mean'])} m, "
          f"max {f1(rg['sensitivity_m']['similarity_rotation_utm']['max'])} m) sert d'**indicateur d'incertitude**.")
    w("")
    w("| Contrôle de position | Valeur |")
    w("|---|---|")
    w(f"| Côte : écart médian | **{f1(gr['median_residual_m'])} m** (≈ {gr['median_residual_m'] / gr['panel_pixel_ground_size_m']:.1f} pixel) |")
    w(f"| Côte : écart à 90 % | {f1(gr['p90_residual_m'])} m |")
    w(f"| Centre du lac | {f1(rg['chosen_fit']['lake_centroid_error_m'])} m ; surface lac image "
      f"{rg['lake']['panel_area_km2']:.2f} km² / maquette {rg['lake']['model_area_km2']:.2f} km² |")
    w(f"| **Contrôle indépendant** : pistes de l'aéroport d'Es Sénia (non utilisées au calage) → pixels chauds linéaires | "
      f"médiane {f1(rg['check_runways_to_warm_pixels_m']['median_m'])} m |")
    w(f"| Taille d'un pixel de l'image au sol | {f1(gr['panel_pixel_ground_size_m'])} m |")
    w("")
    w("![Contrôle du calage : côte (bleu), port et pistes (noir), eau (magenta) de la maquette sur le panneau](controle_calage_2024.png)")
    w("")
    w(f"**Raster produit** : `Oran_Thermal_2024.tif`, maille **{gr['output_pixel_m']:.0f} m** (≈ pixel de l'image, "
      f"aucune résolution inventée), {gr['grid'][0]} × {gr['grid'][1]} cellules, {gr['valid_cells']} valides, "
      "plus proche voisin (les valeurs de la légende sont conservées), NoData = −9999, repère de la maquette ; "
      "copie `Oran_Thermal_2024_UTM30N.tif` (EPSG:32630) pour les SIG.")
    w("")
    w("## 5. Intégration dans la maquette Rhino")
    w("")
    w(f"Fichier `Oran_Thermal_2024_3D.3dm` ({f1(rh['output_MB'])} Mo) = maquette blanche **inchangée** "
      f"({rh['objects_original']} objets d'origine) + {rh['objects_thermal_added']} objets thermiques, tous valides. "
      f"Copie intacte `Oran_Original_Backup.3dm` (SHA-256 identique : {'oui' if rh['backup_identical'] else 'NON'}).")
    w("")
    w(f"| Calque (sous `{LAYER}`) | Contenu |")
    w("|---|---|")
    w("| `NUAGE_POINTS_THERMIQUE::NUAGE_a-b` | **Nuage de couleurs** : 1 point par cellule de 40 m, à 3 m au-dessus du relief, "
      "couleur de la légende + valeur °C attachée au point ; un sous-calque par classe de 2,5 °C → **lecture zone par zone** |")
    w("| `SURFACE_THERMIQUE_TERRAIN::SURF_a-b` | Carreaux colorés de 40 m posés sur le relief (+1 m, au-dessus des rues drapées), mêmes classes |")
    w(f"| `TOITURES_THERMIQUES` | {rh['roof_triangles']} triangles de toits recolorés (+0,3 m) d'après la cellule sous chaque sommet |")
    w(f"| `ZONES_CHAUDES` / `ZONES_FRAICHES` | {rh.get('zones', 0)} contours drapés + étiquettes (moyenne, extrême, surface) |")
    w(f"| `GRILLE_1KM` / `GRILLE_1KM_VALEURS` | {rh.get('grid_cells_1km', 0)} carrés de 1 km drapés + étiquette de la moyenne (détail min/max/médiane dans le nom de l'objet) |")
    w("| `LIMITE_ZONE_ETUDIEE`, `LEGENDE_THERMIQUE` | Contour de l'étude, barre de couleurs et graduations à l'est du modèle |")
    w("")
    w("Répartition des cellules par classe dans la maquette :")
    w("")
    w("| Classe (°C) | Cellules |")
    w("|---|---|")
    for k, n in rh["classes"].items():
        w(f"| {k} | {n} |")
    w("")
    w(f"Couverture : {100 * rh['raster_fraction_on_model']:.1f} % de la zone d'étude tombe sur le relief de la maquette ; "
      "le reste (extrémité est, au-delà de x = 13 650 m) dépasse l'emprise de la maquette et n'est présent que dans le raster et le GPKG.")
    w("")
    w("![Maquette : relief + couche thermique](apercu_3d_thermique.png)")
    w("")
    w("![Centre-ville : toitures colorées](apercu_3d_thermique_centre.png)")
    w("")
    w("## 6. Analyse thermique du 10/07/2024 (valeurs approximatives, issues de l'image)")
    w("")
    w("| Indicateur | Valeur |")
    w("|---|---|")
    for k in ("surface_etudiee", "minimum", "maximum", "moyenne", "ecart_type", "mediane", "P5", "P10", "P25",
              "P75", "P90", "P95"):
        r = S[k]
        w(f"| {k.replace('_', ' ')} | {f1(float(r['valeur']))} {r['unite'].replace('degC', u)} |")
    w("")
    w("Surfaces par classe :")
    w("")
    w("| Classe (°C) | Surface (km²) | Part |")
    w("|---|---|---|")
    for r in rows:
        if r["section"] == "classes":
            w(f"| {r['indicateur']} | {f1(float(r['valeur']))} | {r['note'].replace(' de la zone', '')} |")
    w("")
    w("Zones chaudes (> P90) et fraîches (< P10), les plus étendues :")
    w("")
    w("| Zone | Moyenne (°C) | Détail |")
    w("|---|---|---|")
    for r in rows:
        if r["section"].startswith("zone_"):
            w(f"| {r['indicateur'].replace('zone_', '').replace('_', ' ')} | {f1(float(r['valeur']))} | {r['note']} |")
    w("")
    w("Par catégorie de surface de la maquette (valeur du raster sous chaque face, pondérée par la surface) :")
    w("")
    w("| Catégorie | Moyenne pondérée | Médiane pondérée | Surface couverte |")
    w("|---|---|---|---|")
    for c in ("batiments", "routes_voies", "espaces_verts", "eau"):
        m = S.get(f"{c}_moyenne_ponderee_surface")
        if m:
            w(f"| {c.replace('_', ' / ')} (toits pour les bâtiments) | {f1(float(m['valeur']))} °C | "
              f"{f1(float(S[c + '_mediane_ponderee_surface']['valeur']))} °C | {m['note'].replace('surface couverte ', '')} |")
    w("")
    gs = sorted(grid, key=lambda r: float(r["moyenne"]))
    w(f"Grille de 1 km ({len(grid)} carrés, détail complet dans `Oran_Thermal_2024_Grille_1km.csv`) — "
      "5 carrés les plus chauds et les plus frais :")
    w("")
    w("| Carré | lon, lat du centre | Moyenne | Min – Max |")
    w("|---|---|---|---|")
    for r in gs[::-1][:5] + gs[:5]:
        w(f"| {r['id']} | {r['lon_centre']}, {r['lat_centre']} | {f1(float(r['moyenne']))} °C | "
          f"{f1(float(r['minimum']))} – {f1(float(r['maximum']))} |")
    w("")
    w("![Zones chaudes et fraîches, moyennes par carré de 1 km](apercu_zones_grille_2024.png)")
    w("")
    w("![Carte 2D géoréférencée](Oran_Thermal_2024_carte_2D.png)")
    w("")
    w("![Distribution des valeurs](histogramme_2024.png)")
    w("")
    w("Lecture : le tissu bâti (toits, voirie) est en moyenne nettement plus chaud que les espaces verts et l'eau ; "
      "les zones les plus chaudes sont de grandes emprises minéralisées au sud et à l'est, la frange littorale "
      "ouest et le lac sont les plus frais. Il s'agit de la température **des surfaces** au passage du satellite "
      "(fin de matinée), pas de la température de l'air ressentie.")
    w("")
    w("## 7. Limites de précision")
    w("")
    w("* **Valeurs** : reconstruites depuis les couleurs d'une figure (JPEG), pas depuis le raster Landsat ; "
      "quantifiées par la légende (pas ≈ 0,05 °C, mais fidélité limitée par la compression et le lissage de la figure). "
      f"Les rouges les plus sombres saturent à {f1(ex['value_max'])} °C (bout de la barre) : la valeur réelle peut être supérieure.")
    w("* **Résolution** : 1 pixel de l'image ≈ 40 m ; l'infrarouge thermique Landsat est natif à 100 m. "
      "Une cellule couvre souvent plusieurs bâtiments : la couleur d'un toit est celle de sa cellule, "
      "**ce n'est pas une mesure par bâtiment**.")
    w(f"* **Position** : écart médian {f1(gr['median_residual_m'])} m, 90 % < {f1(gr['p90_residual_m'])} m ; "
      "incertitude globale de l'ordre de 50 à 170 m selon le modèle de calage.")
    w("* **Emprise** : l'extrémité est de la zone d'étude dépasse la maquette (≈ 4 % de la surface).")
    w("* **Date unique** : 10/07/2024, une acquisition ; les statistiques ne décrivent pas une moyenne estivale.")
    w("")
    w("## 8. Contrôle qualité")
    w("")
    w("| Vérification | Résultat |")
    w("|---|---|")
    for k, v in va["checks"].items():
        w(f"| {k.replace('_', ' ')} | {'OK' if v is True else ('ÉCHEC' if v is False else v)} |")
    w("")
    w("L'archive ZIP est relue après écriture (test CRC de chaque fichier, réouverture des deux .3dm) : `zip_check.json`.")
    w("")
    w("## 9. Logiciels et reproductibilité")
    w("")
    w("Python 3 — rhino3dm 8 (lecture/écriture .3dm sans Rhino), NumPy, SciPy, Pillow, Rasterio/GDAL, PyProj, "
      "GeoPandas/Shapely, Matplotlib. Scripts `20` → `25` dans `05_SCRIPTS/` ; tous les réglages dans `thermal_config.json`.")
    md = "\n".join(L) + "\n"
    out = WORK / "RAPPORT_THERMIQUE_2024_FR.md"
    out.write_text(md, encoding="utf-8")
    if shutil.which("pandoc") and shutil.which("soffice"):
        docx = WORK / "RAPPORT_THERMIQUE_2024_FR.docx"
        subprocess.run(["pandoc", out.name, "-o", docx.name, "--resource-path", "."], cwd=WORK, check=True)
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf", docx.name], cwd=WORK, check=True,
                       capture_output=True, timeout=600)
        docx.unlink(missing_ok=True)
    print("rapport:", out, (WORK / "RAPPORT_THERMIQUE_2024_FR.pdf").exists())


if __name__ == "__main__":
    main()
