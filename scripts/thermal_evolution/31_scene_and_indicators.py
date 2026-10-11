"""Step 31 - 3D scene (from the white Rhino maquette) + per-date thermal data and
technical indicators for the 1984 -> 2024 video.

Scene (results/thermal_evolution/web/, three.js axes: x = east, y = up, z = -north,
origin = model origin 35.699 N / 0.635 W):
  terrain_pos.bin / terrain_idx.bin   Copernicus relief of the maquette (25 m), cropped
  bld_pos.bin                         every building triangle of the maquette (non indexed)
  lst_<i>.bin                         float32 grid (ny, nx) of LST for date i, NaN -> -1
  zones_<i>.bin                       hot-zone outlines of date i (line segments, >= P90 of the date)
  scene.json                          grid geometry, dates, indicators, camera path
Indicators (results/thermal_evolution/indicateurs_1984_2024.csv) per date:
  mean, median, std, P10, P90, % area > 35 / 40 degC, area > 40 degC, SUHI proxy
  (mean LST of built cells - mean LST of non-built cells; built = OSM building cover
  >= 20 % of the 40 m cell, CURRENT buildings applied to all dates), hot-spot centroid.
Usage: python 31_scene_and_indicators.py <white maquette .3dm>
"""
import csv
import json
import sys
from pathlib import Path

import numpy as np
import rhino3dm as r3
from pyproj import Transformer
from rasterio.features import shapes
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from oran_georef import LOCAL_CRS  # noqa: E402

OUT = ROOT / "results/thermal_evolution"
WEB = OUT / "web"
BUILD = ("BUILDING", "HEIGHT_", "EXTRA_BUILD")
TO_LL = Transformer.from_crs(LOCAL_CRS, "EPSG:4326", always_xy=True)


def mesh_tris(g):
    V = np.array([[p.X, p.Y, p.Z] for p in g.Vertices], np.float64)
    F = np.array([tuple(g.Faces[k]) for k in range(g.Faces.Count)], np.int64).reshape(-1, 4)
    q = F[:, 2] != F[:, 3]
    return V, np.r_[F[:, [0, 1, 2]], F[q][:, [0, 2, 3]]]


def to3(P):
    """model (x east, y north, z up) -> three.js (x, y up, z = -north)"""
    P = np.asarray(P, np.float64)
    return np.c_[P[:, 0], P[:, 2], -P[:, 1]].astype(np.float32)


def main():
    WEB.mkdir(parents=True, exist_ok=True)
    d = np.load(OUT / "stack_grid.npz")
    dates = [str(x) for x in d["dates"]]; B = d["values"]; t = d["transform"]
    res, gx0, gy1 = t[0], t[2], t[5]
    nb, ny, nx = B.shape
    gx1, gy0 = gx0 + nx * res, gy1 - ny * res
    m = r3.File3dm.Read(sys.argv[1])
    bld = []
    for o in m.Objects:
        name = m.Layers[o.Attributes.LayerIndex].Name
        if not isinstance(o.Geometry, r3.Mesh):
            continue
        if name == "TERRAIN_ET_SOCLE":
            V = np.array([[p.X, p.Y, p.Z] for p in o.Geometry.Vertices])
            top = V[V[:, 2] > V[:, 2].min() + 1e-6]
            xs, ys = np.unique(top[:, 0]), np.unique(top[:, 1])
            Z = np.zeros((len(ys), len(xs)))
            Z[np.searchsorted(ys, top[:, 1]), np.searchsorted(xs, top[:, 0])] = top[:, 2]
        elif name.startswith(BUILD):
            V, T = mesh_tris(o.Geometry)
            c = V[T].mean(1)
            k = (c[:, 0] > gx0 - 500) & (c[:, 0] < gx1 + 500) & (c[:, 1] > gy0 - 500) & (c[:, 1] < gy1 + 500)
            bld.append(V[T[k]].reshape(-1, 3))
    # terrain crop (study area + 1.5 km)
    ix = (xs > gx0 - 1500) & (xs < gx1 + 1500); iy = (ys > gy0 - 1500) & (ys < gy1 + 1500)
    xs_, ys_, Z_ = xs[ix], ys[iy], Z[np.ix_(iy, ix)]
    X, Y = np.meshgrid(xs_, ys_)
    to3(np.c_[X.ravel(), Y.ravel(), Z_.ravel()]).tofile(WEB / "terrain_pos.bin")
    n1, n2 = len(ys_), len(xs_)
    i = (np.arange(n1 - 1)[:, None] * n2 + np.arange(n2 - 1)[None, :]).ravel()
    np.r_[np.c_[i, i + n2, i + 1].ravel(), np.c_[i + 1, i + n2, i + n2 + 1].ravel()].astype(np.uint32) \
        .tofile(WEB / "terrain_idx.bin")
    P = np.vstack(bld); to3(P).tofile(WEB / "bld_pos.bin")
    # terrain height at the centre of every thermal cell (base of the 3D heat spots)
    from scipy.interpolate import RegularGridInterpolator
    gi = RegularGridInterpolator((ys, xs), Z, bounds_error=False, fill_value=0.0)
    CX, CY = np.meshgrid(gx0 + (np.arange(nx) + .5) * res, gy1 - (np.arange(ny) + .5) * res)
    gi(np.c_[CY.ravel(), CX.ravel()]).astype(np.float32).tofile(WEB / "tz_grid.bin")
    # building cover per 40 m cell (horizontal roof area / cell area)
    Tr = P.reshape(-1, 3, 3)
    nz = np.cross(Tr[:, 1] - Tr[:, 0], Tr[:, 2] - Tr[:, 0])[:, 2] * 0.5
    up = nz > 0
    cen = Tr[up].mean(1)
    cc = np.floor((cen[:, 0] - gx0) / res).astype(int); rr = np.floor((gy1 - cen[:, 1]) / res).astype(int)
    ok = (cc >= 0) & (cc < nx) & (rr >= 0) & (rr < ny)
    cover = np.zeros((ny, nx)); np.add.at(cover, (rr[ok], cc[ok]), nz[up][ok])
    cover /= res * res
    built = cover >= 0.20
    # thermal per date
    rows, zones_meta = [], []
    for k_, date in enumerate(dates):
        a = B[k_]
        np.where(np.isfinite(a), a, -1).astype(np.float32).tofile(WEB / f"lst_{k_}.bin")
        v = a[np.isfinite(a)]
        p90 = np.percentile(v, 90)
        hot = np.nan_to_num(a, nan=0) >= p90
        hot = ndimage.binary_opening(hot)
        lab, n = ndimage.label(hot)
        sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1)) if n else np.array([])
        segs = []
        for s, val in shapes(hot.astype(np.uint8), mask=hot, transform=(res, 0, gx0, 0, -res, gy1)):
            for ring in [s["coordinates"][0]]:
                ring = np.array(ring)
                if len(ring) < 8:
                    continue
                zz = np.array([Z[np.clip(np.searchsorted(ys, y_), 0, len(ys) - 1),
                                 np.clip(np.searchsorted(xs, x_), 0, len(xs) - 1)] for x_, y_ in ring])
                Pq = np.c_[ring, zz + 4]
                segs.append(np.repeat(Pq, 2, 0)[1:-1])
        (to3(np.vstack(segs)) if segs else np.zeros((0, 3), np.float32)).tofile(WEB / f"zones_{k_}.bin")
        w = np.where(hot, a, 0)
        rr_, cc_ = np.nonzero(hot)
        hx = gx0 + (np.average(cc_, weights=w[hot]) + .5) * res; hy = gy1 - (np.average(rr_, weights=w[hot]) + .5) * res
        lon, lat = TO_LL.transform(hx, hy)
        fin = np.isfinite(a)
        rows.append({"date": date, "moyenne": v.mean(), "mediane": np.median(v), "ecart_type": v.std(),
                     "P10": np.percentile(v, 10), "P90": p90, "pct_sup_35": 100 * (v > 35).mean(),
                     "pct_sup_40": 100 * (v > 40).mean(), "km2_sup_40": (v > 40).sum() * res * res / 1e6,
                     "suhi_bati_moins_non_bati": a[fin & built].mean() - a[fin & ~built].mean(),
                     "km2_zones_chaudes_P90": hot.sum() * res * res / 1e6, "n_zones_chaudes_4px": int((sizes >= 4).sum()),
                     "centre_chaleur_x": hx, "centre_chaleur_y": hy, "centre_lon": lon, "centre_lat": lat})
    with open(OUT / "indicateurs_1984_2024.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0])); wr.writeheader()
        wr.writerows([{k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()} for r in rows])
    lut = np.load(ROOT / "results/thermal/legend_lut.npz")
    scene = {"dates": dates, "grid": {"x0": gx0, "y1": gy1, "res": res, "nx": nx, "ny": ny},
             "terrain": {"nx": n2, "ny": n1},
             "lut": {"v": lut["v"][::4].round(3).tolist(), "rgb": lut["rgb"][::4].tolist()},
             "ind": [{k: (round(float(v), 3) if not isinstance(v, str) else v) for k, v in r.items()} for r in rows],
             "built_cover_cells": int(built.sum())}
    (WEB / "scene.json").write_text(json.dumps(scene))
    print(json.dumps(rows, indent=1, default=float))
    print("terrain", n2, n1, "buildings tris", len(P) // 3)


if __name__ == "__main__":
    main()
