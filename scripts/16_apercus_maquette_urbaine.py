"""Previews of the detailed urban maquette (script 15): real z-buffer render
(three.js in headless Chromium, sun + soft shadows) of the exact Rhino
geometry captured by script 15 (results/cache/preview_15.pkl); the furniture
blocks are drawn from their insertion points.

Needs node + `npm install three playwright-core` in scripts/preview/ (or set
NODE_PATH) and the Playwright Chromium (/opt/pw-browsers).
Output: results/apercu_urbain_<zone>_<view>.png
"""
import http.server
import json
import math
import os
import pickle
import shutil
import subprocess
import threading
from functools import partial
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PRE = ROOT / "scripts/preview"
WORK = ROOT / "results/cache/preview_web"
CROWN, TRUNK, STEEL = (104, 134, 88), (102, 92, 80), (62, 64, 67)


def furniture_tris(pts):
    """Low-poly copies of the Rhino blocks (same dimensions)."""
    T, C = [], []

    def prism(x, y, z, poly, z0, z1, col):
        n = len(poly)
        b = [(x + p[0], y + p[1]) for p in poly]
        for i in range(n):
            j = (i + 1) % n
            a0, a1 = (*b[i], z + z0), (*b[j], z + z0)
            t0, t1 = (*b[i], z + z1), (*b[j], z + z1)
            T.extend([(a0, a1, t1), (a0, t1, t0)])
            C.extend([col, col])
        for i in range(1, n - 1):
            T.append(((*b[0], z + z1), (*b[i], z + z1), (*b[i + 1], z + z1)))
            C.append(col)

    def rect(hx, hy, th, dx=0.0, dy=0.0):
        c, s = math.cos(th), math.sin(th)
        return [(c * (a + dx) - s * (bb + dy), s * (a + dx) + c * (bb + dy))
                for a, bb in [(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)]]

    def circle(r, n=8):
        return [(r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n)) for k in range(n)]

    def crown(x, y, z, r, z0, z1):
        n, m = 8, 4
        rings = []
        for i in range(m + 1):
            ph = math.pi * i / m
            zz = z + z0 + (z1 - z0) * (1 - math.cos(ph)) / 2
            rr = r * math.sin(ph)
            rings.append([(x + rr * math.cos(2 * math.pi * k / n), y + rr * math.sin(2 * math.pi * k / n), zz)
                          for k in range(n)])
        for i in range(m):
            for k in range(n):
                j = (k + 1) % n
                T.extend([(rings[i][k], rings[i][j], rings[i + 1][j]), (rings[i][k], rings[i + 1][j], rings[i + 1][k])])
                C.extend([CROWN, CROWN])

    for name, x, y, z, th in pts:
        if name.startswith("ARBRE"):
            big = name == "ARBRE_PARC"
            prism(x, y, z, circle(0.18 if big else 0.14, 6), 0, 3.0 if big else 2.6, TRUNK)
            crown(x, y, z, 3.0 if big else 2.2, 2.8 if big else 2.6, 8.0 if big else 6.6)
        elif name == "LAMPADAIRE":
            prism(x, y, z, circle(0.09, 6), 0, 8.0, STEEL)
            prism(x, y, z, rect(0.8, 0.04, th, 0.8), 7.85, 7.95, STEEL)
            prism(x, y, z, rect(0.35, 0.18, th, 1.55), 7.70, 7.85, (230, 230, 226))
        elif name == "POTELET":
            prism(x, y, z, circle(0.06, 6), 0, 0.9, STEEL)
        elif name == "PANNEAU_PASSAGE_PIETON":
            prism(x, y, z, circle(0.035, 5), 0, 2.6, (150, 150, 150))
            prism(x, y, z, rect(0.3, 0.02, th), 2.0, 2.6, (236, 236, 232))
        elif name == "BANC":
            prism(x, y, z, rect(0.9, 0.22, th), 0.42, 0.48, (122, 104, 84))
            prism(x, y, z, rect(0.9, 0.03, th, 0, 0.21), 0.48, 0.85, (122, 104, 84))
        elif name == "ABRIBUS":
            prism(x, y, z, rect(2.1, 0.85, th), 2.45, 2.55, STEEL)
            prism(x, y, z, rect(2.0, 0.02, th, 0, 0.72), 0.1, 2.45, (190, 205, 210))
    return np.array(T, float).reshape(-1, 3, 3), np.array(C, float).reshape(-1, 3)


def export(zone, P, margin=150.0):
    x0, y0, x1, y1 = P["win"][zone]
    tris, cols = [], []
    for t, c, _ in P["tri"][zone]:
        ok = ((t[:, :, 0] > x0 - margin).all(1) & (t[:, :, 0] < x1 + margin).all(1) &
              (t[:, :, 1] > y0 - margin).all(1) & (t[:, :, 1] < y1 + margin).all(1))
        tris.append(t[ok])
        cols.append(np.repeat(np.array(c, float)[None], ok.sum(), 0))
    ft, fc = furniture_tris(P["pts"][zone])
    tris.append(ft)
    cols.append(fc)
    T = np.concatenate(tris)
    C = np.concatenate(cols)
    c0 = np.array([(x0 + x1) / 2, (y0 + y1) / 2, np.percentile(T[:, :, 2], 5)])
    pos = (T - c0)[:, :, [0, 2, 1]] * np.array([1, 1, -1])     # three.js: y up, z = -north
    col = np.repeat(C[:, None, :] / 255.0, 3, 1)
    d = WORK / zone
    d.mkdir(parents=True, exist_ok=True)
    pos.astype(np.float32).tofile(d / "pos.bin")
    col.astype(np.float32).tofile(d / "col.bin")
    half = max(x1 - x0, y1 - y0) / 2
    return {"zone": zone, "half": half, "hx": (x1 - x0) / 2, "hy": (y1 - y0) / 2, "ntri": int(len(T))}


def main():
    P = pickle.load(open(ROOT / "results/cache/preview_15.pkl", "rb"))
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    for f in ("index.html", "render.mjs"):
        shutil.copy(PRE / f, WORK / f)
    if (PRE / "node_modules").exists():
        os.symlink(PRE / "node_modules", WORK / "node_modules")
    zones = [export(z, P) for z in P["win"]]
    # views: (name, camera azimuth deg from south, elevation deg, distance factor, ortho)
    views = {"perspective": (-35, 32, 1.55, False), "plan": (0, 90, 1.0, True), "rue": (-20, 14, 0.55, False)}
    jobs = []
    for z in zones:
        for vn, (az, el, k, ortho) in views.items():
            if vn == "rue" and z["zone"] != "carrefour":
                continue
            jobs.append({"zone": z["zone"], "view": vn, "az": az, "el": el, "dist": z["half"] * 2.2 * k,
                         "half": z["half"], "hx": z["hx"], "hy": z["hy"], "ortho": ortho,
                         "out": str(ROOT / f"results/apercu_urbain_{z['zone']}_{vn}.png")})
    (WORK / "jobs.json").write_text(json.dumps(jobs))
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(WORK))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8766), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    env = dict(os.environ, NODE_PATH=str(PRE / "node_modules"))
    subprocess.run(["node", str(WORK / "render.mjs")], cwd=WORK, env=env, check=True)
    srv.shutdown()
    print("\n".join(j["out"] for j in jobs))


if __name__ == "__main__":
    main()
