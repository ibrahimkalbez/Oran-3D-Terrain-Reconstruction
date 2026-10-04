"""Buildings: exact georeferencing, Copernicus seating, shapes and heights.

Input : results/Oran_maquette_blanche_complete_Rhino8.3dm   (white maquette, script 10)
        data/copernicus/Copernicus_DSM_N35_W00{1,2}.tif      (GLO-30 DSM)
        data/overture/oran_{buildings,segments}.parquet       (script 13b)
Output: results/cache/bati_14.pkl          (new terrain, rebuilt building meshes, georef)
        results/bati_georef_hauteurs_report.json

1. GEOREFERENCING CHECK (model <-> real world)
   The model and the real footprints (Overture = OSM + Google + Microsoft,
   WGS84) are rasterised at 0.5 m on 1.2 km tiles every 1.5 km; the FFT
   cross-correlation peak (sub-pixel parabola) gives the local shift
   real - model.  A weighted robust least-squares fit over all tiles gives an
   affine correction  d(x, y) = c0 + c1 x + c2 y  for each axis.
   The model XY is NOT changed (morphology, dimensions kept): only the link
   model -> Earth is corrected, i.e. where each model point reads the
   Copernicus relief.
2. TERRAIN: every node of the 25 m terrain grid re-reads Copernicus GLO-30
   (same bilinear sampling and sea rule as script 05) at its corrected
   position.  dT = T_new - T_old on the grid; every draped layer is moved by
   dT with the SAME triangulation (script 15), so draping stays exact.
3. COPERNICUS BUILDING SIGNAL: exact coverage of every DSM pixel by the
   footprints (polygon / pixel intersection) and regression DSM - local
   ground ~ coverage -> the DSM building signal (m) is measured and reported.
4. SHAPES: each footprint ring is cleaned with bounded, measured changes:
   duplicate points, spikes (turn > 170 deg), collinear points (turn < 1 deg
   and < 3 cm from the chord) are removed; near-right corners (0.5-6 deg off)
   are squared by least squares (edges parallel / perpendicular to the
   dominant axis, theta = arg(sum L e^{4i phi}) / 4) with vertices shared with
   a neighbouring building fixed; rejected if a vertex moves > 0.30 m, the
   area changes > 1 % or the ring becomes invalid.
5. HEIGHTS (storeys of 3.0 m):
   a) layers HEIGHT_xx (measured heights): kept exactly;
   b) footprints covered >= 50 % by an OSM building with height/levels:
      height tag, else levels x 3.0 m;
   c) otherwise spatial median of the OSM levels within 150 m (>= 5 tagged
      buildings; leave-one-out error reported);
   d) otherwise original height rounded to whole storeys.
6. Every building is rebuilt as a closed prism on the NEW terrain: roof =
   median ground of the footprint + height, base = lowest ground point of the
   footprint (foundation, nothing floats on slopes).  The real footprints
   (Overture) are also built, with the same rules, for an optional layer.
"""
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import rasterio
import shapely
from rasterio.features import rasterize
from rasterio.transform import from_origin
from scipy.ndimage import label, map_coordinates, uniform_filter
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).parent))
import urban_lib as u  # noqa: E402
from oran_georef import ORIGIN_LAT, ORIGIN_LON, _LOC, local_to_lonlat  # noqa: E402

ROOT = u.ROOT
TILES = [ROOT / f"data/copernicus/Copernicus_DSM_N35_W00{i}.tif" for i in (1, 2)]
OV_B = ROOT / "data/overture/oran_buildings.parquet"
OUT = u.CACHE / "bati_14.pkl"
REPORT = ROOT / "results/bati_georef_hauteurs_report.json"
STOREY = 3.0
PINCHED = []
BUILD_PREFIX = ("BUILDING", "EXTRA_BUILD", "HEIGHT_")
rep = {}


def log(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------ Copernicus
class Dem:
    """Bilinear Copernicus GLO-30 sampling, identical to script 05."""

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


class Georef:
    """model XY -> real LOCAL TM XY : p + d(p), d affine."""

    def __init__(self, cx=(0, 0, 0), cy=(0, 0, 0)):
        self.cx, self.cy = np.asarray(cx, float), np.asarray(cy, float)

    def d(self, x, y):
        return (self.cx[0] + self.cx[1] * x + self.cx[2] * y, self.cy[0] + self.cy[1] * x + self.cy[2] * y)

    def to_real(self, x, y):
        dx, dy = self.d(x, y)
        return x + dx, y + dy

    def to_model(self, X, Y):
        x, y = np.array(X, float), np.array(Y, float)
        for _ in range(6):
            dx, dy = self.d(x, y)
            x, y = X - dx, Y - dy
        return x, y

    def lonlat(self, x, y):
        return local_to_lonlat(*self.to_real(x, y))


def terrain_grid(dem, geo, x, y):
    """Script 05 terrain rule on the model grid, read through `geo`."""
    X, Y = np.meshgrid(x, y)
    Z = np.maximum(dem(*geo.lonlat(X, Y)), 0.0)
    lab, _ = label(Z <= 0.5)
    sea = np.isin(lab, np.setdiff1d(np.unique(lab[-1]), [0]))
    Z[sea] = 0.0
    return Z


# ------------------------------------------------------------------ footprints
def building_parts(M):
    """Every building part: layer, footprint polygon (shapely), original top z."""
    recs = []
    for ln in [k for k in M if k.startswith(BUILD_PREFIX)]:
        V, F = M[ln]
        T = u.tris(F)
        n, lab = u.mesh_parts(V, T)
        zmax = np.full(n, -np.inf)
        np.maximum.at(zmax, lab, V[:, 2])
        N = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
        top = (N[:, 2] > 0) & (np.abs(V[T, 2] - zmax[lab[T[:, 0]]][:, None]).max(1) < 1e-3)
        rings = {}
        for r in u.boundary_rings(T[top], V):
            P = V[r, :2]
            rings.setdefault(lab[r[0]], [[], []])[0 if u.ring_area(P) > 0 else 1].append(P)
        for p, (outs, holes) in rings.items():
            if not outs:
                continue
            if len(outs) == 1:
                g = shapely.Polygon(outs[0], holes)
            else:
                g = shapely.union_all([shapely.make_valid(shapely.Polygon(o)) for o in outs])
                if holes:
                    g = g.difference(shapely.union_all([shapely.make_valid(shapely.Polygon(h)) for h in holes]))
            if not g.is_valid:
                g = shapely.make_valid(g)
            g = shapely.union_all([q for q in shapely.get_parts(g) if q.geom_type == "Polygon"])
            if g.is_empty or g.area < 1.0:
                continue
            recs.append((ln, g, float(zmax[p])))
    return recs


def polys_only(g):
    parts = [q for q in shapely.get_parts(g) if q.geom_type == "Polygon" and q.area > 0.01]
    return shapely.MultiPolygon(parts) if len(parts) > 1 else (parts[0] if parts else shapely.Polygon())


def estimate_georef(model_polys, real_polys, step=1500, tile=1200, res=0.5):
    """FFT cross-correlation of building masks on tiles -> affine fit."""
    N = int(tile / res)
    xs = shapely.get_coordinates(shapely.centroid(model_polys))
    x0, y0 = np.percentile(xs, 2, axis=0)
    x1, y1 = np.percentile(xs, 98, axis=0)
    obs = []
    for cx in np.arange(x0, x1 + 1, step):
        for cy in np.arange(y0, y1 + 1, step):
            box = shapely.box(cx - tile / 2, cy - tile / 2, cx + tile / 2, cy + tile / 2)
            pa = model_polys[shapely.intersects(model_polys, box)]
            qa = real_polys[shapely.intersects(real_polys, box)]
            if len(pa) < 30 or len(qa) < 30:
                continue
            tr = from_origin(cx - tile / 2, cy + tile / 2, res, res)
            A = rasterize(list(pa), out_shape=(N, N), transform=tr).astype(np.float32)
            B = rasterize(list(qa), out_shape=(N, N), transform=tr).astype(np.float32)
            a, b = A - A.mean(), B - B.mean()
            cc = np.fft.irfft2(np.fft.rfft2(a) * np.conj(np.fft.rfft2(b)), s=A.shape)
            r, c = np.unravel_index(np.argmax(cc), cc.shape)

            def sub(m, p, c0):
                den = m - 2 * c0 + p
                return 0.0 if den == 0 else 0.5 * (m - p) / den
            dr = sub(cc[(r - 1) % N, c], cc[(r + 1) % N, c], cc[r, c])
            dc = sub(cc[r, (c - 1) % N], cc[r, (c + 1) % N], cc[r, c])
            r = (r if r < N / 2 else r - N) + dr
            c = (c if c < N / 2 else c - N) + dc
            q = cc.max() / np.sqrt((a ** 2).sum() * (b ** 2).sum())
            obs.append((cx, cy, -c * res, r * res, q, len(pa)))   # real - model (x east, y north)
    obs = np.array(obs)
    obs = obs[obs[:, 4] > 0.15]
    X = np.c_[np.ones(len(obs)), obs[:, 0], obs[:, 1]]
    w = obs[:, 4]
    coef, stats = [], {}
    for k, name in [(2, "dx"), (3, "dy")]:
        c, *_ = np.linalg.lstsq(X * w[:, None], obs[:, k] * w, rcond=None)
        res_ = obs[:, k] - X @ c
        m = np.abs(res_) < 3 * 1.4826 * np.median(np.abs(res_))
        c, *_ = np.linalg.lstsq((X * w[:, None])[m], (obs[:, k] * w)[m], rcond=None)
        res_ = obs[:, k] - X @ c
        coef.append(c)
        stats[name] = {"coef_c0_cx_cy": [round(float(v), 7) for v in c], "tiles_used": int(m.sum()),
                       "residual_rms_m": round(float(np.sqrt(np.mean(res_[m] ** 2))), 2)}
    stats["tiles"] = int(len(obs))
    stats["observations"] = [[round(float(v), 2) for v in o] for o in obs]
    return Georef(coef[0], coef[1]), stats


# ------------------------------------------------------------------ shapes
def turn_deg(P):
    e0 = P - np.roll(P, 1, 0)
    e1 = np.roll(P, -1, 0) - P
    cr = e0[:, 0] * e1[:, 1] - e0[:, 1] * e1[:, 0]
    return np.degrees(np.arctan2(cr, (e0 * e1).sum(1)))


def clean_ring(P, fixed):
    """Remove duplicates, spikes and collinear points. Returns P, counts."""
    n_sp = n_col = 0
    changed = True
    while changed and len(P) > 3:
        changed = False
        e = np.roll(P, -1, 0) - P
        dup = np.hypot(*e.T) < 0.01
        if dup.any():
            P, fixed = P[~dup], fixed[~dup]
            changed = True
            continue
        t = np.abs(turn_deg(P))
        prv, nxt = np.roll(P, 1, 0), np.roll(P, -1, 0)
        ch = nxt - prv
        dist = np.abs(ch[:, 0] * (P - prv)[:, 1] - ch[:, 1] * (P - prv)[:, 0]) / (np.hypot(*ch.T) + 1e-12)
        spike = (t > 170) & ~fixed
        col = (t < 1.0) & (dist < 0.03) & ~fixed
        k = np.nonzero(spike)[0] if spike.any() else np.nonzero(col)[0]
        if len(k):
            i = k[0]   # one at a time, then re-evaluate
            if spike.any():
                n_sp += 1
            else:
                n_col += 1
            P, fixed = np.delete(P, i, 0), np.delete(fixed, i)
            changed = True
    return P, fixed, n_sp, n_col


def square_ring(P, fixed, max_move=0.30, tol_deg=6.0):
    """Least-squares squaring of near-right corners. Returns P or None."""
    e = np.roll(P, -1, 0) - P
    L = np.hypot(*e.T)
    phi = np.arctan2(e[:, 1], e[:, 0])
    th = np.angle(np.sum(L * np.exp(4j * phi))) / 4
    dev = np.degrees(np.abs(((phi - th) + np.pi / 4) % (np.pi / 2) - np.pi / 4))
    t = np.abs(turn_deg(P))
    off = np.abs(t - 90)
    if not ((off > 0.5) & (off <= tol_deg)).any():
        return None
    n = len(P)
    rows, rhs = [], []
    W = 100.0
    for i in range(n):
        if dev[i] > tol_deg:
            continue
        a = phi[i] - th
        k = np.round(a / (np.pi / 2))
        ang = th + k * np.pi / 2
        nrm = np.array([-np.sin(ang), np.cos(ang)])
        j = (i + 1) % n
        r = np.zeros(2 * n)
        r[2 * j:2 * j + 2] += nrm * W
        r[2 * i:2 * i + 2] -= nrm * W
        rows.append(r)
        rhs.append(0.0)
    for i in range(n):
        w = 1e4 if fixed[i] else 1.0
        for k in range(2):
            r = np.zeros(2 * n)
            r[2 * i + k] = w
            rows.append(r)
            rhs.append(w * P[i, k])
    sol = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)[0].reshape(n, 2)
    mv = np.hypot(*(sol - P).T)
    if mv.max() > max_move:
        return None
    return sol, float(mv.max())


def regularise(polys, shared_keys):
    st = {"spikes_removed": 0, "collinear_removed": 0, "rings_squared": 0, "rings_square_rejected": 0,
          "max_vertex_move_m": 0.0, "max_squaring_move_m": 0.0, "max_area_change_pct": 0.0,
          "buildings_changed": 0}
    out = []
    for g in polys:
        new_parts, changed = [], False
        for pg in shapely.get_parts(g):
            rings = [np.asarray(pg.exterior.coords)[:-1]] + [np.asarray(h.coords)[:-1] for h in pg.interiors]
            nr = []
            for P in rings:
                key = np.round(P / 0.01).astype(np.int64)
                fixed = np.array([(a, b) in shared_keys for a, b in key.tolist()])
                P2, fx2, ns, nc = clean_ring(P, fixed)
                st["spikes_removed"] += ns
                st["collinear_removed"] += nc
                S = square_ring(P2, fx2) if len(P2) >= 4 else None
                if S is not None:
                    P2 = S[0]
                    st["rings_squared"] += 1
                    st["max_squaring_move_m"] = max(st["max_squaring_move_m"], S[1])
                changed |= (ns + nc > 0) or S is not None
                nr.append(P2)
            q = shapely.Polygon(nr[0], [h for h in nr[1:] if len(h) >= 3]) if len(nr[0]) >= 3 else pg
            if not q.is_valid or abs(q.area - pg.area) > 0.01 * pg.area:
                q = pg
                st["rings_square_rejected"] += 1
            else:
                st["max_area_change_pct"] = max(st["max_area_change_pct"], 100 * abs(q.area - pg.area) / pg.area)
                if changed:
                    # Hausdorff distance = largest displacement of the outline
                    st["max_vertex_move_m"] = max(st["max_vertex_move_m"], float(shapely.hausdorff_distance(q, pg)))
            new_parts.append(q)
        st["buildings_changed"] += changed
        out.append(new_parts[0] if len(new_parts) == 1 else shapely.MultiPolygon(new_parts))
    st["max_outline_change_incl_spikes_m"] = round(st.pop("max_vertex_move_m"), 3)
    st["max_squaring_move_m"] = round(st["max_squaring_move_m"], 3)
    st["max_area_change_pct"] = round(st["max_area_change_pct"], 3)
    return np.array(out, dtype=object), st


# ------------------------------------------------------------------ prisms
def prisms(polys, heights, terrain):
    """Closed prisms seated on `terrain`. Returns V, F (tri as quad with c==d), base, roof."""
    polys = np.asarray(polys, dtype=object)
    # rings touching themselves at a vertex ("pinched", valid in GIS) would give
    # 4 faces on one vertical edge: such footprints are offset inwards by 2 mm
    pinched = np.array([len(c) - len(shapely.get_rings(p)) > len(np.unique(np.round(c, 4), axis=0))
                        for p, c in ((p, shapely.get_coordinates(p)) for p in polys)])
    if pinched.any():
        fixed = shapely.buffer(polys[pinched], -0.002, join_style="mitre")
        polys = polys.copy()
        polys[pinched] = [polys_only(g) if not g.is_empty else q for g, q in zip(fixed, polys[pinched])]
    PINCHED.append(int(pinched.sum()))
    tg = shapely.constrained_delaunay_triangles(polys)
    tparts, tidx = shapely.get_parts(tg, return_index=True)
    tri = shapely.get_coordinates(tparts).reshape(-1, 4, 2)[:, :3]
    cr = (tri[:, 1, 0] - tri[:, 0, 0]) * (tri[:, 2, 1] - tri[:, 0, 1]) - \
         (tri[:, 1, 1] - tri[:, 0, 1]) * (tri[:, 2, 0] - tri[:, 0, 0])
    tri[cr < 0] = tri[cr < 0][:, ::-1]
    keep = np.abs(cr) > 1e-9
    tri, tidx = tri[keep], tidx[keep]
    # rings (exterior CCW, holes CW) -> wall edges
    oriented = shapely.orient_polygons(polys) if hasattr(shapely, "orient_polygons") else \
        np.array([shapely.geometry.polygon.orient(p) if p.geom_type == "Polygon" else
                  shapely.MultiPolygon([shapely.geometry.polygon.orient(q) for q in p.geoms]) for p in polys], dtype=object)
    rings, ridx = shapely.get_rings(shapely.get_parts(oriented, return_index=True)[0], return_index=True)
    pidx = shapely.get_parts(oriented, return_index=True)[1][ridx]
    rc, rci = shapely.get_coordinates(rings, return_index=True)
    nxt_same = np.r_[rci[1:] == rci[:-1], False]
    ea, eb = rc[:-1][nxt_same[:-1]], rc[1:][nxt_same[:-1]]
    eidx = pidx[rci[:-1][nxt_same[:-1]]]
    # seating samples: ring vertices + triangle centroids
    sx = np.r_[rc[:, 0], tri.mean(1)[:, 0]]
    sy = np.r_[rc[:, 1], tri.mean(1)[:, 1]]
    sp = np.r_[pidx[rci], tidx]
    sz = terrain(sx, sy)
    nb = len(polys)
    base = np.full(nb, np.inf)
    np.minimum.at(base, sp, sz)
    o = np.lexsort((sz, sp))
    sps, szs = sp[o], sz[o]
    st = np.r_[0, np.nonzero(np.diff(sps))[0] + 1]
    cnt = np.diff(np.r_[st, len(sps)])
    gmed = np.full(nb, np.nan)
    gmed[sps[st]] = 0.5 * (szs[st + (cnt - 1) // 2] + szs[st + cnt // 2])
    roof = gmed + heights
    # vertices
    nt, ne = len(tri), len(ea)
    top = np.c_[tri.reshape(-1, 2), np.repeat(roof[tidx], 3)]
    bot = np.c_[tri[:, ::-1].reshape(-1, 2), np.repeat(base[tidx], 3)]
    wall = np.c_[np.r_[ea, eb, eb, ea], np.r_[base[eidx], base[eidx], roof[eidx], roof[eidx]]]
    V = np.r_[top, bot, wall.reshape(4, ne, 3).transpose(1, 0, 2).reshape(-1, 3)]
    ft = np.arange(3 * nt).reshape(-1, 3)
    F = np.r_[np.c_[ft, ft[:, 2]], np.c_[ft + 3 * nt, ft[:, 2] + 3 * nt],
              6 * nt + np.arange(4 * ne).reshape(-1, 4)]
    owner = np.r_[np.repeat(tidx, 3), np.repeat(tidx, 3), np.repeat(eidx, 4)]
    Vw, Fw = u_weld(V, F, owner)
    return Vw, Fw, base, roof, gmed


def u_weld(V, F, owner):
    """Weld identical vertices of the same building only (neighbours stay separate solids)."""
    key = np.c_[np.round(V * 1e4).astype(np.int64), owner]
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    Vu = V[first]
    F = inv.ravel()[F]
    tri = F[:, 2] == F[:, 3]
    a, b, c, d = F.T
    ok = (a != b) & (b != c) & (c != a) & (tri | ((d != a) & (d != b)))
    return Vu, F[ok]


def closed_check(V, F):
    T = u.tris(F)
    E = np.sort(np.r_[T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]], 1)
    _, c = np.unique(E, axis=0, return_counts=True)
    return {"open_edges": int((c == 1).sum()), "nonmanifold_edges": int((c > 2).sum())}


# ------------------------------------------------------------------ main
def main():
    t0 = time.time()
    data = u.load_maquette()
    M = data["meshes"]
    T0 = u.Terrain.from_block(M["TERRAIN_ET_SOCLE"][0])
    dem = Dem()
    log("model loaded", round(time.time() - t0))

    recs = building_parts(M)
    lay = np.array([r[0] for r in recs])
    polys = np.array([r[1] for r in recs], dtype=object)
    zmax0 = np.array([r[2] for r in recs])
    log("building parts", len(recs), round(time.time() - t0))

    # real footprints (Overture), WGS84 -> LOCAL TM
    ob = pq.read_table(OV_B).to_pylist()
    rg = shapely.make_valid(shapely.transform(shapely.from_wkb([r["geometry"] for r in ob]),
                                              lambda xy: np.c_[_LOC.transform(xy[:, 0], xy[:, 1])]))
    rg = np.array([shapely.union_all([q for q in shapely.get_parts(g) if q.geom_type == "Polygon"]) for g in rg],
                  dtype=object)

    # 1. georeferencing
    gcache = u.CACHE / "georef_14.pkl"
    if gcache.exists():
        geo, gst = pickle.load(open(gcache, "rb"))
    else:
        geo, gst = estimate_georef(polys, rg)
        pickle.dump((geo, gst), open(gcache, "wb"))
    rep["georeferencing"] = gst
    xs = shapely.get_coordinates(shapely.centroid(polys))
    dxy = np.c_[geo.d(xs[:, 0], xs[:, 1])]
    rep["georeferencing"]["shift_over_buildings_m"] = {
        "norm_min_median_max": np.percentile(np.hypot(*dxy.T), [0, 50, 100]).round(1).tolist(),
        "mean_dx_dy": dxy.mean(0).round(1).tolist()}
    lon0, lat0 = geo.lonlat(np.array([0.0]), np.array([0.0]))
    rep["georeferencing"]["model_origin_lonlat_corrected"] = [round(float(lon0[0]), 7), round(float(lat0[0]), 7)]
    log("georef", gst["dx"], gst["dy"], round(time.time() - t0))

    # 2. terrain re-read at the corrected positions
    Zchk = terrain_grid(dem, Georef(), T0.x, T0.y)
    Znew = terrain_grid(dem, geo, T0.x, T0.y)
    dZ = Znew - T0.Z
    rep["terrain"] = {"reproduce_old_terrain_max_err_m": round(float(np.abs(Zchk - T0.Z).max()), 4),
                      "dT_pct_1_5_50_95_99_m": np.percentile(dZ, [1, 5, 50, 95, 99]).round(2).tolist(),
                      "dT_abs_max_m": round(float(np.abs(dZ).max()), 1)}
    T1 = u.Terrain(T0.x, T0.y, Znew)
    log("terrain", rep["terrain"], round(time.time() - t0))

    # 3. Copernicus building signal (exact coverage, corrected placement)
    rep["copernicus_building_signal"] = dsm_signal(rg, dem, geo)
    log("dsm signal", rep["copernicus_building_signal"], round(time.time() - t0))

    # 4. shapes
    rings_, ri = shapely.get_rings(shapely.get_parts(polys), return_index=True)
    xy, ci = shapely.get_coordinates(rings_, return_index=True)
    last = np.r_[ci[1:] != ci[:-1], True]           # drop the closing point of each ring
    kk = np.unique(np.c_[np.round(xy[~last] / 0.01).astype(np.int64), ci[~last]], axis=0)
    k, c = np.unique(kk[:, :2], axis=0, return_counts=True)
    shared = set(map(tuple, k[c > 1].tolist()))   # vertex keys used by >1 ring point (neighbours)
    polys2, sst = regularise(polys, shared)
    polys2 = np.array([g if g.is_valid else polys_only(shapely.make_valid(g)) for g in polys2], dtype=object)
    rep["shapes"] = sst
    log("shapes", sst, round(time.time() - t0))

    # 5. heights
    h0 = zmax0 - np.array([np.median(T0(*shapely.get_coordinates(p).T)) for p in polys])
    real_m = shapely.make_valid(shapely.transform(rg, lambda xy: np.c_[geo.to_model(xy[:, 0], xy[:, 1])]))
    real_m = np.array([polys_only(g) for g in real_m], dtype=object)
    lev = np.array([r["num_floors"] if r["num_floors"] is not None else np.nan for r in ob], float)
    hh = np.array([r["height"] if r["height"] is not None else np.nan for r in ob], float)
    tagged = ~np.isnan(lev) | ~np.isnan(hh)
    tag_h = np.where(~np.isnan(hh), hh, lev * STOREY)
    tag_lev = np.where(~np.isnan(lev), lev, np.round(hh / STOREY))
    H, src = assign_heights(polys2, lay, h0, real_m[tagged], tag_h[tagged], tag_lev[tagged])
    rep["heights"] = height_report(H, h0, src, real_m[tagged], tag_lev[tagged])
    log("heights", rep["heights"], round(time.time() - t0))

    # 6. rebuild prisms per layer on the new terrain
    out_layers = {}
    for ln in np.unique(lay):
        m = np.nonzero(lay == ln)[0]
        # every polygon part is its own closed solid (parts touching along an
        # edge would otherwise give non-manifold edges)
        parts, pi = shapely.get_parts(polys2[m], return_index=True)
        V, F, base, roof, gm = prisms(parts, H[m][pi], T1)
        out_layers[ln] = (V, F)
    chk = {k: closed_check(*v) for k, v in out_layers.items()}
    chk = {"open_edges": sum(v["open_edges"] for v in chk.values()),
           "nonmanifold_edges": sum(v["nonmanifold_edges"] for v in chk.values())}
    rep["rebuilt_buildings"] = {"parts": int(len(polys2)), "closed_check_all_layers": chk,
                                "pinched_footprints_offset_2mm": int(sum(PINCHED))}
    # real footprints layer (optional, hidden in Rhino)
    keep = shapely.area(real_m) >= 10
    rp = real_m[keep]
    Hr, srcr = assign_heights(rp, np.array(["REAL"] * len(rp)), np.full(len(rp), np.nan),
                              real_m[tagged], tag_h[tagged], tag_lev[tagged], default_lev=None)
    srcs = np.array([(r["sources"][0]["dataset"] if r["sources"] else "?") for r in ob])[keep]
    rparts, rpi = shapely.get_parts(rp, return_index=True)
    Vr, Fr, *_ = prisms(rparts, Hr[rpi], T1)
    rep["real_footprints_layer"] = {"buildings": int(len(rp)),
                                    "sources": {s: int((srcs == s).sum()) for s in np.unique(srcs)},
                                    "height_sources": {s: int((srcr == s).sum()) for s in np.unique(srcr)},
                                    **closed_check(Vr, Fr)}
    log("prisms", rep["rebuilt_buildings"], rep["real_footprints_layer"], round(time.time() - t0))

    pickle.dump({"georef": (geo.cx, geo.cy), "terrain_x": T0.x, "terrain_y": T0.y, "Z_old": T0.Z, "Z_new": Znew,
                 "buildings": out_layers, "real_buildings": (Vr, Fr),
                 "storeys": {"layer": lay, "H": H, "src": src}}, open(OUT, "wb"), protocol=4)
    rep["runtime_s"] = round(time.time() - t0)
    REPORT.write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    log("done", rep["runtime_s"])


def assign_heights(polys, lay, h0, tag_polys, tag_h, tag_lev, default_lev="orig", R=150.0, kmin=5):
    n = len(polys)
    H = np.full(n, np.nan)
    src = np.array(["?"] * n, dtype=object)
    known = np.char.startswith(lay.astype(str), "HEIGHT_")
    H[known] = h0[known]
    src[known] = "mesure_calque_HEIGHT"
    tree = shapely.STRtree(tag_polys)
    qi, ti = tree.query(polys, predicate="intersects")
    inter = shapely.area(shapely.intersection(polys[qi], tag_polys[ti]))
    frac = inter / shapely.area(polys[qi])
    best = np.full(n, -1)
    bf = np.zeros(n)
    for a, b, f in zip(qi, ti, frac):
        if f > bf[a]:
            bf[a], best[a] = f, b
    m = (~known) & (bf >= 0.5)
    H[m] = tag_h[best[m]]
    src[m] = "OSM_etages_ou_hauteur"
    # spatial median of tagged storeys
    tc = shapely.get_coordinates(shapely.centroid(tag_polys))
    kd = cKDTree(tc)
    pc_ = shapely.get_coordinates(shapely.centroid(polys))
    rest = np.isnan(H)
    nbs = kd.query_ball_point(pc_[rest], R)
    imp = np.array([np.median(tag_lev[j]) if len(j) >= kmin else np.nan for j in nbs])
    idx = np.nonzero(rest)[0]
    ok = ~np.isnan(imp)
    H[idx[ok]] = np.maximum(1, np.round(imp[ok])) * STOREY
    src[idx[ok]] = "mediane_spatiale_OSM_150m"
    rest = np.isnan(H)
    if default_lev == "orig":
        H[rest] = np.maximum(1, np.round(h0[rest] / STOREY)) * STOREY
        src[rest] = "origine_arrondie_etages"
    else:
        H[rest] = np.round(np.median(tag_lev)) * STOREY
        src[rest] = "mediane_globale_OSM"
    return H, src


def height_report(H, h0, src, tag_polys, tag_lev, R=150.0, kmin=5):
    tc = shapely.get_coordinates(shapely.centroid(tag_polys))
    kd = cKDTree(tc)
    err = []
    for i, j in enumerate(kd.query_ball_point(tc, R)):
        j = [k for k in j if k != i]
        if len(j) >= kmin:
            err.append(np.median(tag_lev[j]) - tag_lev[i])
    err = np.abs(np.array(err))
    base = np.abs(tag_lev - np.median(tag_lev)).mean()
    return {"sources": {s: int((src == s).sum()) for s in np.unique(src)},
            "osm_tagged_buildings": int(len(tag_lev)),
            "osm_levels_median_p90": [float(np.median(tag_lev)), float(np.percentile(tag_lev, 90))],
            "imputation_leave_one_out": {"n": int(len(err)), "MAE_storeys": round(float(err.mean()), 2),
                                         "within_1_storey": round(float((err <= 1).mean()), 3),
                                         "baseline_global_median_MAE": round(float(base), 2)},
            "height_before_pct_5_50_95": np.percentile(h0[np.isfinite(h0)], [5, 50, 95]).round(1).tolist(),
            "height_after_pct_5_50_95": np.percentile(H, [5, 50, 95]).round(1).tolist(),
            "mean_abs_change_m": round(float(np.nanmean(np.abs(H - h0))), 2)}


def dsm_signal(polys_real_local, dem, geo):
    """Regression (DSM - local ground) ~ exact building coverage of each pixel,
    real footprints at their true position (LOCAL TM)."""
    ds_ = dem.t[0]
    A = ds_.transform
    b = shapely.total_bounds(polys_real_local)
    lon, lat = local_to_lonlat(np.array([b[0], b[2]]), np.array([b[1], b[3]]))
    c0 = int((lon.min() - A.c) / A.a) - 5
    c1 = int((lon.max() - A.c) / A.a) + 5
    r0 = int((lat.max() - A.f) / A.e) - 5
    r1 = int((lat.min() - A.f) / A.e) + 5
    D = dem.a[0][r0:r1, c0:c1]

    def topix(xy):
        lo, la = local_to_lonlat(xy[:, 0], xy[:, 1])
        return np.c_[(lo - A.c) / A.a - c0, (la - A.f) / A.e - r0]
    P = shapely.transform(polys_real_local, topix)
    bb = shapely.bounds(P)
    cmin, rmin = np.floor(bb[:, 0]).astype(int), np.floor(bb[:, 1]).astype(int)
    nc, nr = np.floor(bb[:, 2]).astype(int) - cmin + 1, np.floor(bb[:, 3]).astype(int) - rmin + 1
    cnt = nc * nr
    bi = np.repeat(np.arange(len(P)), cnt)
    off = np.arange(cnt.sum()) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    cc, rr = cmin[bi] + off % nc[bi], rmin[bi] + off // nc[bi]
    ar = shapely.area(shapely.intersection(P[bi], shapely.box(cc, rr, cc + 1, rr + 1)))
    ny, nx = D.shape
    ok = (rr >= 0) & (rr < ny) & (cc >= 0) & (cc < nx)
    cov = np.zeros(ny * nx)
    np.add.at(cov, rr[ok] * nx + cc[ok], ar[ok])
    cov = cov.reshape(ny, nx)
    num = uniform_filter(np.where(cov < 0.05, D, 0), 7)
    den = uniform_filter((cov < 0.05).astype(float), 7)
    G = np.where(den > 0.2, num / np.maximum(den, 1e-9), np.nan)
    slope = np.hypot(*np.gradient(np.nan_to_num(G), 30))
    m = (den > 0.2) & (slope < 0.03) & (D > 2)
    y, x = (D - G)[m], cov[m]
    k = np.linalg.lstsq(np.c_[x, np.ones_like(x)], y, rcond=None)[0]
    bins = {}
    for lo_, hi_ in [(0, .05), (.05, .3), (.3, .6), (.6, 1.01)]:
        s = (x >= lo_) & (x < hi_)
        bins[f"cover_{lo_}-{hi_}"] = {"pixels": int(s.sum()), "dsm_minus_ground_median_m": round(float(np.median(y[s])), 2)}
    return {"pixels_flat_area": int(m.sum()), "dsm_m_per_unit_cover": round(float(k[0]), 2), "bins": bins,
            "conclusion": "GLO-30 (30 m) keeps only a few metres of the building volumes: "
                          "not usable for individual heights; used for the ground (seating)."}


if __name__ == "__main__":
    main()
