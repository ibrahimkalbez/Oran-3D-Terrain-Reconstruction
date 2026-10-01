"""Finish the Rhino file: clip every object exactly to the maquette rectangle
(terrain extent) and colour the terrain by altitude.

* Long OSM relations (power lines, train routes...) and the bay polygon reach
  up to 40 km outside the modelled area: every mesh is clipped exactly by the
  4 vertical planes of the terrain rectangle (triangles cut, not dropped;
  intersection points computed on canonically ordered edges so neighbouring
  triangles stay conforming). Buildings (closed solids) fully outside are
  removed, those crossing the border are kept whole.
* Terrain mesh gets per-vertex colours: sea blue, then green -> ochre ->
  brown -> light grey with altitude (hypsometric tints).
Usage: python scripts/05c_finish_rhino.py results/Oran_relief_Copernicus_Rhino8.3dm"""
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import rhino3dm as r3

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("b", Path(__file__).parent / "05_build_oran_relief.py")
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)


def clip_halfplane(V, T, axis, c, keep_ge, eps=1e-9):
    """Keep the part of triangles with V[:,axis] >= c (keep_ge) or <= c."""
    s = (V[:, axis] - c) * (1 if keep_ge else -1)
    S = s[T]
    inside = S >= -eps
    n_in = inside.sum(1)
    out = [T[n_in == 3]]
    newV = []
    base = len(V)

    def cross(P, Q, sP, sQ):
        swap = (P[:, 0] > Q[:, 0]) | ((P[:, 0] == Q[:, 0]) & ((P[:, 1] > Q[:, 1]) |
                                                          ((P[:, 1] == Q[:, 1]) & (P[:, 2] > Q[:, 2]))))
        P2 = np.where(swap[:, None], Q, P); Q2 = np.where(swap[:, None], P, Q)
        s1 = np.where(swap, sQ, sP); s2 = np.where(swap, sP, sQ)
        return P2 + (s1 / (s1 - s2))[:, None] * (Q2 - P2)

    for k in range(3):
        rot = [(0, 1, 2), (1, 2, 0), (2, 0, 1)][k]
        Tk, Ik, Sk = T[:, rot], inside[:, rot], S[:, rot]
        # one vertex inside (vertex 0)
        m1 = (n_in == 1) & Ik[:, 0]
        if m1.any():
            a, bb, cc = Tk[m1, 0], Tk[m1, 1], Tk[m1, 2]
            pab = cross(V[a], V[bb], Sk[m1, 0], Sk[m1, 1]); pac = cross(V[a], V[cc], Sk[m1, 0], Sk[m1, 2])
            n = m1.sum(); i1 = base + np.arange(n); i2 = base + n + np.arange(n); base += 2 * n
            newV += [pab, pac]; out.append(np.c_[a, i1, i2])
        # two vertices inside (vertex 0 outside)
        m2 = (n_in == 2) & ~Ik[:, 0]
        if m2.any():
            a, bb, cc = Tk[m2, 0], Tk[m2, 1], Tk[m2, 2]
            pab = cross(V[a], V[bb], Sk[m2, 0], Sk[m2, 1]); pac = cross(V[a], V[cc], Sk[m2, 0], Sk[m2, 2])
            n = m2.sum(); i1 = base + np.arange(n); i2 = base + n + np.arange(n); base += 2 * n
            newV += [pab, pac]; out += [np.c_[i1, bb, cc], np.c_[i1, cc, i2]]
    V = np.vstack([V] + newV) if newV else V
    return V, np.vstack(out)


def hypsometric(z):
    stops = np.array([0, 20, 80, 160, 300, 450, 620])
    cols = np.array([[66, 133, 194], [120, 170, 110], [160, 190, 120], [214, 200, 140],
                     [190, 150, 100], [160, 120, 90], [235, 230, 225]], float)
    c = np.stack([np.interp(z, stops, cols[:, i]) for i in range(3)], 1)
    c[z <= 0.01] = [66, 133, 194]                                   # sea
    return c.astype(np.uint8)


def main(path):
    rep = json.loads((ROOT / "results/relief_report.json").read_text())
    x0, y0, x1, y1 = rep["terrain_extent_local_m"]
    src = r3.File3dm.Read(path)
    out = r3.File3dm()
    out.Settings.ModelUnitSystem = src.Settings.ModelUnitSystem
    out.Settings.ModelAbsoluteTolerance = src.Settings.ModelAbsoluteTolerance
    out.Settings.EarthAnchorPoint = src.Settings.EarthAnchorPoint
    for i in range(len(src.Layers)):
        L = src.Layers[i]; n = r3.Layer()
        n.Name, n.Color, n.Visible = L.Name, L.Color, L.Visible
        out.Layers.Add(n)
    stats = {"objects_in": 0, "objects_out": 0, "faces_in": 0, "faces_out": 0, "removed_empty": []}
    for o in src.Objects:
        g = o.Geometry
        lname = src.Layers[o.Attributes.LayerIndex].Name
        V = np.array([[p.X, p.Y, p.Z] for p in g.Vertices], np.float64)
        F = np.array([tuple(g.Faces[k]) for k in range(g.Faces.Count)], np.int64).reshape(-1, 4)
        stats["objects_in"] += 1; stats["faces_in"] += len(F)
        if lname == "TERRAIN_COPERNICUS_GLO30":
            Vc, Fc = b.clean_for_rhino(V, F)
            m = r3.Mesh(); m.Vertices.UseDoublePrecisionVertices = True
            for x, y, z in Vc.tolist():
                m.Vertices.Add(x, y, z)
            for a, bb, c, d in Fc.tolist():
                m.Faces.AddFace(a, bb, c) if c == d else m.Faces.AddFace(a, bb, c, d)
            for r, gg, bl in hypsometric(Vc[:, 2]).tolist():
                m.VertexColors.Add(r, gg, bl)
            m.Normals.ComputeNormals()
            out.Objects.AddMesh(m, o.Attributes)
            stats["objects_out"] += 1; stats["faces_out"] += len(Fc)
            continue
        T = b.tris(F)
        # closed solids (buildings): keep whole if their centre is inside, else drop
        P = V[T]
        if g.IsClosed and (V[:, 2].max() - V[:, 2].min()) > 0 and lname.startswith(("BUILDING", "HEIGHT_", "EXTRA_BUILD", "PLANE")):
            c = P.mean(1)
            T = T[(c[:, 0] >= x0) & (c[:, 0] <= x1) & (c[:, 1] >= y0) & (c[:, 1] <= y1)]
        else:
            for axis, cval, ge in ((0, x0, True), (0, x1, False), (1, y0, True), (1, y1, False)):
                if len(T):
                    V, T = clip_halfplane(V, T, axis, cval, ge)
        if not len(T):
            stats["removed_empty"].append(lname)
            continue
        Vc, Fc = b.clean_for_rhino(V, T)
        if not len(Fc):
            stats["removed_empty"].append(lname)
            continue
        b.add_mesh(out, Vc, Fc, o.Attributes)
        stats["objects_out"] += 1; stats["faces_out"] += len(Fc)
    bad = []
    for o in out.Objects:
        ok, log = o.Geometry.IsValidWithLog
        if not ok:
            bad.append((out.Layers[o.Attributes.LayerIndex].Name, log.strip()[:100]))
        bb = o.Geometry.GetBoundingBox()
        if bb.Min.X < x0 - 1e-6 or bb.Max.X > x1 + 1e-6 or bb.Min.Y < y0 - 1e-6 or bb.Max.Y > y1 + 1e-6:
            stats.setdefault("outside_after_clip", []).append(out.Layers[o.Attributes.LayerIndex].Name)
    stats["invalid_meshes"] = bad
    out.Write(path, 8)
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
