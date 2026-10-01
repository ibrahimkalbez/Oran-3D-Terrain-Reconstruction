"""Build the Oran model on its real relief (Rhino 8 file + data for printing).

Input : data/rhino/brahim_ib9a.3dm (flat OSM city, Web Mercator XY, Z in m)
        data/copernicus/Copernicus_DSM_N35_W00{1,2}.tif (GLO-30)
Output: results/Oran_relief_Copernicus_Rhino8.3dm
        results/cache/relief_parts.npz   (terrain + lifted solids, for 06_print_export.py)
        results/relief_report.json

Method (documentation/methodologie_fusion.md, rapport_final.md)
 1. Every vertex: model XY (EPSG:3857 relative to lon -0.635 / lat 35.699)
    -> lon/lat -> local Transverse Mercator (true metres, same origin, north-up).
    Z is kept (already true metres). Pure per-point projection, no fitting.
 2. Meshes are welded (1e-4 m), degenerate/duplicate faces removed, and split
    into parts at non-manifold edges (each building = its own closed part).
 3. Terrain = Copernicus GLO-30 on a 25 m grid (its native resolution here),
    sea flattened to 0 m.
    One exact surface function T(x,y) (same triangulation as the terrain
    mesh) is used for everything below, so nothing floats or sinks.
 4. Parts taller than 1.5 m (buildings, towers, aircraft) are lifted as rigid
    blocks to the MEDIAN ground level under their footprint (true height above
    ground kept exactly); the base of every grounded part is then extended
    vertically down to the LOWEST ground point of its footprint (foundation),
    so nothing floats on slopes and nothing is buried uphill. XY untouched.
 5. Thin parts (roads, rail, land use, water, barriers...) are refined
    adaptively and draped vertex by vertex: z = T(x,y) + z_orig + 0.5
    (0.5 m = the original BASE plate at z = -0.5, which the terrain replaces,
    so the original stacking order of all layers is preserved exactly).
    Per layer, the refinement tolerance is kept below the layer's height above
    the ground (<= 0.5 m) -> no draped surface ever dips under the terrain.
    Water (BAY, NATURAL_WA) stays flat at the median level of its outline.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
import rhino3dm as r3
from scipy.ndimage import label, map_coordinates
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

sys.path.insert(0, str(Path(__file__).parent))
from oran_georef import ORIGIN_LAT, ORIGIN_LON, local_to_lonlat, model_to_local  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/rhino/brahim_ib9a.3dm"
OUT3DM = ROOT / "results/Oran_relief_Copernicus_Rhino8.3dm"
CACHE = ROOT / "results/cache"
TILES = [ROOT / f"data/copernicus/Copernicus_DSM_N35_W00{i}.tif" for i in (1, 2)]
RES = 25.0            # terrain grid (m) ~ native Copernicus 1" (25 x 31 m here)
RIGID_MIN_H = 1.5     # parts taller than this are lifted rigidly
DRAPE_TOL_MAX = 0.5  # max draping deviation (m); per layer: < its height above ground
DRAPE_MIN_EDGE = 0.05  # edges shorter than this are never split (m)
BASE_OFFSET = 0.5     # original BASE plate was at z = -0.5
WELD = 1e-4           # weld tolerance (m)
SKIP_LAYERS = {"BASE"}
WATER_LAYERS = {"BAY", "NATURAL_WA"}  # flat water surfaces  # flat 30 km plate, replaced by the terrain


# ----------------------------------------------------------------- read / cache
def read_model():
    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / "model_raw.npz"
    model = r3.File3dm.Read(str(SRC))
    if model is None:
        sys.exit(f"cannot read {SRC}")
    objs = []
    for o in model.Objects:
        g = o.Geometry
        if not isinstance(g, r3.Mesh):
            print("  non-mesh object skipped:", type(g).__name__)
            continue
        V = np.array([[p.X, p.Y, p.Z] for p in g.Vertices], np.float64)
        F = np.array([tuple(g.Faces[i]) for i in range(g.Faces.Count)], np.int64).reshape(-1, 4)
        objs.append((o.Attributes, V, F, g.IsValid))
    return model, objs


# ----------------------------------------------------------------- DEM / terrain
class Dem:
    def __init__(self):
        self.t = [rasterio.open(p) for p in TILES]
        self.a = [t.read(1).astype(np.float64) for t in self.t]

    def __call__(self, lon, lat):
        out = np.full(np.shape(lon), np.nan)
        for t, a in zip(self.t, self.a):
            T = t.transform
            m = (lon >= T.c) & (lon < T.c + T.a * t.width) & (lat <= T.f) & (lat > T.f + T.e * t.height)
            out[m] = map_coordinates(a, [(lat[m] - T.f) / T.e - 0.5, (lon[m] - T.c) / T.a - 0.5],
                                     order=1, mode="nearest")
        return out


class Terrain:
    """Regular grid, row 0 = south. Surface = the exact triangulation of the mesh."""

    def __init__(self, dem, x0, y0, x1, y1):
        self.x = np.arange(x0, x1 + RES / 2, RES)
        self.y = np.arange(y0, y1 + RES / 2, RES)
        X, Y = np.meshgrid(self.x, self.y)
        Z = dem(*local_to_lonlat(X, Y))
        if np.isnan(Z).any():
            sys.exit("terrain outside Copernicus tiles")
        Z = np.maximum(Z, 0.0)
        # open sea = low area connected to the northern edge -> exactly 0 m
        lab, _ = label(Z <= 0.5)
        sea_ids = np.setdiff1d(np.unique(lab[-1]), [0])
        self.sea = np.isin(lab, sea_ids)
        Z[self.sea] = 0.0
        self.Z = Z
        self.dem = dem

    def __call__(self, xq, yq):
        x, y, Z = self.x, self.y, self.Z
        fx = (xq - x[0]) / RES
        fy = (yq - y[0]) / RES
        inside = (fx >= 0) & (fx <= len(x) - 1) & (fy >= 0) & (fy <= len(y) - 1)
        c = np.clip(np.floor(fx).astype(int), 0, len(x) - 2)
        r = np.clip(np.floor(fy).astype(int), 0, len(y) - 2)
        u, v = fx - c, fy - r
        z00, z10, z01, z11 = Z[r, c], Z[r, c + 1], Z[r + 1, c], Z[r + 1, c + 1]
        lower = u + v <= 1
        out = np.where(lower, z00 + u * (z10 - z00) + v * (z01 - z00),
                       z11 + (1 - u) * (z01 - z11) + (1 - v) * (z10 - z11))
        if (~inside).any():  # far features (long power / rail routes): raw DEM
            lon, lat = local_to_lonlat(xq[~inside], yq[~inside])
            out[~inside] = np.maximum(np.nan_to_num(self.dem(lon, lat)), 0.0)
        return out

    def set_window(self, x0, y0, x1, y1):
        """Displayed / printed terrain = this window of the (larger) draping grid."""
        self.wi = slice(int(round((x0 - self.x[0]) / RES)), int(round((x1 - self.x[0]) / RES)) + 1)
        self.wj = slice(int(round((y0 - self.y[0]) / RES)), int(round((y1 - self.y[0]) / RES)) + 1)

    def window(self):
        return self.x[self.wi], self.y[self.wj], self.Z[self.wj, self.wi]

    def mesh(self):
        wx, wy, wz = self.window()
        ny, nx = wz.shape
        X, Y = np.meshgrid(wx, wy)
        V = np.c_[X.ravel(), Y.ravel(), wz.ravel()]
        i = (np.arange(ny - 1)[:, None] * nx + np.arange(nx - 1)[None, :]).ravel()
        F = np.vstack([np.c_[i, i + 1, i + nx], np.c_[i + 1, i + nx + 1, i + nx]])
        return V, F


# ----------------------------------------------------------------- mesh cleaning
def clean_and_split(V, F):
    """Weld, drop degenerate/duplicate faces, split at non-manifold edges.
    Returns V2 (own vertex copies per part), F2 (n,4; tri if f[2]==f[3]), part id per face."""
    key = np.round(V / WELD).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    Vw = V[first]
    Fw = inv.ravel()[F]
    tri = Fw[:, 2] == Fw[:, 3]
    a_, b_, c_, d_ = Fw.T
    ok3 = (a_ != b_) & (b_ != c_) & (c_ != a_)
    good = np.where(tri, ok3, ok3 & (d_ != a_) & (d_ != b_) & (d_ != c_))
    Fw = Fw[good]
    # duplicate faces (same vertex set)
    _, uniq = np.unique(np.sort(Fw, axis=1), axis=0, return_index=True)
    Fw = Fw[np.sort(uniq)]
    # zero-area faces
    a, b, c, d = (Vw[Fw[:, k]] for k in range(4))
    quad = Fw[:, 2] != Fw[:, 3]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1) + \
        np.where(quad, 0.5 * np.linalg.norm(np.cross(c - a, d - a), axis=1), 0)
    Fw = Fw[area > 1e-10]
    n = len(Fw)
    quad = Fw[:, 2] != Fw[:, 3]
    ar = np.arange(n)
    E = np.vstack([Fw[:, [0, 1]], Fw[:, [1, 2]], np.c_[Fw[:, 2], np.where(quad, Fw[:, 3], Fw[:, 0])],
                   Fw[quad][:, [3, 0]]])
    fid = np.concatenate([ar, ar, ar, ar[quad]])
    E = np.sort(E, axis=1)
    order = np.lexsort((E[:, 1], E[:, 0]))
    E, fid = E[order], fid[order]
    same = np.all(E[1:] == E[:-1], axis=1)
    starts = np.r_[0, np.nonzero(~same)[0] + 1]
    counts = np.diff(np.r_[starts, len(E)])
    two = starts[counts == 2]
    g = coo_matrix((np.ones(len(two)), (fid[two], fid[two + 1])), shape=(n, n))
    npart, part = connected_components(g, directed=False)
    stats = {"open_edges": int((counts == 1).sum()), "nonmanifold_edges": int((counts > 2).sum())}
    # own vertex copies per part
    pk = part[:, None].astype(np.int64) * (len(Vw) + 1) + Fw
    uk, inv2 = np.unique(pk.ravel(), return_inverse=True)
    V2 = Vw[uk % (len(Vw) + 1)]
    F2 = inv2.reshape(-1, 4)
    return V2, F2, part, npart, stats


def group_median(g, v, n):
    """Median of v per group id g (0..n-1); NaN for empty groups."""
    out = np.full(n, np.nan)
    if not len(v):
        return out
    o = np.lexsort((v, g))
    g, v = g[o], v[o]
    starts = np.r_[0, np.nonzero(np.diff(g))[0] + 1]
    cnt = np.diff(np.r_[starts, len(g)])
    lo = v[starts + (cnt - 1) // 2]
    hi = v[starts + cnt // 2]
    out[g[starts]] = 0.5 * (lo + hi)
    return out


def orient_outward(V, F, part, npart):
    """Flip every closed part whose signed volume is negative (inward normals
    in the source model) so that all solids have outward-facing normals."""
    q = F[:, 2] != F[:, 3]
    T = np.r_[F[:, [0, 1, 2]], F[q][:, [0, 2, 3]]]
    tp = np.r_[part, part[q]]
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    vol = np.zeros(npart)
    np.add.at(vol, tp, np.einsum("ij,ij->i", a, np.cross(b, c)) / 6.0)
    flip = (vol < 0)[part]
    F = F.copy()
    F[flip] = np.where(q[flip, None], F[flip][:, [0, 3, 2, 1]], F[flip][:, [0, 2, 1, 1]])
    return F, int((vol < 0).sum())


def tris(F):
    q = F[:, 2] != F[:, 3]
    return np.r_[F[:, [0, 1, 2]], F[q][:, [0, 2, 3]]]


def _edge_dev(A, B, surf):
    """Exact max |terrain - chord| along segments AB (XY): the terrain is
    piecewise linear, so the extremum is at a crossing with a grid line,
    a grid column or a cell diagonal. Outside the grid: samples at 1/4..3/4."""
    za, zb = surf(A[:, 0], A[:, 1]), surf(B[:, 0], B[:, 1])
    dev = np.zeros(len(A))
    for t in (0.25, 0.5, 0.75):
        P = A + (B - A) * t
        dev = np.maximum(dev, np.abs(surf(P[:, 0], P[:, 1]) - (za + (zb - za) * t)))
    if not hasattr(surf, "x"):
        return dev
    x0, y0 = surf.x[0], surf.y[0]
    for f in ((1, 0), (0, 1), (1, 1)):
        sa = f[0] * (A[:, 0] - x0) + f[1] * (A[:, 1] - y0)
        sb = f[0] * (B[:, 0] - x0) + f[1] * (B[:, 1] - y0)
        k0 = np.floor(np.minimum(sa, sb) / RES) + 1
        k1 = np.ceil(np.maximum(sa, sb) / RES) - 1
        n = np.clip(k1 - k0 + 1, 0, None).astype(np.int64)
        if not n.sum():
            continue
        e = np.repeat(np.arange(len(A)), n)
        k = np.repeat(k0, n) + (np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n))
        t = (k * RES - sa[e]) / (sb[e] - sa[e])
        P = A[e] + (B[e] - A[e]) * t[:, None]
        d = np.abs(surf(P[:, 0], P[:, 1]) - (za[e] + (zb[e] - za[e]) * t))
        np.maximum.at(dev, e, d)
    return dev


def _node_dev(V, T, surf):
    """Exact max |terrain - plane| at terrain grid nodes inside each triangle."""
    dev = np.zeros(len(T))
    if not hasattr(surf, "x"):
        return dev
    x0, y0, nx, ny = surf.x[0], surf.y[0], len(surf.x), len(surf.y)
    A, B, C = V[T[:, 0], :2], V[T[:, 1], :2], V[T[:, 2], :2]
    det = (B[:, 0] - A[:, 0]) * (C[:, 1] - A[:, 1]) - (C[:, 0] - A[:, 0]) * (B[:, 1] - A[:, 1])
    lo, hi = np.minimum(np.minimum(A, B), C), np.maximum(np.maximum(A, B), C)
    i0 = np.clip(np.floor((lo[:, 0] - x0) / RES) + 1, 0, nx - 1).astype(np.int64)
    i1 = np.clip(np.floor((hi[:, 0] - x0) / RES), -1, nx - 1).astype(np.int64)
    j0 = np.clip(np.floor((lo[:, 1] - y0) / RES) + 1, 0, ny - 1).astype(np.int64)
    j1 = np.clip(np.floor((hi[:, 1] - y0) / RES), -1, ny - 1).astype(np.int64)
    ni, nj = np.clip(i1 - i0 + 1, 0, None), np.clip(j1 - j0 + 1, 0, None)
    n = ni * nj * (np.abs(det) > 1e-9)
    if not n.sum():
        return dev
    za, zb, zc = (surf(P[:, 0], P[:, 1]) for P in (A, B, C))
    for chunk in np.array_split(np.arange(len(T)), max(1, int(n.sum() // 5_000_000) + 1)):
        nc = n[chunk]
        if not nc.sum():
            continue
        e = np.repeat(chunk, nc)
        r = np.arange(nc.sum()) - np.repeat(np.cumsum(nc) - nc, nc)
        ii = i0[e] + r % ni[e]
        jj = j0[e] + r // ni[e]
        px, py = x0 + ii * RES, y0 + jj * RES
        u = ((px - A[e, 0]) * (C[e, 1] - A[e, 1]) - (C[e, 0] - A[e, 0]) * (py - A[e, 1])) / det[e]
        w = ((B[e, 0] - A[e, 0]) * (py - A[e, 1]) - (px - A[e, 0]) * (B[e, 1] - A[e, 1])) / det[e]
        m = (u >= 0) & (w >= 0) & (u + w <= 1)
        d = np.abs(surf.Z[jj[m], ii[m]] - (za[e[m]] + u[m] * (zb[e[m]] - za[e[m]]) + w[m] * (zc[e[m]] - za[e[m]])))
        np.maximum.at(dev, e[m], d)
    return dev


def subdivide(V, T, surf, tol, min_edge=0.5, max_iter=40):
    """Adaptive conforming refinement for draping, with an EXACT error test:
    the deviation between a flat triangle and the piecewise-linear terrain is
    maximal at the terrain lines crossing its edges or at the terrain nodes
    inside it; edges (resp. all 3 edges) are split while that deviation
    exceeds `tol` and the edge is longer than `min_edge`. Triangles with 1, 2
    or 3 split edges become 2, 3 or 4 triangles: no slivers, no T-junctions,
    closed meshes stay closed. Flat areas (sea, plains) are not refined."""
    for _ in range(max_iter):
        e = np.sort(np.r_[T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]], axis=1)
        ue, inv = np.unique(e, axis=0, return_inverse=True)
        inv = inv.ravel().reshape(3, -1).T
        L = np.linalg.norm(V[ue[:, 0], :2] - V[ue[:, 1], :2], axis=1)
        split = (_edge_dev(V[ue[:, 0], :2], V[ue[:, 1], :2], surf) > tol) & (L > min_edge)
        bad_t = (_node_dev(V, T, surf) > tol) & (L[inv].max(1) > min_edge)
        split[inv[bad_t].ravel()] = True
        split &= L > min_edge
        if not split.any():
            break
        mid = -np.ones(len(ue), np.int64)
        mid[split] = len(V) + np.arange(split.sum())
        V = np.vstack([V, 0.5 * (V[ue[split, 0]] + V[ue[split, 1]])])
        m = mid[inv]
        ns = (m >= 0).sum(1)
        out = [T[ns == 0]]
        for k in range(3):
            rot = [(0, 1, 2), (1, 2, 0), (2, 0, 1)][k]
            Tk = T[:, rot]; mk = m[:, rot]
            one = (ns == 1) & (mk[:, 0] >= 0)
            a_, b_, c_, m0 = Tk[one, 0], Tk[one, 1], Tk[one, 2], mk[one, 0]
            out += [np.c_[a_, m0, c_], np.c_[m0, b_, c_]]
            two = (ns == 2) & (mk[:, 2] < 0)
            a_, b_, c_, m0, m1 = Tk[two, 0], Tk[two, 1], Tk[two, 2], mk[two, 0], mk[two, 1]
            out += [np.c_[m0, b_, m1], np.c_[a_, m0, m1], np.c_[a_, m1, c_]]
        f3 = ns == 3
        a_, b_, c_ = T[f3, 0], T[f3, 1], T[f3, 2]
        m0, m1, m2 = m[f3, 0], m[f3, 1], m[f3, 2]
        out += [np.c_[a_, m0, m2], np.c_[m0, b_, m1], np.c_[m2, m1, c_], np.c_[m0, m1, m2]]
        T = np.vstack(out)
    return V, T


# ----------------------------------------------------------------- main
def main():
    t0 = time.time()
    model, objs = read_model()
    layers = [model.Layers[i] for i in range(len(model.Layers))]
    print(f"read {len(objs)} meshes in {time.time() - t0:.0f}s")

    # terrain extent: 99.9 % of vertices (long OSM route relations excluded) + 400 m
    pts = np.vstack([V[::5, :2] for a, V, F, _ in objs
                     if not layers[a.LayerIndex].Name.startswith(("ROUTE_", "POWER_LINE", "BASE"))])
    lx, ly = model_to_local(pts[:, 0], pts[:, 1])
    lo = np.percentile(np.c_[lx, ly], 0.05, axis=0) - 400
    hi = np.percentile(np.c_[lx, ly], 99.95, axis=0) + 400
    dem = Dem()
    x0, y0 = np.floor(lo / RES) * RES
    x1, y1 = np.ceil(hi / RES) * RES
    # draping grid = same lattice extended to ALL geometry (long OSM routes up to 40 km)
    allp = np.vstack([V[:, :2] for a, V, F, _ in objs if layers[a.LayerIndex].Name not in SKIP_LAYERS])
    ax_, ay_ = model_to_local(allp[:, 0], allp[:, 1])
    fx0 = min(x0, np.floor((ax_.min() - 200) / RES) * RES); fy0 = min(y0, np.floor((ay_.min() - 200) / RES) * RES)
    fx1 = max(x1, np.ceil((ax_.max() + 200) / RES) * RES); fy1 = max(y1, np.ceil((ay_.max() + 200) / RES) * RES)
    terrain = Terrain(dem, fx0, fy0, fx1, fy1)
    terrain.set_window(x0, y0, x1, y1)
    wx, wy, wz = terrain.window()
    print(f"terrain {wz.shape[1]}x{wz.shape[0]} @ {RES} m (draping grid {terrain.Z.shape[1]}x"
          f"{terrain.Z.shape[0]}), z {wz.min():.1f}..{wz.max():.1f} m ({time.time() - t0:.0f}s)")

    out_objs, solids, report_layers = [], [], {}
    chk = {"xy_roundtrip_max_m": 0.0, "rigid_height_change_max_m": 0.0,
           "rigid_float_gap_max_m": 0.0, "drape_offset_err_max_m": 0.0}
    drape_tol = {}
    fnd = []
    for attr, V, F, valid in objs:
        lname = layers[attr.LayerIndex].Name
        if lname in SKIP_LAYERS:
            report_layers[lname] = "replaced by terrain"
            continue
        V2, F2, part, npart, st = clean_and_split(V, F)
        F2, st["parts_flipped_outward"] = orient_outward(V2, F2, part, npart)
        # XY projection (per point, exact)
        X, Y = model_to_local(V2[:, 0], V2[:, 1])
        L = np.c_[X, Y, V2[:, 2]]
        # part of each vertex (vertex copies are per part)
        vpart = np.zeros(len(V2), np.int64)
        vpart[F2.ravel()] = np.repeat(part, 4)
        zmin = np.full(npart, np.inf); zmax = np.full(npart, -np.inf)
        np.minimum.at(zmin, vpart, V2[:, 2]); np.maximum.at(zmax, vpart, V2[:, 2])
        rigid = (zmax - zmin) > RIGID_MIN_H
        # ---- rigid parts: footprint samples = bottom vertices + samples in bottom faces
        Tr = tris(F2)
        tpart = vpart[Tr[:, 0]]
        rv = rigid[vpart]
        bottom_v = rv & (V2[:, 2] <= zmin[vpart] + 0.05)
        smp_x, smp_y, smp_p = [L[bottom_v, 0]], [L[bottom_v, 1]], [vpart[bottom_v]]
        bt = rigid[tpart] & bottom_v[Tr].all(1)
        if bt.any():
            A, B, C = L[Tr[bt, 0]], L[Tr[bt, 1]], L[Tr[bt, 2]]
            area = 0.5 * np.abs(np.cross(B[:, :2] - A[:, :2], C[:, :2] - A[:, :2]))
            n = np.clip(np.ceil(area / 4.0).astype(int), 1, 5000)
            idx = np.repeat(np.arange(len(A)), n)
            rng = np.random.default_rng(0)
            u, w = rng.random(len(idx)), rng.random(len(idx))
            f = u + w > 1; u[f], w[f] = 1 - u[f], 1 - w[f]
            S_ = A[idx] + (B[idx] - A[idx]) * u[:, None] + (C[idx] - A[idx]) * w[:, None]
            smp_x.append(S_[:, 0]); smp_y.append(S_[:, 1]); smp_p.append(tpart[bt][idx])
        sx, sy, sp = map(np.concatenate, (smp_x, smp_y, smp_p))
        tz_s = terrain(sx, sy) if len(sx) else np.zeros(0)
        tmin = np.full(npart, np.inf)
        np.minimum.at(tmin, sp, tz_s)
        gmed = group_median(sp, tz_s, npart)       # ground level of the footprint
        grounded = rigid & (zmin <= 0.05)           # parts standing on the ground (z = 0)
        # ---- rigid parts: rigid lift to the median ground level (true height kept),
        #      base of grounded parts extended vertically down to the lowest ground point
        keepF = rigid[part]
        Fr = F2[keepF]
        used = np.unique(Fr.ravel())
        remap = -np.ones(len(L), np.int64); remap[used] = np.arange(len(used))
        pu = vpart[used]
        Vr = L[used].copy()
        Vr[:, 2] += np.where(rigid[pu], gmed[pu], 0.0)
        base = grounded[pu] & (L[used, 2] <= zmin[pu] + 0.05)
        Vr[base, 2] = tmin[pu][base]
        Fr = remap[Fr]
        if rigid.any():
            hr = np.full(npart, -np.inf)
            np.maximum.at(hr, pu, Vr[:, 2])
            rp = rigid & np.isfinite(hr) & np.isfinite(gmed)
            chk["rigid_height_change_max_m"] = max(chk["rigid_height_change_max_m"],
                                                   float(np.abs((hr - gmed) - zmax)[rp].max()))
            g = grounded & np.isfinite(tmin)
            if g.any():
                above = np.full(npart, -np.inf)       # base minus ground at every footprint sample
                np.maximum.at(above, sp, tmin[sp] - tz_s)
                chk["rigid_float_gap_max_m"] = max(chk["rigid_float_gap_max_m"], float(above[g].max()))
                fnd.append((gmed - tmin)[g])
        Fd = tris(F2[~keepF])
        Vd = np.zeros((0, 3)); Td = np.zeros((0, 3), np.int64)
        if len(Fd):
            usedd = np.unique(Fd.ravel())
            rm = -np.ones(len(L), np.int64); rm[usedd] = np.arange(len(usedd))
            if lname in WATER_LAYERS:
                # water is flat: each water body at the median terrain level of its outline
                Vd, Td = L[usedd].copy(), rm[Fd]
                vp = vpart[usedd]
                lev = np.zeros(npart)
                for q in np.unique(vp):
                    mq = vp == q
                    lev[q] = np.median(terrain(Vd[mq, 0], Vd[mq, 1]))
                Vd[:, 2] = lev[vp] + Vd[:, 2] + BASE_OFFSET
            else:
                # tolerance below the layer's height above the ground -> never sinks
                off_min = float((L[usedd, 2] + BASE_OFFSET).min())
                tol = float(np.clip(off_min - 0.02, 0.05, DRAPE_TOL_MAX))
                Vd, Td = subdivide(L[usedd].copy(), rm[Fd], terrain, tol, min_edge=DRAPE_MIN_EDGE)
                zorig = Vd[:, 2].copy()
                Vd[:, 2] = terrain(Vd[:, 0], Vd[:, 1]) + zorig + BASE_OFFSET
                # check inside faces (random points) : draped surface == terrain + offset
                # check: random points spread proportionally to AREA (what is seen)
                rng = np.random.default_rng(1)
                ar = 0.5 * np.linalg.norm(np.cross(Vd[Td[:, 1]] - Vd[Td[:, 0]], Vd[Td[:, 2]] - Vd[Td[:, 0]]), axis=1)
                ft = rng.choice(len(Td), min(len(Td) * 3, 60000), p=ar / ar.sum())
                w = rng.dirichlet([1, 1, 1], len(ft))
                P0 = (Vd[Td[ft]] * w[:, :, None]).sum(1)
                Z0 = (zorig[Td[ft]] * w).sum(1)
                inside = ((P0[:, 0] > terrain.x[0]) & (P0[:, 0] < terrain.x[-1]) &
                          (P0[:, 1] > terrain.y[0]) & (P0[:, 1] < terrain.y[-1]))
                if inside.any():
                    Tq = terrain(P0[inside, 0], P0[inside, 1])
                    dev = np.abs(P0[inside, 2] - Tq - Z0[inside] - BASE_OFFSET)
                    chk["drape_offset_err_max_m"] = max(chk["drape_offset_err_max_m"], float(dev.max()))
                    clr = float((P0[inside, 2] - Tq).min())
                    chk["drape_min_clearance_m"] = min(chk.get("drape_min_clearance_m", 9e9), clr)
                    drape_tol[lname] = {"tol_m": round(tol, 3), "min_clearance_m": round(clr, 3),
                                        "max_dev_m": round(float(dev.max()), 3)}
        # XY round-trip check on a sample (local -> lon/lat -> model)
        from oran_georef import X0_WM, Y0_WM, _WM
        s = slice(None, None, max(1, len(L) // 2000))
        lon, lat = local_to_lonlat(L[s, 0], L[s, 1])
        mx, my = _WM.transform(lon, lat)
        chk["xy_roundtrip_max_m"] = max(chk["xy_roundtrip_max_m"], float(np.max(np.hypot(
            mx - X0_WM - V2[s, 0], my - Y0_WM - V2[s, 1]))))
        out_objs.append((attr, Vr, Fr, Vd, Td))
        # solids for printing: closed rigid parts
        if rigid.any():
            solids.append((lname, Vr, Fr, vpart[used]))
        report_layers[lname] = {
            "faces_in": int(len(F)), "valid_in": bool(valid), "parts": int(npart),
            "rigid_parts": int(rigid.sum()), "draped_parts": int((~rigid).sum()),
            "faces_rigid": int(len(Fr)), "tris_draped": int(len(Td)), **st,
            "ground_level_m": [float(np.nanmin(gmed[rigid])), float(np.nanmax(gmed[rigid]))] if rigid.any() else None,
        }
        print(f"  {lname:32s} parts {npart:6d} rigid {rigid.sum():6d} draped tris {len(Td):8d} "
              f"({time.time() - t0:.0f}s)")

    write_rhino(model, layers, out_objs, terrain)
    save_cache(terrain, solids)
    rep = {"source": str(SRC.name), "origin_lon_lat": [ORIGIN_LON, ORIGIN_LAT],
           "terrain_grid": [int(wz.shape[1]), int(wz.shape[0])], "terrain_res_m": RES,
           "terrain_extent_local_m": [float(wx[0]), float(wy[0]), float(wx[-1]), float(wy[-1])],
           "terrain_z_m": [float(wz.min()), float(wz.max())],
           "draping_grid_extent_local_m": [float(terrain.x[0]), float(terrain.y[0]),
                                           float(terrain.x[-1]), float(terrain.y[-1])],
           "checks": chk,
           "foundation_extension_m_p50_p95_p99_max": (np.percentile(np.concatenate(fnd), [50, 95, 99, 100]).round(2).tolist() if fnd else None), "drape_tolerance_m": drape_tol, "layers": report_layers, "runtime_s": round(time.time() - t0)}
    (ROOT / "results/relief_report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    print(json.dumps(chk, indent=1))


def clean_for_rhino(V, F):
    """Final clean-up (Rhino 'CullDegenerateFaces' + 'Weld' + 'Compact'):
    weld vertices at identical positions, drop faces with repeated corners
    (degenerate) and exact duplicate faces, drop unused vertices."""
    if F.shape[1] == 3:
        F = np.c_[F, F[:, 2]]
    Vu, inv = np.unique(V, axis=0, return_inverse=True)
    F = inv.ravel()[F]
    tri = F[:, 2] == F[:, 3]
    a, b, c, d = F.T
    ok3 = (a != b) & (b != c) & (c != a)
    F = F[np.where(tri, ok3, ok3 & (d != a) & (d != b) & (d != c))]
    _, first = np.unique(np.sort(F, 1), axis=0, return_index=True)
    F = F[np.sort(first)]
    used = np.unique(F)
    rm = -np.ones(len(Vu), np.int64); rm[used] = np.arange(len(used))
    return Vu[used], rm[F]


def add_mesh(out, V, F, attr):
    V, F = clean_for_rhino(V, F)
    m = r3.Mesh()
    m.Vertices.UseDoublePrecisionVertices = True
    for x, y, z in V.tolist():
        m.Vertices.Add(x, y, z)
    Fl = F.tolist()
    if F.shape[1] == 4:
        for a, b, c, d in Fl:
            if c == d:
                m.Faces.AddFace(a, b, c)
            else:
                m.Faces.AddFace(a, b, c, d)
    else:
        for a, b, c in Fl:
            m.Faces.AddFace(a, b, c)
    m.Normals.ComputeNormals()
    return out.Objects.AddMesh(m, attr)


def write_rhino(model, layers, out_objs, terrain):
    out = r3.File3dm()
    out.Settings.ModelUnitSystem = r3.UnitSystem.Meters
    out.Settings.ModelAbsoluteTolerance = 0.001
    ea = out.Settings.EarthAnchorPoint
    ea.EarthBasepointLatitude = ORIGIN_LAT
    ea.EarthBasepointLongitude = ORIGIN_LON
    ea.EarthBasepointElevation = 0.0
    ea.ModelBasePoint = r3.Point3d(0, 0, 0)
    ea.ModelNorth = r3.Vector3d(0, 1, 0)
    ea.ModelEast = r3.Vector3d(1, 0, 0)
    ea.Name = "Oran"
    ea.Description = ("Local Transverse Mercator (WGS84) centred on lon -0.635 lat 35.699, metres. "
                      "Z = Copernicus GLO-30 heights (EGM2008).")
    out.Settings.EarthAnchorPoint = ea
    for L in layers:
        n = r3.Layer()
        n.Name, n.Color, n.Visible = L.Name, L.Color, L.Visible
        out.Layers.Add(n)
    for name, col in [("TERRAIN_COPERNICUS_GLO30", (196, 178, 140, 255))]:
        n = r3.Layer(); n.Name = name; n.Color = col
        tl = out.Layers.Add(n)
    for attr, Vr, Fr, Vd, Td in out_objs:
        a = attr.Duplicate() if hasattr(attr, "Duplicate") else attr
        V = np.vstack([Vr, Vd])
        F = np.vstack([Fr, np.c_[Td, Td[:, 2]] + len(Vr)]) if len(Td) else Fr
        if len(F):
            add_mesh(out, V, F, a)
    TV, TF = terrain.mesh()
    ta = r3.ObjectAttributes(); ta.LayerIndex = tl; ta.Name = f"Terrain Copernicus GLO-30 {RES:.0f} m"
    add_mesh(out, TV, TF, ta)
    OUT3DM.parent.mkdir(exist_ok=True)
    out.Write(str(OUT3DM), 8)
    print("wrote", OUT3DM, f"{OUT3DM.stat().st_size / 1e6:.0f} MB")


def save_cache(terrain, solids):
    CACHE.mkdir(parents=True, exist_ok=True)
    wx, wy, wz = terrain.window()
    d = {"tx": wx, "ty": wy, "tz": wz}
    for i, (name, V, F, vp) in enumerate(solids):
        d[f"s{i}_V"], d[f"s{i}_F"], d[f"s{i}_P"] = V, F, vp
    d["names"] = np.array([s[0] for s in solids])
    np.savez_compressed(CACHE / "relief_parts.npz", **d)


if __name__ == "__main__":
    main()
