"""Shared settings for the 10/07/2024 thermal-map integration (scripts 20-24).

All parameters that depend on the source image (panel crop, legend bar,
ground control points) live in data/thermal/thermal_config.json so that the
scripts stay generic and every manual choice is traceable.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from oran_georef import LOCAL_CRS, ORIGIN_LAT, ORIGIN_LON  # noqa: E402,F401

DATA = Path(os.environ.get("THERMAL_DATA", ROOT / "data/thermal"))
CONFIG = DATA / "thermal_config.json"
WORK = Path(os.environ.get("THERMAL_WORK", ROOT / "results/thermal"))
PKG_NAME = "ORAN_THERMAL_2024_PACKAGE"
LAYER = "THERMAL_ORAN_2024_07_10"
DATE = "2024-07-10"


def load_config():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


# Thermal palette of the source map: blue (low) -> green -> yellow -> orange -> red (high).
# Used only when the legend bar of the document cannot be sampled.
FALLBACK_STOPS = [(0.00, (49, 54, 149)), (0.20, (69, 117, 180)), (0.40, (116, 196, 118)),
                  (0.55, (255, 255, 51)), (0.75, (253, 141, 60)), (1.00, (215, 25, 28))]


def palette(t, stops=FALLBACK_STOPS):
    """t in [0,1] -> uint8 RGB (piecewise linear)."""
    t = np.clip(np.asarray(t, float), 0, 1)
    xs = np.array([s[0] for s in stops])
    cs = np.array([s[1] for s in stops], float)
    return np.stack([np.interp(t, xs, cs[:, k]) for k in range(3)], -1).round().astype(np.uint8)


def lut_colors(values, lut_v, lut_rgb):
    """Map values to the RGB colours of the document legend (lut from script 20)."""
    v = np.asarray(values, float)
    return np.stack([np.interp(v, lut_v, lut_rgb[:, k]) for k in range(3)], -1).round().astype(np.uint8)


def hot_cool_zones(a, tr, top_n=8, min_px=4):
    """Connected areas above P90 (hot) / below P10 (cool) of raster a (NaN = NoData).
    Returns a list of dicts with kind, rank, mean, max/min, area, centroid and the boolean mask."""
    from scipy import ndimage
    v = a[np.isfinite(a)]
    out = []
    for kind, mask in (("chaude", a >= np.percentile(v, 90)), ("fraiche", a <= np.percentile(v, 10))):
        lab, n = ndimage.label(np.nan_to_num(mask, nan=0).astype(bool))
        if not n:
            continue
        sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
        for rank, i in enumerate(np.argsort(sizes)[::-1][:top_n], 1):
            if sizes[i] < min_px:
                break
            m = lab == i + 1
            r, c = ndimage.center_of_mass(m)
            x, y = tr * (c + 0.5, r + 0.5)
            vals = a[m]
            out.append({"kind": kind, "rank": rank, "mean": float(np.nanmean(vals)),
                        "extreme": float(np.nanmax(vals) if kind == "chaude" else np.nanmin(vals)),
                        "area_km2": float(sizes[i] * abs(tr.a * tr.e) / 1e6), "x": float(x), "y": float(y),
                        "mask": m})
    return out


def grid_cells(a, tr, size_m=1000.0, min_cover=0.25):
    """Statistics of raster a on a regular size_m grid aligned on the model origin."""
    px = abs(tr.a)
    ny, nx = a.shape
    X = tr.c + (np.arange(nx) + 0.5) * px
    Y = tr.f - (np.arange(ny) + 0.5) * px
    ix = np.floor(X / size_m).astype(int)
    iy = np.floor(Y / size_m).astype(int)
    IX, IY = np.meshgrid(ix, iy)
    ok = np.isfinite(a)
    cells = []
    full = (size_m / px) ** 2
    for gx in np.unique(IX[ok]):
        for gy in np.unique(IY[ok & (IX == gx)]):
            m = ok & (IX == gx) & (IY == gy)
            if m.sum() < min_cover * full:
                continue
            v = a[m]
            cells.append({"id": f"C{gx:+03d}_{gy:+03d}", "x0": gx * size_m, "y0": gy * size_m,
                          "size": size_m, "n": int(m.sum()), "cover": float(m.sum() / full),
                          "mean": float(v.mean()), "min": float(v.min()), "max": float(v.max()),
                          "median": float(np.median(v))})
    return cells


def weighted_median(v, w):
    o = np.argsort(v); cw = np.cumsum(w[o])
    return float(v[o][np.searchsorted(cw, cw[-1] / 2)])
