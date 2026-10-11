"""Step 24 - previews of the thermal maquette, metadata JSON and validation report.

Usage: python scripts/thermal/24_previews_metadata_validation.py <dir with the two .3dm>
Outputs (results/thermal/): apercu_3d_thermique.png (whole study area),
  apercu_3d_thermique_centre.png (city centre, coloured roofs),
  Oran_Thermal_2024_Metadata.json, validation_report.json, VALIDATION_REPORT_FR.md
"""
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
import rhino3dm as r3  # noqa: E402
from matplotlib.collections import PolyCollection  # noqa: E402

from thermal_common import DATA, LAYER, LOCAL_CRS, ORIGIN_LAT, ORIGIN_LON, WORK, load_config  # noqa: E402

CENTRE = (-4200, -900, 1800, 3200)          # model metres: Sidi El Houari -> city centre


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def mesh_tris(g):
    V = np.array([[p.X, p.Y, p.Z] for p in g.Vertices])
    F = np.array([tuple(g.Faces[k]) for k in range(g.Faces.Count)]).reshape(-1, 4)
    q = F[:, 2] != F[:, 3]
    T = np.r_[F[:, [0, 1, 2]], F[q][:, [0, 2, 3]]]
    C = np.array([g.VertexColors[i][:3] for i in range(g.VertexColors.Count)]) if g.VertexColors.Count else None
    return V, T, C


def hillshade(Z, res):
    gy, gx = np.gradient(Z, res)
    slope = np.arctan(np.hypot(gx, gy)); asp = np.arctan2(-gx, gy)
    az, alt = np.radians(315), np.radians(45)
    return np.clip(np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - asp), 0, 1)


def main():
    mdir = Path(sys.argv[1])
    cfg = load_config()
    thermal = mdir / "Oran_Thermal_2024_3D.3dm"
    backup = mdir / "Oran_Original_Backup.3dm"
    rrep = json.loads((WORK / "rhino_thermal_report.json").read_text())
    grep = json.loads((WORK / "georef_report.json").read_text())
    erep = json.loads((WORK / "extract_report.json").read_text())
    m = r3.File3dm.Read(str(thermal))
    names = {i: m.Layers[i].FullPath for i in range(len(m.Layers))}
    # ---------------- previews
    terr = roofs = None
    surf = []
    for o in m.Objects:
        ln = names[o.Attributes.LayerIndex]
        if ln == "TERRAIN_ET_SOCLE":
            terr = np.array([[p.X, p.Y, p.Z] for p in o.Geometry.Vertices])
        elif ln.endswith("TOITURES_THERMIQUES"):
            roofs = (roofs or []) + [mesh_tris(o.Geometry)]
    with rasterio.open(WORK / "Oran_Thermal_2024.tif") as ds:
        T = ds.read(1, masked=True).filled(np.nan); tr = ds.transform
    lut = np.load(WORK / "legend_lut.npz")
    from matplotlib.colors import ListedColormap
    cmap = ListedColormap(lut["rgb"] / 255.0)
    top = terr[terr[:, 2] > terr[:, 2].min() + 1e-6]
    xs, ys = np.unique(top[:, 0]), np.unique(top[:, 1])
    Z = np.zeros((len(ys), len(xs)))
    Z[np.searchsorted(ys, top[:, 1]), np.searchsorted(xs, top[:, 0])] = top[:, 2]
    hs = hillshade(Z, xs[1] - xs[0])
    b = rasterio.transform.array_bounds(*T.shape, tr)
    for fname, box, with_roofs in (("apercu_3d_thermique.png", (b[0], b[1], b[2], b[3]), False),
                                   ("apercu_3d_thermique_centre.png", CENTRE, True)):
        fig, ax = plt.subplots(figsize=(12, 9), dpi=160)
        ax.imshow(hs, cmap="gray", extent=(xs[0], xs[-1], ys[0], ys[-1]), origin="lower", vmin=0, vmax=1.3)
        im = ax.imshow(T, cmap=cmap, vmin=lut["v"][0], vmax=lut["v"][-1], alpha=0.72,
                       extent=(b[0], b[2], b[1], b[3]), interpolation="nearest")
        if with_roofs and roofs:
            polys, cols = [], []
            for V, Tr, C in roofs:
                cen = V[Tr].mean(1)
                k = (cen[:, 0] > box[0]) & (cen[:, 0] < box[2]) & (cen[:, 1] > box[1]) & (cen[:, 1] < box[3])
                polys.append(V[Tr[k]][:, :, :2]); cols.append(C[Tr[k]].mean(1) / 255.0)
            pc = PolyCollection(np.concatenate(polys), facecolors=np.concatenate(cols),
                                edgecolors="k", linewidths=0.05)
            ax.add_collection(pc)
        ax.set_xlim(box[0], box[2]); ax.set_ylim(box[1], box[3]); ax.set_aspect("equal")
        plt.colorbar(im, ax=ax, shrink=0.75, label=f"{cfg.get('quantity', '')} ({cfg.get('unit', '')})")
        ax.set_title("Maquette Rhino d'Oran - couche " + LAYER + (" - toitures (centre)" if with_roofs else ""))
        ax.set_xlabel("X modèle (m)"); ax.set_ylabel("Y modèle (m)")
        fig.tight_layout(); fig.savefig(WORK / fname); plt.close(fig)
    # zones + 1 km grid map, and source panel vs reconstruction
    import geopandas as gpd
    gpkg = WORK / "Oran_Thermal_2024.gpkg"
    zones = gpd.read_file(gpkg, layer="zones_chaudes_fraiches")
    grid = gpd.read_file(gpkg, layer="grille_1km")
    fig, ax = plt.subplots(figsize=(14, 11), dpi=160)
    ax.imshow(hs, cmap="gray", extent=(xs[0], xs[-1], ys[0], ys[-1]), origin="lower", vmin=0, vmax=1.3)
    im = ax.imshow(T, cmap=cmap, vmin=lut["v"][0], vmax=lut["v"][-1], alpha=0.8,
                   extent=(b[0], b[2], b[1], b[3]), interpolation="nearest")
    for _, c in grid.iterrows():
        x, y = c.geometry.exterior.xy
        ax.plot(x, y, color="0.35", lw=0.3)
        ax.text(c["x0"] + 500, c["y0"] + 500, f"{c['mean']:.1f}", ha="center", va="center", fontsize=4.2)
    for _, z in zones.iterrows():
        col = "#7f0000" if z["kind"] == "chaude" else "#08306b"
        for poly in getattr(z.geometry, "geoms", [z.geometry]):
            x, y = poly.exterior.xy
            ax.plot(x, y, color=col, lw=1.2)
        ax.annotate(f"{'C' if z['kind'] == 'chaude' else 'F'}{z['rank']} {z['mean']:.1f}", (z["x"], z["y"]),
                    xytext=(6, 6), textcoords="offset points", fontsize=7, color=col, weight="bold")
    ax.set_xlim(b[0], b[2]); ax.set_ylim(b[1], b[3]); ax.set_aspect("equal")
    plt.colorbar(im, ax=ax, shrink=0.7, label=f"{cfg.get('quantity', '')} ({cfg.get('unit', '')})")
    ax.set_title("10/07/2024 — zones chaudes (C, > P90) et fraîches (F, < P10), moyenne par carré de 1 km")
    fig.tight_layout(); fig.savefig(WORK / "apercu_zones_grille_2024.png"); plt.close(fig)
    from PIL import Image
    val_px = np.load(WORK / "values_px.npy")
    fig, ax = plt.subplots(1, 2, figsize=(16, 6.5), dpi=130)
    ax[0].imshow(Image.open(WORK / "panel_2024.png")); ax[0].set_title("Document source — panneau 10/07/2024")
    ax[1].imshow(val_px, cmap=cmap, vmin=lut["v"][0], vmax=lut["v"][-1], interpolation="nearest")
    ax[1].set_title("Valeurs reconstruites (couleur -> °C par la légende)")
    for a_ in ax:
        a_.set_xticks([]); a_.set_yticks([])
    fig.tight_layout(); fig.savefig(WORK / "comparaison_panneau_2024.png"); plt.close(fig)
    # ---------------- validation
    checks = {
        "fichier_3dm_thermique_s_ouvre": m is not None and len(m.Objects) > 0,
        "maquette_originale_conservee_identique": sha(backup) == rrep["source_sha256"],
        "couche_THERMAL_ORAN_2024_07_10_presente": any(n == LAYER for n in names.values()),
        "objets_originaux_tous_presents": rrep["objects_output"] - rrep["objects_thermal_added"]
        == rrep["objects_original"],
        "objets_thermiques_valides": not rrep["invalid_thermal_objects"],
        "unites_modele_metres": str(m.Settings.ModelUnitSystem).endswith("Meters"),
        "ancrage_terrestre": [m.Settings.EarthAnchorPoint.EarthBasepointLatitude,
                              m.Settings.EarthAnchorPoint.EarthBasepointLongitude] == [ORIGIN_LAT, ORIGIN_LON],
        "raster_sur_terrain_de_la_maquette_pct": round(100 * rrep["raster_fraction_on_model"], 2),
        "calage_ecart_median_cote_m": round(grep["median_residual_m"], 1),
        "calage_ecart_P90_cote_m": round(grep["p90_residual_m"], 1),
        "calage_ecart_centre_lac_m": round(json.loads((WORK / "registration_2024.json").read_text())
                                           ["chosen_fit"]["lake_centroid_error_m"], 1),
        "controle_independant_pistes_aeroport_mediane_m": round(json.loads((WORK / "registration_2024.json").read_text())
                                                                ["check_runways_to_warm_pixels_m"]["median_m"], 1),
    }
    val = {"date_controle": date.today().isoformat(), "checks": checks,
           "rhino": rrep, "georeferencement": grep, "extraction": erep}
    (WORK / "validation_report.json").write_text(json.dumps(val, indent=1, ensure_ascii=False))
    ok = lambda v: "OK" if v is True else ("ÉCHEC" if v is False else str(v))  # noqa: E731
    md = ["# Rapport de validation — carte thermique du 10/07/2024 sur la maquette blanche d'Oran", "",
          "| Contrôle | Résultat |", "|---|---|"]
    md += [f"| {k.replace('_', ' ')} | {ok(v)} |" for k, v in checks.items()]
    md += ["", f"Objets d'origine : {rrep['objects_original']} — objets thermiques ajoutés : "
           f"{rrep['objects_thermal_added']} — taille : {rrep['output_MB']} Mo.", "",
           "Calques thermiques :", ""] + [f"* `{n}`" for n in rrep["thermal_layers"]]
    (WORK / "VALIDATION_REPORT_FR.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    # ---------------- metadata
    meta = {
        "titre": "Carte thermique d'Oran du 10/07/2024 intégrée à la maquette blanche Rhino 8",
        "date_donnees": "2024-07-10",
        "grandeur": cfg.get("quantity"), "unite": cfg.get("unit"),
        "source": {"document": cfg["source_image"], "sha256": sha(DATA / cfg["source_image"]),
                   "origine_declaree": cfg.get("source_reference", "non précisée dans le document"),
                   "panneau_extrait_px": cfg["panel_px"], "legende_px": cfg["legend_px"],
                   "legende_min_max": [cfg["legend_min"], cfg["legend_max"]]},
        "nature_des_valeurs": "Reconstruites à partir des couleurs de l'image (légende du document) ; "
                              "approximatives, quantifiées par la légende ; ce ne sont pas les valeurs du raster original.",
        "temperature": "Température de SURFACE terrestre (LST, Landsat) — ne pas confondre avec la température de l'air.",
        "crs_modele": LOCAL_CRS, "crs_copie_SIG": "EPSG:32630",
        "ancrage_rhino": {"lat": ORIGIN_LAT, "lon": ORIGIN_LON},
        "resolution_sortie_m": grep["output_pixel_m"],
        "taille_pixel_image_au_sol_m": round(grep["panel_pixel_ground_size_m"], 1),
        "georeferencement": {"methode": "calage automatique trait de cote + centre du lac sur la maquette (script 21a)",
                             "modele": grep["model"], "n_points_controle": grep["n_gcp"],
                             "ecart_median_m": round(grep["median_residual_m"], 1),
                             "ecart_P90_m": round(grep["p90_residual_m"], 1),
                             "registration": json.loads((WORK / "registration_2024.json").read_text())},
        "nodata": -9999, "classes": grep["class_edges"],
        "maquette": {"source": rrep["source_model"], "sha256": rrep["source_sha256"],
                     "calque_thermique": LAYER, "sous_calques": rrep["thermal_layers"]},
        "fichiers": {p.name: sha(p) for p in (thermal, backup)},
    }
    (WORK / "Oran_Thermal_2024_Metadata.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    print(json.dumps(checks, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
