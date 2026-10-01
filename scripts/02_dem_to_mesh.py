"""Step 2b - Convert the Oran DEM into 3D terrain meshes.

Produces, from results/oran_dem_utm30n.tif:
  results/oran_terrain_surface.obj   open surface, real metres, local origin
                                     (import in Blender / Rhino for fusion)
  results/oran_terrain_solid.stl     closed watertight block (surface + walls
                                     + flat base), real metres

Coordinates are local: X/Y = UTM 30N minus the grid top-left origin stored in
results/oran_dem_stats.json, so they stay small enough for Rhino/Blender
precision. Add that origin back to georeference.

Usage : python scripts/02_dem_to_mesh.py [--step 1] [--base 20]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
import trimesh

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results"


def grid_faces(h, w, offset=0):
    i = np.arange(h - 1)[:, None] * w + np.arange(w - 1)[None, :]
    i = i.ravel() + offset
    return np.vstack([np.c_[i, i + w, i + 1], np.c_[i + 1, i + w, i + w + 1]])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--step", type=int, default=1, help="keep 1 pixel out of N")
    p.add_argument("--base", type=float, default=20.0, help="base thickness below 0 m (m)")
    a = p.parse_args()

    with rasterio.open(OUT / "oran_dem_utm30n.tif") as r:
        z = r.read(1)[::a.step, ::a.step].astype("float64")
        px = r.res[0] * a.step
    h, w = z.shape
    xs = np.arange(w) * px
    ys = (h - 1 - np.arange(h)) * px          # north up, origin bottom-left
    X, Y = np.meshgrid(xs, ys)
    top = np.c_[X.ravel(), Y.ravel(), z.ravel()]
    tf = grid_faces(h, w)

    surf = trimesh.Trimesh(top, tf, process=False)
    surf.export(OUT / "oran_terrain_surface.obj")

    # Closed solid: duplicate grid at z = -base, stitch border walls
    bot = top.copy()
    bot[:, 2] = -a.base
    n = len(top)
    bf = grid_faces(h, w, offset=n)[:, ::-1]
    idx = np.arange(n).reshape(h, w)
    ring = np.r_[idx[0, :], idx[1:, -1], idx[-1, -2::-1], idx[-2:0:-1, 0]]
    a_, b_ = ring, np.roll(ring, -1)
    walls = np.vstack([np.c_[a_, a_ + n, b_], np.c_[b_, a_ + n, b_ + n]])
    solid = trimesh.Trimesh(np.vstack([top, bot]), np.vstack([tf, bf, walls]), process=True)
    if solid.volume < 0:
        solid.invert()
    solid.export(OUT / "oran_terrain_solid.stl")

    info = {
        "grid": [h, w], "pixel_m": px,
        "surface_triangles": len(tf),
        "solid_triangles": len(solid.faces),
        "solid_watertight": bool(solid.is_watertight),
        "solid_extent_m": solid.extents.round(1).tolist(),
    }
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
