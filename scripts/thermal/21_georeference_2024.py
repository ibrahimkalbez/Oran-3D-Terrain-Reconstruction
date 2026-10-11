"""Step 21 - georeference the extracted 10/07/2024 panel and write the thermal
raster in the coordinate system of the Rhino model.

Ground control points (thermal_config.json "gcps"): list of
  {"name": ..., "px": [col, row] in PANEL pixels, "lon": ..., "lat": ...}
picked on features identifiable both on the panel and on the map (coast
headlands, port jetties, boundary corners). Their lon/lat come from the
Copernicus coastline / OpenStreetMap geometry already used by the Rhino model.

Transform: affine (>= 3 GCPs) or 2nd-order polynomial (>= 8 GCPs, config
"gcp_model": "poly2"). Residuals and leave-one-out errors are reported; they
ARE the positional accuracy of the result (nothing better is claimed).

Outputs (results/thermal/):
  Oran_Thermal_2024.tif         float32 GeoTIFF, model CRS (local Transverse Mercator,
                                metres, = Rhino model XY), NoData = -9999
  Oran_Thermal_2024_UTM30N.tif  same data in EPSG:32630 for GIS use
  Oran_Thermal_2024.gpkg        layers: gcp, study_area, thermal_classes
  georef_report.json
"""
import json

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.features import shapes
from rasterio.transform import from_origin
from rasterio.warp import Resampling, calculate_default_transform, reproject
from pyproj import Transformer

from thermal_common import LOCAL_CRS, WORK, load_config

NODATA = -9999.0
LOC = Transformer.from_crs("EPSG:4326", LOCAL_CRS, always_xy=True)


def design(px, model):
    x, y = px[:, 0], px[:, 1]
    cols = [np.ones_like(x), x, y]
    if model == "poly2":
        cols += [x * x, x * y, y * y]
    return np.stack(cols, 1)


def fit(src, dst, model):
    A = design(src, model)
    coef, *_ = np.linalg.lstsq(A, dst, rcond=None)
    return coef


def apply(coef, src, model):
    return design(src, model) @ coef


def main():
    cfg = load_config()
    model = cfg.get("gcp_model", "affine")
    g = cfg["gcps"]
    px = np.array([p["px"] for p in g], float) + 0.5            # pixel centres
    xy = np.array(LOC.transform([p["lon"] for p in g], [p["lat"] for p in g])).T
    need = 8 if model == "poly2" else 3
    if len(g) < need:
        raise SystemExit(f"{model} needs >= {need} GCPs, got {len(g)}")
    fwd = fit(px, xy, model)
    inv = fit(xy, px, model)
    res = apply(fwd, px, model) - xy
    loo = []
    for i in range(len(g)):
        k = np.arange(len(g)) != i
        if k.sum() >= need:
            loo.append(float(np.linalg.norm(apply(fit(px[k], xy[k], model), px[i:i + 1], model) - xy[i])))
    val = np.load(WORK / "values_px.npy")
    h, w = val.shape
    # ground size of one panel pixel (affine part of the fit)
    J = np.linalg.lstsq(design(px, "affine"), xy, rcond=None)[0][1:]
    gsd = float(np.sqrt(abs(np.linalg.det(J))))
    res_m = float(cfg.get("output_pixel_m", round(gsd)))
    corners = apply(fwd, np.array([[0, 0], [w, 0], [0, h], [w, h]], float), model)
    xmin, ymin = corners.min(0); xmax, ymax = corners.max(0)
    xmin, ymin = np.floor([xmin / res_m, ymin / res_m]) * res_m
    xmax, ymax = np.ceil([xmax / res_m, ymax / res_m]) * res_m
    nx, ny = int((xmax - xmin) / res_m), int((ymax - ymin) / res_m)
    cx = xmin + (np.arange(nx) + 0.5) * res_m
    cy = ymax - (np.arange(ny) + 0.5) * res_m
    X, Y = np.meshgrid(cx, cy)
    sp = apply(inv, np.c_[X.ravel(), Y.ravel()], model)
    c, r = np.floor(sp[:, 0]).astype(int), np.floor(sp[:, 1]).astype(int)
    inside = (c >= 0) & (c < w) & (r >= 0) & (r < h)
    out = np.full(nx * ny, np.nan, np.float32)
    out[inside] = val[r[inside], c[inside]]                       # nearest neighbour: legend values kept
    out = out.reshape(ny, nx)
    data = np.where(np.isfinite(out), out, NODATA).astype(np.float32)
    tr = from_origin(xmin, ymax, res_m, res_m)
    crs = CRS.from_proj4(LOCAL_CRS)
    prof = dict(driver="GTiff", width=nx, height=ny, count=1, dtype="float32", crs=crs,
                transform=tr, nodata=NODATA, compress="deflate")
    tif = WORK / "Oran_Thermal_2024.tif"
    with rasterio.open(tif, "w", **prof) as dst:
        dst.write(data, 1)
        dst.update_tags(DATE="2024-07-10", QUANTITY=cfg.get("quantity", "see metadata"),
                        UNIT=cfg.get("unit", "unknown"), SOURCE=cfg["source_image"],
                        METHOD="colour->value from document legend, GCP georeferencing",
                        ACCURACY_RMSE_M=f"{np.sqrt((res ** 2).sum(1).mean()):.1f}")
    # UTM 30N copy for GIS
    t2, w2, h2 = calculate_default_transform(crs, "EPSG:32630", nx, ny, xmin, ymin, xmax, ymax,
                                             resolution=res_m)
    utm = np.full((h2, w2), NODATA, np.float32)
    reproject(data, utm, src_transform=tr, src_crs=crs, dst_transform=t2, dst_crs="EPSG:32630",
              src_nodata=NODATA, dst_nodata=NODATA, resampling=Resampling.nearest)
    with rasterio.open(WORK / "Oran_Thermal_2024_UTM30N.tif", "w", **{**prof, "crs": "EPSG:32630",
                       "transform": t2, "width": w2, "height": h2}) as dst:
        dst.write(utm, 1)
    # vector layers
    import geopandas as gpd
    from shapely.geometry import Point, shape
    from shapely.ops import unary_union
    gpkg = WORK / "Oran_Thermal_2024.gpkg"
    gpkg.unlink(missing_ok=True)
    gpd.GeoDataFrame({"name": [p["name"] for p in g], "lon": [p["lon"] for p in g],
                      "lat": [p["lat"] for p in g], "px_col": px[:, 0] - .5, "px_row": px[:, 1] - .5,
                      "residual_m": np.linalg.norm(res, axis=1)},
                     geometry=[Point(*p) for p in xy], crs=crs).to_file(gpkg, layer="gcp", driver="GPKG")
    valid = np.isfinite(out)
    area = [shape(s) for s, v in shapes(valid.astype(np.uint8), mask=valid, transform=tr) if v == 1]
    gpd.GeoDataFrame({"name": ["zone_etudiee_2024_07_10"]}, geometry=[unary_union(area)],
                     crs=crs).to_file(gpkg, layer="study_area", driver="GPKG")
    edges = cfg.get("class_edges", list(np.arange(cfg["legend_min"], cfg["legend_max"] + 1e-9, 5.0)))
    cls = np.full(out.shape, 0, np.int16)
    cls[valid] = np.clip(np.digitize(out[valid], edges[1:-1]) + 1, 1, len(edges) - 1)
    recs = [(int(v), shape(s)) for s, v in shapes(cls, mask=valid, transform=tr)]
    gpd.GeoDataFrame({"class_id": [r[0] for r in recs],
                      "label": [f"{edges[r[0]-1]:g}-{edges[r[0]]:g}" for r in recs],
                      "area_m2": [r[1].area for r in recs]},
                     geometry=[r[1] for r in recs], crs=crs).to_file(gpkg, layer="thermal_classes", driver="GPKG")
    rep = {"model": model, "n_gcp": len(g),
           "rmse_m": float(np.sqrt((res ** 2).sum(1).mean())),
           "max_residual_m": float(np.linalg.norm(res, axis=1).max()),
           "leave_one_out_mean_m": float(np.mean(loo)) if loo else None,
           "leave_one_out_max_m": float(np.max(loo)) if loo else None,
           "panel_pixel_ground_size_m": gsd, "output_pixel_m": res_m,
           "grid": [nx, ny], "bounds_model_m": [float(xmin), float(ymin), float(xmax), float(ymax)],
           "valid_cells": int(valid.sum()), "class_edges": [float(e) for e in edges],
           "crs_model": LOCAL_CRS}
    (WORK / "georef_report.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
