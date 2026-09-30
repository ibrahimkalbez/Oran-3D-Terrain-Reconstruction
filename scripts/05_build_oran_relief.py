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
 3. Terrain = Copernicus GLO-30, bilinear on a 15 m grid, sea flattened to 0 m.
    One exact surface function T(x,y) (same triangulation as the terrain
    mesh) is used for everything below, so nothing floats or sinks.
 4. Parts taller than 1.5 m (buildings, towers, aircraft) are lifted as rigid
    blocks: dz = min of T over their footprint -> geometry and heights exact,
    base touches the ground at its lowest point, never floats.
 5. Thin parts (roads, rail, land use, water, barriers...) are refined
    adaptively (deviation from the terrain <= 5 cm) and draped vertex by vertex: z = T(x,y) + z_orig + 0.5
    (0.5 m = the original BASE plate at z = -0.5, which the terrain replaces,
    so the original stacking order of all layers is preserved exactly).
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
RES = 15.0            # terrain grid (m)
RIGID_MIN_H = 1.5     # parts taller than this are lifted rigidly
DRAPE_TOL = 0.05     # max draping deviation from the terrain surface (m)
BASE_OFFSET = 0.5     # original BASE plate was at z = -0.5
WELD = 1e-4           # weld tolerance (m)
SKIP_LAYERS = {"BASE"}  # flat 30 km plate, replaced by the terrain


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

    def mesh(self):
        ny, nx = self.Z.shape
        X, Y = np.meshgrid(self.x, self.y)
        V = np.c_[X.ravel(), Y.ravel(), self.Z.ravel()]
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


def tris(F):
    q = F[:, 2] != F[:, 3]
    return np.r_[F[:, [0, 1, 2]], F[q][:, [0, 2, 3]]]


def subdivide(V, T, surf, tol, min_edge=2.0, max_iter=20):
    """Adaptive conforming refinement for draping.
    An edge is split when the terrain surface deviates by more than `tol`
    from the straight edge (tested at 1/4, 1/2, 3/4) and it is longer than
    `min_edge`; a triangle whose centroid deviates by more than `tol` has all
    its edges split. Triangles with 1, 2 or 3 split edges become 2, 3 or 4
    triangles: no slivers, no T-junctions, closed meshes stay closed.
    Flat areas (sea, plains) are not refined at all."""
    for _ in range(max_iter):
        e = np.sort(np.r_[T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]], axis=1)
        ue, inv = np.unique(e, axis=0, return_inverse=True)
        inv = inv.ravel().reshape(3, -1).T
        A, B = V[ue[:, 0], :2], V[ue[:, 1], :2]
        L = np.linalg.norm(B - A, axis=1)
        za, zb = surf(A[:, 0], A[:, 1]), surf(B[:, 0], B[:, 1])
        err = np.zeros(len(ue))
        for t in (0.25, 0.5, 0.75):
            P = A + (B - A) * t
            err = np.maximum(err, np.abs(surf(P[:, 0], P[:, 1]) - (za + (zb - za) * t)))
        split = (err > tol) & (L > min_edge)
        C = (V[T[:, 0], :2] + V[T[:, 1], :2] + V[T[:, 2], :2]) / 3
        zc = (surf(V[T[:, 0], 0], V[T[:, 0], 1]) + surf(V[T[:, 1], 0], V[T[:, 1], 1]) +
              surf(V[T[:, 2], 0], V[T[:, 2], 1])) / 3
        bad_t = (np.abs(surf(C[:, 0], C[:, 1]) - zc) > tol) & (L[inv].max(1) > min_edge)
        split[inv[bad_t].ravel()] = True
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


def _canon_cross(P, Q, sP, sQ, L):
    """Point where segment PQ crosses the line s = L, computed on the
    lexicographically ordered endpoints -> bit-identical for both neighbours."""
    swap = (P[:, 0] > Q[:, 0]) | ((P[:, 0] == Q[:, 0]) &
                                  ((P[:, 1] > Q[:, 1]) | ((P[:, 1] == Q[:, 1]) & (P[:, 2] > Q[:, 2]))))
    P2 = np.where(swap[:, None], Q, P); Q2 = np.where(swap[:, None], P, Q)
    s1 = np.where(swap, sQ, sP); s2 = np.where(swap, sP, sQ)
    t = (L - s1) / (s2 - s1)
    return P2 + t[:, None] * (Q2 - P2)


def imprint(V, T, x0, y0, nx, ny, eps=1e-7):
    """Cut triangles exactly along the terrain triangulation: grid lines
    x = x0 + iR, y = y0 + jR and cell diagonals (x-x0)+(y-y0) = kR.
    Each triangle is clipped at once into the strips between consecutive
    lines (convex pieces, fan-triangulated), family after family. Every
    resulting triangle lies inside ONE planar terrain facet, so draping its
    vertices reproduces the terrain surface exactly (zero deviation).
    Crossing points are computed on canonically ordered edge endpoints:
    neighbours create bit-identical points, the exact weld at the end makes
    the mesh conforming (no cracks / T-junctions); closed meshes stay closed."""
    fams = [(lambda P: P[:, 0] - x0, nx - 1), (lambda P: P[:, 1] - y0, ny - 1),
            (lambda P: (P[:, 0] - x0) + (P[:, 1] - y0), nx + ny - 2)]
    for sfun, K in fams:
        s = sfun(V)
        S = s[T]
        k0 = np.clip(np.floor((S.min(1) + eps) / RES), 0, K - 1).astype(np.int64)
        k1 = np.clip(np.ceil((S.max(1) - eps) / RES) - 1, 0, K - 1).astype(np.int64)
        keep = k0 >= k1
        Tk = T[keep]
        cut = np.nonzero(~keep)[0]
        if len(cut):
            nrep = (k1 - k0 + 1)[cut]
            ti = np.repeat(cut, nrep)
            kk = np.repeat(k0[cut], nrep) + (np.arange(nrep.sum()) - np.repeat(np.cumsum(nrep) - nrep, nrep))
            L1 = np.where(kk == 0, -np.inf, kk * RES)
            L2 = np.where(kk == K - 1, np.inf, (kk + 1) * RES)
            tri = T[ti]
            pts, val = [], []
            for e in range(3):
                ia, ib = tri[:, e], tri[:, (e + 1) % 3]
                A, B = V[ia], V[ib]
                sa, sb = s[ia], s[ib]
                vin = (sa >= L1 - eps) & (sa <= L2 + eps)
                c1 = ((sa < L1 - eps) & (sb > L1 + eps)) | ((sa > L1 + eps) & (sb < L1 - eps))
                c2 = ((sa < L2 - eps) & (sb > L2 + eps)) | ((sa > L2 + eps) & (sb < L2 - eps))
                with np.errstate(invalid="ignore", divide="ignore"):
                    P1 = _canon_cross(A, B, sa, sb, np.where(c1, L1, 0.0))
                    P2 = _canon_cross(A, B, sa, sb, np.where(c2, L2, 0.0))
                    t1 = np.where(c1, (L1 - sa) / (sb - sa), 9.0)
                    t2 = np.where(c2, (L2 - sa) / (sb - sa), 9.0)
                first2 = t2 < t1
                Pf = np.where(first2[:, None], P2, P1); Ps = np.where(first2[:, None], P1, P2)
                cf = np.where(first2, c2, c1); cs = np.where(first2, c1, c2)
                pts += [A, Pf, Ps]
                val += [vin, cf, cs]
            P = np.stack(pts, 1)            # (m, 9, 3) boundary-ordered candidates
            ok = np.stack(val, 1)
            order = np.argsort(~ok, axis=1, kind="stable")
            P = np.take_along_axis(P, order[:, :, None], 1)
            cnt = ok.sum(1)
            newT, newV = [], []
            base = len(V)
            P = P[:, :5]                    # a triangle/strip intersection has <= 5 corners
            flat = P.reshape(-1, 3)
            idx = base + np.arange(len(P) * 5).reshape(-1, 5)
            for i in range(1, 4):
                m = cnt > i + 1
                newT.append(np.c_[idx[m, 0], idx[m, i], idx[m, i + 1]])
            V = np.vstack([V, flat])
            Tk = np.vstack([Tk] + newT)
        # exact weld + drop degenerate faces
        Vu, inv = np.unique(V, axis=0, return_inverse=True)
        T = inv.ravel()[Tk]
        T = T[(T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 2] != T[:, 0])]
        a, b, c = Vu[T[:, 0]], Vu[T[:, 1]], Vu[T[:, 2]]
        T = T[0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1) > 1e-12]
        used = np.unique(T)
        rm = -np.ones(len(Vu), np.int64); rm[used] = np.arange(len(used))
        V, T = Vu[used], rm[T]
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
    # always include every building
    dem = Dem()
    x0, y0 = np.floor(lo / RES) * RES
    x1, y1 = np.ceil(hi / RES) * RES
    terrain = Terrain(dem, x0, y0, x1, y1)
    print(f"terrain {terrain.Z.shape[1]}x{terrain.Z.shape[0]} @ {RES} m, "
          f"z {terrain.Z.min():.1f}..{terrain.Z.max():.1f} m ({time.time() - t0:.0f}s)")

    out_objs, solids, report_layers = [], [], {}
    chk = {"xy_roundtrip_max_m": 0.0, "rigid_height_change_max_m": 0.0,
           "rigid_float_gap_max_m": 0.0, "drape_offset_err_max_m": 0.0}
    for attr, V, F, valid in objs:
        lname = layers[attr.LayerIndex].Name
        if lname in SKIP_LAYERS:
            report_layers[lname] = "replaced by terrain"
            continue
        V2, F2, part, npart, st = clean_and_split(V, F)
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
        tmin = np.full(npart, np.inf)
        if len(sx):
            np.minimum.at(tmin, sp, terrain(sx, sy))
        dz = np.where(rigid, tmin - zmin, 0.0)
        # ---- thin parts: subdivide then drape
        keepF = rigid[part]
        Fr = F2[keepF]
        used = np.unique(Fr.ravel())
        remap = -np.ones(len(L), np.int64); remap[used] = np.arange(len(used))
        Vr = L[used].copy(); Vr[:, 2] += dz[vpart[used]]
        Fr = remap[Fr]
        # checks (rigid)
        if rigid.any():
            hr = np.full(npart, -np.inf); lr = np.full(npart, np.inf)
            np.maximum.at(hr, vpart[used], Vr[:, 2]); np.minimum.at(lr, vpart[used], Vr[:, 2])
            rp = rigid & np.isfinite(hr)
            chk["rigid_height_change_max_m"] = max(chk["rigid_height_change_max_m"],
                                                   float(np.abs((hr - lr) - (zmax - zmin))[rp].max()))
            gap = lr[sp] - terrain(sx, sy)   # >= 0 everywhere, == 0 at the lowest point
            gmin = np.full(npart, np.inf); np.minimum.at(gmin, sp, gap)
            chk["rigid_float_gap_max_m"] = max(chk["rigid_float_gap_max_m"],
                                               float(np.abs(gmin[rp & np.isfinite(gmin)]).max()))
        Fd = tris(F2[~keepF])
        Vd = np.zeros((0, 3)); Td = np.zeros((0, 3), np.int64)
        if len(Fd):
            usedd = np.unique(Fd.ravel())
            rm = -np.ones(len(L), np.int64); rm[usedd] = np.arange(len(usedd))
            Vd, Td = subdivide(L[usedd].copy(), rm[Fd], terrain, DRAPE_TOL)
            zorig = Vd[:, 2].copy()
            Vd[:, 2] = terrain(Vd[:, 0], Vd[:, 1]) + zorig + BASE_OFFSET
            chk["drape_offset_err_max_m"] = max(
                chk["drape_offset_err_max_m"],
                float(np.abs(Vd[:, 2] - terrain(Vd[:, 0], Vd[:, 1]) - zorig - BASE_OFFSET).max()))
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
            "lift_m": [float(dz[rigid].min()), float(dz[rigid].max())] if rigid.any() else None,
        }
        print(f"  {lname:32s} parts {npart:6d} rigid {rigid.sum():6d} draped tris {len(Td):8d} "
              f"({time.time() - t0:.0f}s)")

    write_rhino(model, layers, out_objs, terrain)
    save_cache(terrain, solids)
    rep = {"source": str(SRC.name), "origin_lon_lat": [ORIGIN_LON, ORIGIN_LAT],
           "terrain_grid": [int(terrain.Z.shape[1]), int(terrain.Z.shape[0])], "terrain_res_m": RES,
           "terrain_extent_local_m": [float(terrain.x[0]), float(terrain.y[0]),
                                      float(terrain.x[-1]), float(terrain.y[-1])],
           "terrain_z_m": [float(terrain.Z.min()), float(terrain.Z.max())],
           "checks": chk, "layers": report_layers, "runtime_s": round(time.time() - t0)}
    (ROOT / "results/relief_report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    print(json.dumps(chk, indent=1))


def add_mesh(out, V, F, attr):
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
    ta = r3.ObjectAttributes(); ta.LayerIndex = tl; ta.Name = "Terrain Copernicus GLO-30 15m"
    add_mesh(out, TV, TF, ta)
    OUT3DM.parent.mkdir(exist_ok=True)
    out.Write(str(OUT3DM), 8)
    print("wrote", OUT3DM, f"{OUT3DM.stat().st_size / 1e6:.0f} MB")


def save_cache(terrain, solids):
    CACHE.mkdir(parents=True, exist_ok=True)
    d = {"tx": terrain.x, "ty": terrain.y, "tz": terrain.Z}
    for i, (name, V, F, vp) in enumerate(solids):
        d[f"s{i}_V"], d[f"s{i}_F"], d[f"s{i}_P"] = V, F, vp
    d["names"] = np.array([s[0] for s in solids])
    np.savez_compressed(CACHE / "relief_parts.npz", **d)


if __name__ == "__main__":
    main()
