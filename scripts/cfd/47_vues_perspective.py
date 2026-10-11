"""Step 47 - perspective views of the CFD results in the streets (three.js, headless Chromium).

For each sector and scenario:
  * aerial oblique view from upwind,
  * street-level views (eye 1.7 m above the ground) along named streets (Overture/OSM):
      CENTRE_VILLE : boulevard Larbi Ben M'hidi, rue Emir Abdelkader
      GAMBETTA     : edge of the coastal escarpment (acceleration band), looking downwind
      BD_DES_LIONS : boulevard des Lions, towards the roundabout
Scene: terrain (maquette), real-footprint buildings of the CFD model, ground coloured by the
computed pedestrian wind speed U/Uref at 2 m (5 m cells), 3D streamlines (tubes) coloured by U.

  python 47_vues_perspective.py <post_dir> <scenario_sid> <out_dir>
"""
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.interpolate import RegularGridInterpolator

ROOT = Path(__file__).resolve().parents[2]
GEO = ROOT / "results/cfd/geometrie"
SECT = json.loads((ROOT / "results/cfd/secteurs.json").read_text())
NODE = Path("/home/user/work/video/node_modules")
# camera paths (model metres): street axes from the Overture/OSM street centroids
STREETS = {
    "CENTRE_VILLE": [("Boulevard Larbi Ben M'hidi", [(-962, 277), (-854, 301), (-659, 346)]),
                     ("Rue Émir Abdelkader", [(-1140, 412), (-1056, 309), (-1035, 285)])],
    "GAMBETTA": [("Rebord de l'escarpement (Gambetta)", [(700, 1500), (1200, 1650), (1700, 1800)])],
    "BD_DES_LIONS": [("Boulevard des Lions", [(5760, 1428), (6500, 1050), (7366, 601)])],
}


def to3(P):
    P = np.asarray(P, np.float64)
    return np.c_[P[:, 0], P[:, 2], -P[:, 1]].astype(np.float32)


def stl_tris(path):
    txt = Path(path).read_text().split("vertex")[1:]
    return np.array([list(map(float, t.split()[:3])) for t in txt]).reshape(-1, 3, 3)


def main():
    post, sid, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]); out.mkdir(parents=True, exist_ok=True)
    meta = json.loads((post / f"meta_{sid}.json").read_text()); uref = meta["U10_ref_ms"]
    ped = pd.read_csv(post / f"champ_pieton_{sid}.csv")
    sl = pd.read_csv(post / f"lignes_courant_{sid}.csv")
    g = np.load(GEO / "terrain_grid.npz"); xs, ys, Z = g["xs"], g["ys"], g["Z"]
    zg = RegularGridInterpolator((ys, xs), Z, bounds_error=False, fill_value=0)
    web = out / "web"; web.mkdir(exist_ok=True)
    shutil.copy(Path(__file__).parent / "perspective" / "index.html", web / "index.html")
    shutil.copy(Path(__file__).parent / "perspective" / "render.mjs", web / "render.mjs")
    if not (web / "node_modules").exists():
        (web / "node_modules").symlink_to(NODE)
    shots = []
    a = math.radians(meta["dir_from_deg"]); flow = np.array([-math.sin(a), -math.cos(a)])
    for k, s in SECT.items():
        b = s["model_box_m"]; m = 350
        # terrain crop
        ix = (xs > b[0] - m) & (xs < b[2] + m); iy = (ys > b[1] - m) & (ys < b[3] + m)
        X, Y = np.meshgrid(xs[ix], ys[iy]); P = np.c_[X.ravel(), Y.ravel(), Z[np.ix_(iy, ix)].ravel()]
        n2 = ix.sum(); i = (np.arange(iy.sum() - 1)[:, None] * n2 + np.arange(n2 - 1)[None, :]).ravel()
        to3(P).tofile(web / f"{k}_terrain_pos.bin")
        np.r_[np.c_[i, i + n2, i + 1].ravel(), np.c_[i + 1, i + n2, i + n2 + 1].ravel()].astype(np.uint32).tofile(web / f"{k}_terrain_idx.bin")
        to3(stl_tris(Path(meta["geo_dir"]) / f"buildings_{k}.stl").reshape(-1, 3)).tofile(web / f"{k}_bld.bin")
        d = ped[ped.secteur == k]
        # continuous draped surface of the pedestrian field: regular 5 m grid, a quad where its 4 corners are fluid
        gx = np.round((d.x.values - b[0]) / 5).astype(int); gy = np.round((d.y.values - b[1]) / 5).astype(int)
        nxg, nyg = gx.max() + 1, gy.max() + 1
        idx = -np.ones((nyg, nxg), np.int64); idx[gy, gx] = np.arange(len(d))
        a_, b_, c_, d_ = idx[:-1, :-1], idx[:-1, 1:], idx[1:, 1:], idx[1:, :-1]
        ok = (a_ >= 0) & (b_ >= 0) & (c_ >= 0) & (d_ >= 0)
        T = np.r_[np.c_[a_[ok], d_[ok], c_[ok]], np.c_[a_[ok], c_[ok], b_[ok]]].astype(np.uint32)
        zt = zg(np.c_[d.y.values, d.x.values])
        P = np.c_[d.x.values, d.y.values, np.maximum(d.z.values - 1.6, zt + 0.4)]
        np.c_[to3(P), (d.U.values / uref).astype(np.float32)].astype(np.float32).tofile(web / f"{k}_ped.bin")
        T.tofile(web / f"{k}_ped_idx.bin")
        L = sl[sl.secteur == k]
        lines = [np.c_[grp.x, grp.y, grp.z, grp.U / uref] for _, grp in L.groupby("id")]
        hseed = [float(l_[0, 2] - zg([[l_[0, 1], l_[0, 0]]])[0]) for l_ in lines]
        # streamlines: for each line a header row (n, 0, 0, 0) followed by n rows (x, y, z, U/Uref)
        blocks = [np.vstack([np.array([[len(l_), 1 if h > 20 else 0, 0, 0]]), l_]) for l_, h in zip(lines, hseed)] or [np.zeros((0, 4))]
        np.concatenate(blocks).astype(np.float32).tofile(web / f"{k}_sl.bin")
        # cameras: aerial oblique from upwind, then street level along each named street
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        R = 0.9 * max(b[2] - b[0], b[3] - b[1])
        cam = (cx - flow[0] * R, cy - flow[1] * R, float(zg([[cy, cx]])[0]) + 0.75 * R)
        shots.append({"sector": k, "name": f"{k}_aerienne", "label": f"{k.replace('_', ' ')} — vue aérienne oblique depuis l'amont",
                      "cam": to3([cam])[0].tolist(), "tgt": to3([(cx, cy, float(zg([[cy, cx]])[0]))])[0].tolist(), "fov": 45})
        for j, (nm, pts) in enumerate(STREETS[k]):
            p0, p1 = np.array(pts[0], float), np.array(pts[-1], float)
            dvec = (p1 - p0) / np.linalg.norm(p1 - p0)
            for tag, (src, dst) in (("rue", (p0 - 15 * dvec, p1)), ("rue_inverse", (p1 + 15 * dvec, p0))):
                z0 = float(zg([[src[1], src[0]]])[0]) + 1.7; z1 = float(zg([[dst[1], dst[0]]])[0]) + 6.0
                shots.append({"sector": k, "name": f"{k}_{tag}_{j}", "label": f"{nm} — vue piétonne (œil à 1,7 m)", "street": True,
                              "cam": to3([(src[0], src[1], z0)])[0].tolist(), "tgt": to3([(dst[0], dst[1], z1)])[0].tolist(), "fov": 70})
            mid = (p0 + p1) / 2; side = np.array([-dvec[1], dvec[0]])
            c2 = mid - 220 * dvec + 120 * side
            shots.append({"sector": k, "name": f"{k}_rue_haute_{j}", "label": f"{nm} — perspective à 60 m",
                          "cam": to3([(c2[0], c2[1], float(zg([[c2[1], c2[0]]])[0]) + 60)])[0].tolist(),
                          "tgt": to3([(mid[0], mid[1], float(zg([[mid[1], mid[0]]])[0]))])[0].tolist(), "fov": 55})
    scene = {"sid": sid, "uref": uref, "dir": meta["dir_from_deg"], "solver": meta["solver"], "cells": meta.get("mesh_cells"),
             "shots": shots, "sectors": list(SECT)}
    (web / "scene.json").write_text(json.dumps(scene))
    srv = subprocess.Popen([sys.executable, "-m", "http.server", "8771", "--bind", "127.0.0.1"], cwd=web,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        r = subprocess.run(["node", "render.mjs", str(out)], cwd=web, capture_output=True, text=True, timeout=7200)
        print(r.stdout[-2000:], r.stderr[-2000:])
    finally:
        srv.terminate()
    print("views:", sorted(p.name for p in out.glob("*.png")))


if __name__ == "__main__":
    main()
