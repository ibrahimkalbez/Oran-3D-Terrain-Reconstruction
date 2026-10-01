"""White maquette with ALL details: start from the detailed relief file
(05 + 05b + 05c: buildings, roads, rail, land use... on the exact relief) and
  * paint every layer and object white,
  * replace the terrain surface by a closed solid TERRAIN + SOCLE block
    (terrain on top, vertical walls, flat bottom SOCLE_M below sea level),
  * add exact contour lines: intersection of the terrain triangulation with
    the planes z = 10 m * k (layer COURBES_NIVEAU_10m) and every 50 m
    (COURBES_NIVEAU_50m), joined into continuous polylines lying exactly on
    the terrain surface,
  * final clean-up of every mesh (weld at 1e-6 m, degenerate/duplicate faces)
    and Rhino validity check.
Usage: python scripts/10_maquette_blanche_complete.py
Output: results/Oran_maquette_blanche_complete_Rhino8.3dm"""
import importlib.util
import json
from pathlib import Path

import numpy as np
import rhino3dm as r3

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("b", Path(__file__).parent / "05_build_oran_relief.py")
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
SRC = ROOT / "results/Oran_relief_Copernicus_Rhino8.3dm"
OUT = ROOT / "results/Oran_maquette_blanche_complete_Rhino8.3dm"
H, MASTER = 10.0, 50.0
SOCLE_M = 60.0
WHITE = (255, 255, 255, 255)


def clean(V, F):
    return b.clean_for_rhino(np.round(V, 6), F)


def terrain_block(tx, ty, tz):
    X, Y = np.meshgrid(tx, ty)
    ny, nx = tz.shape
    top = np.c_[X.ravel(), Y.ravel(), tz.ravel()]
    i = (np.arange(ny - 1)[:, None] * nx + np.arange(nx - 1)[None, :]).ravel()
    tf = np.vstack([np.c_[i, i + 1, i + nx], np.c_[i + 1, i + nx + 1, i + nx]])
    idx = np.arange(nx * ny).reshape(ny, nx)
    ring = np.r_[idx[0, :], idx[1:, -1], idx[-1, -2::-1], idx[-2:0:-1, 0]]
    n = len(top)
    bot = top[ring].copy(); bot[:, 2] = -SOCLE_M
    br = n + np.arange(len(ring)); c = n + len(ring)
    centre = np.r_[bot[:, :2].mean(0), -SOCLE_M]
    a_, b_ = ring, np.roll(ring, -1)
    ba, bb = br, np.roll(br, -1)
    F = np.vstack([tf, np.c_[a_, ba, b_], np.c_[b_, ba, bb], np.c_[np.full(len(ring), c), bb, ba]])
    return np.vstack([top, bot, centre]), F, tf, top


def contours(V, T, level):
    """Exact level-set of a triangle mesh -> list of polylines (Nx3)."""
    z = V[:, 2] - level
    z = np.where(z == 0, 1e-9, z)
    s = np.sign(z[T])
    cross = (s.min(1) < 0) & (s.max(1) > 0)
    Tc = T[cross]
    if not len(Tc):
        return []
    E = np.stack([Tc[:, [0, 1]], Tc[:, [1, 2]], Tc[:, [2, 0]]], 1)          # (n,3,2)
    sc = np.sign(z[E])
    ec = sc[:, :, 0] != sc[:, :, 1]                                        # 2 crossing edges per tri
    Es = np.sort(E, axis=2)
    seg = Es[ec].reshape(-1, 2, 2)                                         # (n, 2 edges, 2 verts)
    keys = seg[:, :, 0].astype(np.int64) * len(V) + seg[:, :, 1]
    uk, inv = np.unique(keys.ravel(), return_inverse=True)
    inv = inv.reshape(-1, 2)
    a, bb = uk // len(V), uk % len(V)
    t = z[a] / (z[a] - z[bb])
    P = V[a] + t[:, None] * (V[bb] - V[a])
    P[:, 2] = level
    # chain segments (each point has degree <= 2)
    nb = [[] for _ in range(len(uk))]
    for i, (p, q) in enumerate(inv.tolist()):
        nb[p].append(q); nb[q].append(p)
    seen = np.zeros(len(uk), bool)
    lines = []
    order = sorted(range(len(uk)), key=lambda k: len(nb[k]))               # open chains first
    for st in order:
        if seen[st]:
            continue
        chain = [st]; seen[st] = True; cur = st
        while True:
            nxt = [k for k in nb[cur] if not seen[k]]
            if not nxt:
                break
            cur = nxt[0]; seen[cur] = True; chain.append(cur)
        if len(nb[st]) == 2 and st in nb[chain[-1]] and len(chain) > 2:
            chain.append(st)                                               # closed ring
        if len(chain) >= 2:
            lines.append(P[chain])
    return lines


def main():
    d = np.load(ROOT / "results/cache/relief_parts.npz")
    tx, ty, tz = d["tx"], d["ty"], d["tz"]
    src = r3.File3dm.Read(str(SRC))
    out = r3.File3dm()
    out.Settings.ModelUnitSystem = src.Settings.ModelUnitSystem
    out.Settings.ModelAbsoluteTolerance = src.Settings.ModelAbsoluteTolerance
    out.Settings.EarthAnchorPoint = src.Settings.EarthAnchorPoint
    for i in range(len(src.Layers)):
        L = src.Layers[i]; n = r3.Layer()
        n.Name, n.Color, n.Visible = L.Name, WHITE, L.Visible
        if n.Name == "TERRAIN_COPERNICUS_GLO30":
            n.Name = "TERRAIN_ET_SOCLE"
        out.Layers.Add(n)

    def lay(name, col):
        L = r3.Layer(); L.Name = name; L.Color = col
        return out.Layers.Add(L)
    l10 = lay("COURBES_NIVEAU_10m", (190, 190, 190, 255))
    l50 = lay("COURBES_NIVEAU_50m", (120, 120, 120, 255))
    rep = {"objects": 0, "invalid": []}
    for o in src.Objects:
        g = o.Geometry
        at = o.Attributes
        lname = src.Layers[at.LayerIndex].Name
        if lname == "TERRAIN_COPERNICUS_GLO30":
            V, F, tf, top = terrain_block(tx, ty, tz)
            at.Name = f"Terrain Copernicus + socle {SOCLE_M:.0f} m"
            b.add_mesh(out, V, F, at)
            rep["terrain_block_faces"] = int(len(F))
            continue
        V = np.array([[p.X, p.Y, p.Z] for p in g.Vertices], np.float64)
        F = np.array([tuple(g.Faces[k]) for k in range(g.Faces.Count)], np.int64).reshape(-1, 4)
        V, F = clean(V, F)
        if len(F):
            b.add_mesh(out, V, F, at)
            rep["objects"] += 1
    # contour lines on the exact terrain surface
    nlines = 0
    for k in range(1, int(tz.max() // H) + 1):
        level = k * H
        for P in contours(top, tf, level):
            at = r3.ObjectAttributes()
            at.LayerIndex = l50 if level % MASTER == 0 else l10
            at.Name = f"Courbe {level:.0f} m"
            out.Objects.AddPolyline(r3.Polyline([r3.Point3d(*map(float, p)) for p in P]), at)
            nlines += 1
    rep["contour_polylines"] = nlines
    rep["contour_levels"] = int(tz.max() // H)
    for o in out.Objects:
        if isinstance(o.Geometry, r3.Mesh):
            ok, log = o.Geometry.IsValidWithLog
            if not ok:
                rep["invalid"].append(out.Layers[o.Attributes.LayerIndex].Name)
    out.Write(str(OUT), 8)
    rep["file_MB"] = round(OUT.stat().st_size / 1e6, 1)
    (ROOT / "results/maquette_blanche_report.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
