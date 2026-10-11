"""Step 30 - extract the 8 dated panels of the source figure (1984 -> 2024) with the
same legend calibration as step 20, locate each panel by maximising the overlap of
its legend-coloured pixels with the 2024 study area (pure translation, IoU reported),
and put every date on the 2024 georeferenced 40 m grid of the maquette.

Outputs (results/thermal_evolution/):
  stack_px.npz                  values per date, 2024-panel pixel geometry (NaN = NoData)
  stack_grid.npz                values per date on the model grid (+ transform)
  Oran_LST_1984_2024_stack.tif  8-band GeoTIFF on the model grid (band i = DATES[i])
  panel_<date>.png              crop of each original panel (for the 2D side of the video)
  extract_all_report.json
Annotation pixels (2023 circles/numbers) are NoData; the 2004 stripes belong to the image.
"""
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/thermal"))
_s = importlib.util.spec_from_file_location("e20", ROOT / "scripts/thermal/20_extract_panel_2024.py")
m20 = importlib.util.module_from_spec(_s); _s.loader.exec_module(m20)
from thermal_common import DATA, load_config  # noqa: E402

W2024 = ROOT / "results/thermal"
OUT = ROOT / "results/thermal_evolution"
DATES = ["1984-07-28", "2000-08-01", "2004-06-25", "2010-07-12", "2015-07-18", "2020-07-31",
         "2023-07-24", "2024-07-10"]
APPROX = {"1984-07-28": (24, 105), "2000-08-01": (676, 105), "2004-06-25": (1328, 105),
          "2010-07-12": (24, 636), "2015-07-18": (676, 636), "2020-07-31": (1328, 636),
          "2023-07-24": (24, 1168), "2024-07-10": (676, 1168)}
SEARCH = 14


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    img = Image.open(DATA / cfg["source_image"]).convert("RGB")
    full = np.asarray(img, float)
    lut_v, lut_rgb = m20.legend_lut(img, cfg)
    lut_lab = m20.rgb2lab(lut_rgb.astype(float))
    x0, y0, x1, y1 = cfg["panel_px"]; pw, ph = x1 - x0, y1 - y0
    area = np.load(W2024 / "area_mask_px.npy")
    k = int(cfg.get("edge_erode_px", 0))
    inner = ndimage.binary_erosion(area, iterations=k) if k else area

    def convert(P):
        lab = m20.rgb2lab(P.reshape(-1, 3))
        best = np.empty(len(lab), np.int64); dist = np.empty(len(lab))
        for s in range(0, len(lab), 200_000):
            d = np.linalg.norm(lab[s:s + 200_000, None, :] - lut_lab[None], axis=2)
            best[s:s + 200_000] = d.argmin(1); dist[s:s + 200_000] = d.min(1)
        v = lut_v[best].astype(np.float32)
        v[dist > cfg["max_color_dist"]] = np.nan
        return v.reshape(P.shape[:2]), dist.reshape(P.shape[:2])

    stack, rep = {}, {}
    for date in DATES:
        ax, ay = APPROX[date]
        H, W = full.shape[:2]
        bx0, by0 = max(0, ax - SEARCH), max(0, ay - SEARCH)
        big = full[by0:min(H, ay + ph + SEARCH), bx0:min(W, ax + pw + SEARCH)]
        vb, _ = convert(big)
        ok = np.isfinite(vb).astype(np.float32)
        best = (-1.0, 0, 0)
        for dy in range(0, ok.shape[0] - ph + 1):
            for dx in range(0, ok.shape[1] - pw + 1):
                sc = float((ok[dy:dy + ph, dx:dx + pw] * area).sum() - (ok[dy:dy + ph, dx:dx + pw] * ~area).sum())
                if sc > best[0]:
                    best = (sc, dx, dy)
        px0, py0 = bx0 + best[1], by0 + best[2]
        crop = full[py0:py0 + ph, px0:px0 + pw]
        Image.fromarray(crop.astype(np.uint8)).save(OUT / f"panel_{date}.png")
        v, dist = convert(crop)
        okp = np.isfinite(v)
        iou = float((okp & area).sum() / (okp | area).sum())
        v[~inner] = np.nan
        nod = inner & ~np.isfinite(v)
        stack[date] = v
        rep[date] = {"panel_px": [int(px0), int(py0), int(px0 + pw), int(py0 + ph)],
                     "iou_coloured_vs_2024_area": iou, "valid_px": int(np.isfinite(v).sum()),
                     "nodata_inside_area_pct": float(100 * nod.sum() / inner.sum()),
                     "median_dE": float(np.median(dist[np.isfinite(v)]))}
        print(date, json.dumps(rep[date]))
    np.savez_compressed(OUT / "stack_px.npz", **stack)
    R = json.loads((W2024 / "registration_2024.json").read_text())
    A = np.array(R["px_to_model"]); Minv = np.linalg.inv(A[:, 1:])
    with rasterio.open(W2024 / "Oran_Thermal_2024.tif") as ds:
        prof = ds.profile; tr = ds.transform; ny, nx = ds.height, ds.width
    X, Y = np.meshgrid(tr.c + (np.arange(nx) + .5) * tr.a, tr.f + (np.arange(ny) + .5) * tr.e)
    sp = (np.c_[X.ravel(), Y.ravel()] - A[:, 0]) @ Minv.T
    c, r = np.floor(sp[:, 0]).astype(int), np.floor(sp[:, 1]).astype(int)
    ins = (c >= 0) & (c < pw) & (r >= 0) & (r < ph)
    bands = []
    for date in DATES:
        o = np.full(nx * ny, np.nan, np.float32); o[ins] = stack[date][r[ins], c[ins]]
        bands.append(o.reshape(ny, nx))
    B = np.stack(bands)
    prof.update(count=len(DATES))
    with rasterio.open(OUT / "Oran_LST_1984_2024_stack.tif", "w", **prof) as dst:
        dst.write(np.where(np.isfinite(B), B, -9999).astype(np.float32))
        for i, d in enumerate(DATES, 1):
            dst.set_band_description(i, f"LST {d} degC (reconstruit depuis la figure)")
    np.savez_compressed(OUT / "stack_grid.npz", dates=np.array(DATES), values=B, transform=np.array(tr)[:6])
    (OUT / "extract_all_report.json").write_text(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
