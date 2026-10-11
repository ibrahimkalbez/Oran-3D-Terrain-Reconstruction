"""Step 23 - statistics and 2D map of the 10/07/2024 thermal raster.

Inputs : results/thermal/Oran_Thermal_2024.tif (+ category_samples.npz from step 22)
Outputs: results/thermal/Oran_Thermal_2024_Statistics.csv   (global, quantiles,
           classes, hot/cool zones, per surface category)
         results/thermal/Oran_Thermal_2024_carte_2D.png, histogramme_2024.png
All values come from the colour->value conversion of the document legend: they
are APPROXIMATE (quantised to the legend, image resolution) - see metadata.
"""
import csv
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402
from pyproj import Transformer  # noqa: E402
from scipy import ndimage  # noqa: E402

from thermal_common import LOCAL_CRS, WORK, grid_cells, hot_cool_zones, load_config, weighted_median  # noqa: E402

TO_LL = Transformer.from_crs(LOCAL_CRS, "EPSG:4326", always_xy=True)


def main():
    cfg = load_config()
    unit = cfg.get("unit", "")
    with rasterio.open(WORK / "Oran_Thermal_2024.tif") as src:
        a = src.read(1, masked=True).filled(np.nan); tr = src.transform
    px_area = abs(tr.a * tr.e)
    v = a[np.isfinite(a)]
    rows = []

    def add(section, name, value, u=unit, note=""):
        rows.append({"section": section, "indicateur": name,
                     "valeur": round(float(value), 3) if value is not None else "", "unite": u, "note": note})
    add("global", "pixels_valides", len(v), "pixels")
    add("global", "surface_etudiee", len(v) * px_area / 1e6, "km2")
    add("global", "minimum", v.min()); add("global", "maximum", v.max())
    add("global", "moyenne", v.mean()); add("global", "ecart_type", v.std())
    add("global", "mediane", np.median(v))
    for q in (5, 10, 25, 75, 90, 95):
        add("quantiles", f"P{q}", np.percentile(v, q))
    edges = json.loads((WORK / "georef_report.json").read_text())["class_edges"]
    for lo, hi in zip(edges[:-1], edges[1:]):
        k = (v >= lo) & ((v < hi) | (hi == edges[-1]) & (v <= hi))
        add("classes", f"{lo:g}-{hi:g}", k.sum() * px_area / 1e6, "km2", f"{100 * k.mean():.1f} % de la zone")
    # hot / cool zones: connected areas above P90 / below P10 (same definition as the Rhino layers)
    for z in hot_cool_zones(a, tr):
        lon, lat = TO_LL.transform(z["x"], z["y"])
        ext = "max" if z["kind"] == "chaude" else "min"
        add(f"zone_{z['kind']}", f"zone_{z['kind']}_{z['rank']}", z["mean"], unit,
            f"{ext} {z['extreme']:.1f}; surface {z['area_km2']:.2f} km2; centre x={z['x']:.0f} y={z['y']:.0f} m "
            f"(modele), lon={lon:.5f} lat={lat:.5f}")
    cat = WORK / "category_samples.npz"
    if cat.exists():
        d = np.load(cat, allow_pickle=True)
        for name in d.files:
            s = d[name]
            if s.size and np.isfinite(s[:, 0]).any():
                vv, w = s[:, 0], s[:, 1]; ok = np.isfinite(vv)
                vv, w = vv[ok], w[ok]
                add("categorie_surface", f"{name}_moyenne_ponderee_surface", np.average(vv, weights=w), unit,
                    f"surface couverte {w.sum() / 1e6:.2f} km2")
                add("categorie_surface", f"{name}_mediane_ponderee_surface", weighted_median(vv, w), unit)
                add("categorie_surface", f"{name}_P10_P90", np.percentile(vv, 10), unit,
                    f"P90 = {np.percentile(vv, 90):.1f} (par face)")
    with open(WORK / "Oran_Thermal_2024_Statistics.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)
    cells = grid_cells(a, tr)
    with open(WORK / "Oran_Thermal_2024_Grille_1km.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["id", "x0_m", "y0_m", "lon_centre", "lat_centre", "couverture_pct", "moyenne", "mediane",
                     "minimum", "maximum", "unite"])
        for c in cells:
            lo, la = TO_LL.transform(c["x0"] + 500, c["y0"] + 500)
            wr.writerow([c["id"], c["x0"], c["y0"], f"{lo:.5f}", f"{la:.5f}", f"{100 * c['cover']:.0f}",
                         f"{c['mean']:.2f}", f"{c['median']:.2f}", f"{c['min']:.2f}", f"{c['max']:.2f}", unit])
    # 2D map with the document's legend colours
    lut = np.load(WORK / "legend_lut.npz")
    cmap = ListedColormap(lut["rgb"] / 255.0)
    b = rasterio.transform.array_bounds(*a.shape, tr)
    fig, ax = plt.subplots(figsize=(10, 8), dpi=150)
    im = ax.imshow(a, cmap=cmap, vmin=lut["v"][0], vmax=lut["v"][-1],
                   extent=(b[0], b[2], b[1], b[3]), interpolation="nearest")
    plt.colorbar(im, ax=ax, label=f"{cfg.get('quantity', 'valeur')} ({unit})", shrink=0.8)
    ax.set_title("Oran - carte thermique du 10/07/2024 (géoréférencée, repère de la maquette)")
    ax.set_xlabel("X modèle (m, Transverse Mercator local)"); ax.set_ylabel("Y modèle (m)")
    ax.set_aspect("equal"); fig.tight_layout(); fig.savefig(WORK / "Oran_Thermal_2024_carte_2D.png"); plt.close(fig)
    fig, ax = plt.subplots(figsize=(7, 4), dpi=150)
    ax.hist(v, bins=np.arange(np.floor(v.min()), np.ceil(v.max()) + 0.5, 0.5), color="#d7191c")
    ax.set_xlabel(f"{cfg.get('quantity', 'valeur')} ({unit})"); ax.set_ylabel("pixels")
    ax.set_title("Distribution des valeurs - 10/07/2024"); fig.tight_layout()
    fig.savefig(WORK / "histogramme_2024.png"); plt.close(fig)
    print(f"{len(rows)} lignes de statistiques")


if __name__ == "__main__":
    main()
