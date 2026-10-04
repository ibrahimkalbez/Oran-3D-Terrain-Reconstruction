"""Shared helpers for the urban detailing of the white maquette (scripts 14, 15).

  * load_maquette()  : every mesh of the white maquette Rhino file as numpy
                       arrays (cached in results/cache, the 3dm read takes 2 min)
  * Terrain          : the exact terrain surface of the file (25 m grid, same
                       triangulation as the TERRAIN_ET_SOCLE mesh) -> z(x, y)
  * mesh_parts()     : connected parts of a triangle mesh
  * part_footprints(): footprint rings (top faces of flat-roof prisms)
All coordinates: LOCAL Transverse Mercator metres of the model (scripts/oran_georef.py).
"""
import pickle
from pathlib import Path

import numpy as np
import rhino3dm as r3
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "results/cache"
SRC_3DM = ROOT / "results/Oran_maquette_blanche_complete_Rhino8.3dm"
SOCLE_M = 60.0


def load_maquette(src=SRC_3DM):
    """{layer: (V float64 (n,3), F int64 (m,4))}, layer order, file settings, polylines."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / "maquette_meshes.pkl"
    if cache.exists() and cache.stat().st_mtime > Path(src).stat().st_mtime:
        return pickle.load(open(cache, "rb"))
    f = r3.File3dm.Read(str(src))
    meshes, order = {}, []
    for i in range(len(f.Layers)):
        order.append(f.Layers[i].Name)
    for o in f.Objects:
        g = o.Geometry
        if not isinstance(g, r3.Mesh):
            continue
        ln = f.Layers[o.Attributes.LayerIndex].Name
        V = np.array([[p.X, p.Y, p.Z] for p in g.Vertices], np.float64)
        F = np.array([tuple(g.Faces[k]) for k in range(g.Faces.Count)], np.int64).reshape(-1, 4)
        meshes[ln] = (V, F)
    e = f.Settings.EarthAnchorPoint
    data = {"meshes": meshes, "layers": order,
            "earth": (e.EarthBasepointLatitude, e.EarthBasepointLongitude)}
    pickle.dump(data, open(cache, "wb"), protocol=4)
    return data


class Terrain:
    """Regular 25 m grid (row 0 = south); surface = exact triangulation of the
    terrain mesh: cell split by the diagonal (c+1, r) - (c, r+1)."""

    def __init__(self, x, y, Z):
        self.x, self.y, self.Z = x, y, Z
        self.res = float(x[1] - x[0])

    @classmethod
    def from_block(cls, V):
        top = V[V[:, 2] > -SOCLE_M + 1]
        x = np.unique(np.round(top[:, 0], 3))
        y = np.unique(np.round(top[:, 1], 3))
        res = x[1] - x[0]
        Z = np.full((len(y), len(x)), np.nan)
        Z[np.round((top[:, 1] - y[0]) / res).astype(int),
          np.round((top[:, 0] - x[0]) / res).astype(int)] = top[:, 2]
        assert not np.isnan(Z).any()
        return cls(x, y, Z)

    def __call__(self, xq, yq, Z=None):
        Z = self.Z if Z is None else Z
        x, y, R = self.x, self.y, self.res
        fx = np.clip((np.asarray(xq) - x[0]) / R, 0, len(x) - 1 - 1e-9)
        fy = np.clip((np.asarray(yq) - y[0]) / R, 0, len(y) - 1 - 1e-9)
        c = np.floor(fx).astype(int)
        r = np.floor(fy).astype(int)
        u, v = fx - c, fy - r
        z00, z10, z01, z11 = Z[r, c], Z[r, c + 1], Z[r + 1, c], Z[r + 1, c + 1]
        return np.where(u + v <= 1, z00 + u * (z10 - z00) + v * (z01 - z00),
                        z11 + (1 - u) * (z01 - z11) + (1 - v) * (z10 - z11))


def tris(F):
    q = F[:, 2] != F[:, 3]
    return np.r_[F[:, [0, 1, 2]], F[q][:, [0, 2, 3]]]


def mesh_parts(V, T):
    n = len(V)
    g = coo_matrix((np.ones(2 * len(T)), (np.r_[T[:, 0], T[:, 1]], np.r_[T[:, 1], T[:, 2]])), shape=(n, n))
    return connected_components(g, directed=False)


def ring_area(P):
    x, y = P[:, 0], P[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def boundary_rings(T, V):
    """Closed boundary loops of a triangle set (vertex index lists), using the
    directed boundary edges (outer rings CCW, holes CW for upward faces)."""
    E = np.r_[T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]
    key = np.sort(E, 1)
    _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    B = E[cnt[inv.ravel()] == 1]
    nxt = {}
    for a, b in B.tolist():
        nxt.setdefault(a, []).append(b)
    rings, used = [], set()
    for a, b in B.tolist():
        if (a, b) in used:
            continue
        ring = [a]
        cur, prev = b, a
        used.add((a, b))
        guard = 0
        while cur != a and guard < len(B) + 2:
            ring.append(cur)
            cands = [c for c in nxt.get(cur, []) if (cur, c) not in used]
            if not cands:
                break
            prev, cur = cur, cands[0]
            used.add((prev, cur))
            guard += 1
        if cur == a and len(ring) >= 3:
            rings.append(ring)
    return rings
