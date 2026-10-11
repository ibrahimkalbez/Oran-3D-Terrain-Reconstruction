"""Step 20 - extract ONLY the 10/07/2024 panel from the reference thermal map
and convert its colours back to values using the document's own legend.

Inputs  (data/thermal/):
  source image (config "source_image") - the multi-date thermal figure
  thermal_config.json:
    panel_px   [x0, y0, x1, y1]  crop of the 10/07/2024 map panel (map frame only)
    legend_px  [x0, y0, x1, y1]  crop of the colour bar (inside the coloured part)
    legend_min / legend_max      values printed at the ends of the bar (e.g. 20 / 45)
    legend_low_end               "bottom" | "top" | "left" | "right": where the minimum is
    max_color_dist               max RGB distance to the legend to accept a pixel (default 40)
    mask_polygon_px (optional)   study-area outline in panel pixels; pixels outside -> NoData

Method: the colour bar is sampled along its long axis (median across its width)
-> lookup table colour -> value. Every panel pixel is assigned the value of the
nearest legend colour (CIE-Lab distance). Pixels whose colour is far from every
legend colour (white background, black outlines, labels, north arrow, scale bar)
become NoData, then are NOT interpolated (no invented data).

Outputs (results/thermal/): panel_2024.png, values_px.npy (float32, NaN = NoData),
legend_lut.npz, extract_report.json
"""
import json

import numpy as np
from PIL import Image, ImageDraw

from thermal_common import DATA, WORK, load_config


def rgb2lab(rgb):
    c = np.asarray(rgb, float) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ M.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def legend_lut(img, cfg):
    x0, y0, x1, y1 = cfg["legend_px"]
    bar = np.asarray(img.crop((x0, y0, x1, y1)).convert("RGB"), float)
    vertical = bar.shape[0] >= bar.shape[1]
    prof = np.median(bar, axis=1 if vertical else 0)          # colour along the long axis
    low = cfg.get("legend_low_end", "bottom" if vertical else "left")
    if low in ("bottom", "right"):
        prof = prof[::-1]
    v = np.linspace(cfg["legend_min"], cfg["legend_max"], len(prof))
    return v, prof.round().astype(np.uint8)


def main():
    cfg = load_config()
    WORK.mkdir(parents=True, exist_ok=True)
    img = Image.open(DATA / cfg["source_image"]).convert("RGB")
    lut_v, lut_rgb = legend_lut(img, cfg)
    panel = img.crop(tuple(cfg["panel_px"]))
    panel.save(WORK / "panel_2024.png")
    P = np.asarray(panel, float)
    lab = rgb2lab(P.reshape(-1, 3))
    lut_lab = rgb2lab(lut_rgb.astype(float))
    best = np.empty(len(lab), np.int64); dist = np.empty(len(lab))
    for s in range(0, len(lab), 200_000):                      # chunked nearest-colour search
        d = np.linalg.norm(lab[s:s + 200_000, None, :] - lut_lab[None], axis=2)
        best[s:s + 200_000] = d.argmin(1); dist[s:s + 200_000] = d.min(1)
    val = lut_v[best].astype(np.float32)
    val[dist > cfg.get("max_color_dist", 12.0)] = np.nan       # Lab distance (ΔE)
    val = val.reshape(P.shape[:2])
    if cfg.get("mask_polygon_px"):
        m = Image.new("L", panel.size, 0)
        ImageDraw.Draw(m).polygon([tuple(p) for p in cfg["mask_polygon_px"]], fill=1)
        val[np.asarray(m) == 0] = np.nan
    np.save(WORK / "values_px.npy", val)
    np.savez(WORK / "legend_lut.npz", v=lut_v, rgb=lut_rgb)
    ok = np.isfinite(val)
    rep = {"source_image": cfg["source_image"], "panel_px": cfg["panel_px"],
           "panel_size_px": list(panel.size), "legend_px": cfg["legend_px"],
           "legend_range": [cfg["legend_min"], cfg["legend_max"]], "legend_samples": len(lut_v),
           "legend_step": float(np.diff(lut_v).mean()),
           "valid_pixels": int(ok.sum()), "nodata_pixels": int((~ok).sum()),
           "median_color_error_dE": float(np.median(dist[ok.ravel()])) if ok.any() else None,
           "value_min": float(np.nanmin(val)) if ok.any() else None,
           "value_max": float(np.nanmax(val)) if ok.any() else None}
    (WORK / "extract_report.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
