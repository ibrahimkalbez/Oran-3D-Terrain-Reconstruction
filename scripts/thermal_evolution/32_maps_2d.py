"""Step 32 - 2D georeferenced plans for the right side of the video (one per date,
same frame as the 3D maquette, model metres) + the 2024 - 1984 difference plan.

Outputs: results/thermal_evolution/web/plan_<i>.png, plan_diff.png, panel_<i>.png
"""
import json
import shutil
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import ListedColormap, TwoSlopeNorm  # noqa: E402
from scipy import ndimage  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results/thermal_evolution"
WEB = OUT / "web"


def main():
    d = np.load(OUT / "stack_grid.npz")
    dates = [str(x) for x in d["dates"]]; B = d["values"]; t = d["transform"]
    res, x0, y1 = t[0], t[2], t[5]
    ny, nx = B.shape[1:]
    ext = (x0, x0 + nx * res, y1 - ny * res, y1)
    lut = np.load(ROOT / "results/thermal/legend_lut.npz")
    cmap = ListedColormap(lut["rgb"] / 255.0)
    vmin, vmax = lut["v"][0], lut["v"][-1]
    for i, date in enumerate(dates):
        a = B[i]
        fig = plt.figure(figsize=(7.2, 5.0), dpi=100)
        ax = fig.add_axes([0.09, 0.08, 0.89, 0.84])
        ax.imshow(a, cmap=cmap, vmin=vmin, vmax=vmax, extent=ext, interpolation="nearest")
        p90 = np.nanpercentile(a, 90)
        hot = ndimage.binary_opening(np.nan_to_num(a, nan=0) >= p90)
        ax.contour(np.linspace(ext[0], ext[1], nx), np.linspace(ext[3], ext[2], ny), hot.astype(float),
                   levels=[0.5], colors="k", linewidths=0.7)
        ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3]); ax.set_aspect("equal")
        ax.set_xticks(np.arange(-8000, 18001, 4000)); ax.set_yticks(np.arange(-10000, 10001, 4000))
        ax.tick_params(labelsize=7); ax.grid(True, lw=0.3, color="0.6")
        ax.set_title(f"Plan 2D géoréférencé — {date[8:]}/{date[5:7]}/{date[:4]} — contour : zones ≥ P90 ({p90:.1f} °C)",
                     fontsize=8.5)
        ax.set_xlabel("X maquette (m)", fontsize=7); ax.set_ylabel("Y maquette (m)", fontsize=7)
        fig.savefig(WEB / f"plan_{i}.png"); plt.close(fig)
        shutil.copy(OUT / f"panel_{date}.png", WEB / f"panel_{i}.png")
    diff = B[-1] - B[0]
    fig = plt.figure(figsize=(7.2, 5.0), dpi=100)
    ax = fig.add_axes([0.09, 0.08, 0.80, 0.84])
    im = ax.imshow(diff, cmap="RdBu_r", norm=TwoSlopeNorm(0, -10, 15), extent=ext, interpolation="nearest")
    ax.set_aspect("equal"); ax.tick_params(labelsize=7); ax.grid(True, lw=0.3, color="0.6")
    ax.set_title(f"Différence 2024 − 1984 (°C) — moyenne {np.nanmean(diff):+.1f} °C", fontsize=8.5)
    cb = fig.colorbar(im, cax=fig.add_axes([0.91, 0.15, 0.02, 0.7])); cb.ax.tick_params(labelsize=7)
    fig.savefig(WEB / "plan_diff.png"); plt.close(fig)
    np.where(np.isfinite(diff), diff, -99).astype(np.float32).tofile(WEB / "lst_diff.bin")
    print(json.dumps({"diff_mean": float(np.nanmean(diff)), "diff_p90": float(np.nanpercentile(diff, 90))}))


if __name__ == "__main__":
    main()
