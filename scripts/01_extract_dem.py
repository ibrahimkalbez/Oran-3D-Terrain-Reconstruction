"""Step 2a - Extract the Oran topography from the Copernicus GLO-30 DEM.

Crops the Copernicus tile to the Oran area of interest, reprojects it to
UTM zone 30N (EPSG:32630, metres) and writes a GeoTIFF plus statistics and
a hillshade preview.

Input : data/copernicus/Copernicus_DSM_N35_W001.tif
        (https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N35_00_W001_00_DEM/...)
Output: results/oran_dem_utm30n.tif, results/oran_dem_stats.json,
        results/oran_dem_hillshade.png

Usage : python scripts/01_extract_dem.py [--bbox W S E N] [--res 30]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject, transform
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/copernicus/Copernicus_DSM_N35_W001.tif"
OUT = ROOT / "results"
DST_CRS = "EPSG:32630"
# Oran city, port, Santa Cruz / Murdjadjo massif, Es Senia (lon/lat WGS84)
DEFAULT_BBOX = (-0.72, 35.64, -0.56, 35.76)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bbox", nargs=4, type=float, default=DEFAULT_BBOX)
    p.add_argument("--res", type=float, default=30.0, help="output pixel size (m)")
    a = p.parse_args()
    OUT.mkdir(exist_ok=True)

    # Axis-aligned UTM rectangle inscribed in the lon/lat bbox (no NaN corners)
    W, S, E, N = a.bbox
    xs, ys = transform("EPSG:4326", DST_CRS, [W, W, E, E], [S, N, S, N])
    x0 = np.ceil(max(xs[0], xs[1]) / a.res) * a.res
    x1 = np.floor(min(xs[2], xs[3]) / a.res) * a.res
    y0 = np.ceil(max(ys[0], ys[2]) / a.res) * a.res
    y1 = np.floor(min(ys[1], ys[3]) / a.res) * a.res
    w, h = int((x1 - x0) / a.res), int((y1 - y0) / a.res)
    dt = rasterio.Affine(a.res, 0, x0, 0, -a.res, y1)

    with rasterio.open(SRC) as src:
        m = 0.01  # source margin (deg) so bilinear sampling has neighbours
        win = from_bounds(W - m, S - m, E + m, N + m, transform=src.transform)
        win = win.round_offsets().round_lengths()
        data = src.read(1, window=win).astype("float32")
        dem = np.full((h, w), np.nan, "float32")
        reproject(data, dem, src_transform=src.window_transform(win), src_crs=src.crs,
                  dst_transform=dt, dst_crs=DST_CRS, resampling=Resampling.bilinear,
                  dst_nodata=np.nan)
    assert not np.isnan(dem).any()
    # Sea surface in a DSM is noisy around 0 m: clamp to 0
    dem = np.maximum(dem, 0.0)

    prof = dict(driver="GTiff", height=dem.shape[0], width=dem.shape[1], count=1,
                dtype="float32", crs=DST_CRS, transform=dt, compress="deflate")
    with rasterio.open(OUT / "oran_dem_utm30n.tif", "w", **prof) as dst:
        dst.write(dem, 1)

    stats = {
        "source": "Copernicus DEM GLO-30 (DSM), tile N35 W001",
        "bbox_wgs84": list(a.bbox),
        "crs": DST_CRS,
        "pixel_size_m": a.res,
        "grid": list(dem.shape),
        "origin_utm_top_left": [dt.c, dt.f],
        "extent_m": [dem.shape[1] * a.res, dem.shape[0] * a.res],
        "elev_min_m": float(dem.min()),
        "elev_max_m": float(dem.max()),
        "elev_mean_m": float(dem.mean()),
        "sea_fraction": float((dem <= 0.5).mean()),
    }
    (OUT / "oran_dem_stats.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LightSource
    ls = LightSource(azdeg=315, altdeg=45)
    rgb = ls.shade(dem, cmap=plt.cm.terrain, vert_exag=2, dx=a.res, dy=a.res, blend_mode="soft")
    ext = [0, dem.shape[1] * a.res / 1000, 0, dem.shape[0] * a.res / 1000]
    fig, ax = plt.subplots(figsize=(9, 8))
    ax.imshow(rgb, extent=ext)
    ax.set(title="Oran - Copernicus GLO-30 (UTM 30N)", xlabel="km", ylabel="km")
    fig.savefig(OUT / "oran_dem_hillshade.png", dpi=130, bbox_inches="tight")


if __name__ == "__main__":
    main()
