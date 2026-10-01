"""Step 3-5 - Put the flat Rhino model of Oran on the Copernicus relief and
export a clean, watertight, print-ready model.

Principle (see documentation/methodologie_fusion.md)
  * The Rhino geometry is never redrawn: XY stays point-for-point identical
    (only the georeferencing transform is applied to every point).
  * Buildings (closed solids) are lifted as rigid blocks: dz = lowest terrain
    altitude under the footprint, so nothing floats. Heights are preserved.
  * Curves (streets) and open surfaces are draped vertex by vertex on the DEM
    (bilinear interpolation); curves are densified so they follow the slope.
  * Terrain = Copernicus GLO-30 resampled on the model extent.
  * Print model = terrain block + buildings merged with a robust boolean union
    (manifold3d) -> single watertight manifold solid, STL + OBJ.

Georeferencing (choose one)
  (auto)       model coordinates already in UTM 30N (EPSG:32630)
  --gcp FILE   CSV "model_x,model_y,lon,lat" (>= 2 points) -> similarity fit
  --offset DX DY [--rot DEG]  model -> UTM translation / rotation

Usage
  python scripts/04_fuse_rhino_terrain.py data/rhino/brahim_ib9a.3dm \
      --size-mm 200 --zexag 1.5
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
import rhino3dm as r3
import trimesh
from rasterio.warp import Resampling, reproject, transform as warp_tf
from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import gaussian_filter

sys.path.insert(0, str(Path(__file__).parent))
from rhino_utils import curve_points, geometry_to_mesh, iter_objects, mesh_to_np, unit_scale  # noqa

ROOT = Path(__file__).resolve().parents[1]
DEM_SRC = ROOT / "data/copernicus/Copernicus_DSM_N35_W001.tif"
UTM = "EPSG:32630"


# --------------------------------------------------------------------------- georef
def similarity_from_gcp(path):
    rows = list(csv.DictReader(open(path)))
    src = np.array([[float(r["model_x"]), float(r["model_y"])] for r in rows])
    lon = [float(r["lon"]) for r in rows]
    lat = [float(r["lat"]) for r in rows]
    ex, ny = warp_tf("EPSG:4326", UTM, lon, lat)
    dst = np.c_[ex, ny]
    # Umeyama similarity (rotation + uniform scale + translation)
    ms, md = src.mean(0), dst.mean(0)
    s0, d0 = src - ms, dst - md
    U, S, Vt = np.linalg.svd(d0.T @ s0 / len(src))
    D = np.diag([1, np.sign(np.linalg.det(U @ Vt))])
    R = U @ D @ Vt
    scale = np.trace(np.diag(S) @ D) / (s0 ** 2).sum(1).mean()
    t = md - scale * R @ ms
    res = dst - (scale * (R @ src.T).T + t)
    return scale, R, t, np.linalg.norm(res, axis=1)


def make_georef(a, model_lo, to_m):
    if a.gcp:
        s, R, t, res = similarity_from_gcp(a.gcp)
        info = {"mode": "gcp", "scale": s, "rot_deg": float(np.degrees(np.arctan2(R[1, 0], R[0, 0]))),
                "residuals_m": res.round(3).tolist()}
        f = lambda p: np.c_[(s * (R @ p[:, :2].T)).T + t, p[:, 2] * to_m]
        return f, info
    if a.offset:
        th = np.radians(a.rot)
        R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
        t = np.array(a.offset)
        f = lambda p: np.c_[(R @ (p[:, :2] * to_m).T).T + t, p[:, 2] * to_m]
        return f, {"mode": "offset", "offset": a.offset, "rot_deg": a.rot}
    x, y = model_lo[0] * to_m, model_lo[1] * to_m
    if not (1e5 < x < 9e5 and 3.8e6 < y < 4.1e6):
        sys.exit("Model is not in UTM 30N coordinates: give --gcp points.csv or --offset DX DY")
    return (lambda p: p * to_m), {"mode": "already_utm30n"}


# --------------------------------------------------------------------------- DEM
def load_dem(x0, y0, x1, y1, res, smooth):
    w, h = int(round((x1 - x0) / res)) + 1, int(round((y1 - y0) / res)) + 1
    dt = rasterio.Affine(res, 0, x0, 0, -res, y0 + (h - 1) * res)
    with rasterio.open(DEM_SRC) as src:
        dem = np.full((h, w), np.nan, "float32")
        reproject(rasterio.band(src, 1), dem, dst_transform=dt, dst_crs=UTM,
                  resampling=Resampling.bilinear, dst_nodata=np.nan)
    if np.isnan(dem).any():
        sys.exit("Model extent is outside the Copernicus tile in data/copernicus/")
    dem = np.maximum(dem, 0.0)
    if smooth > 0:
        dem = gaussian_filter(dem, smooth / res)
    xs = x0 + np.arange(w) * res
    ys = y0 + np.arange(h) * res
    grid = dem[::-1]  # row 0 = south
    interp = RegularGridInterpolator((ys, xs), grid, bounds_error=False, fill_value=None)
    return xs, ys, grid, lambda xy: interp(np.c_[xy[:, 1], xy[:, 0]])


def terrain_solid(xs, ys, z, base):
    h, w = z.shape
    X, Y = np.meshgrid(xs, ys)
    top = np.c_[X.ravel(), Y.ravel(), z.ravel()]
    i = (np.arange(h - 1)[:, None] * w + np.arange(w - 1)[None, :]).ravel()
    tf = np.vstack([np.c_[i, i + 1, i + w], np.c_[i + 1, i + w + 1, i + w]])
    n = len(top)
    bot = top.copy()
    bot[:, 2] = base
    idx = np.arange(n).reshape(h, w)
    ring = np.r_[idx[0, :], idx[1:, -1], idx[-1, -2::-1], idx[-2:0:-1, 0]]
    a_, b_ = ring, np.roll(ring, -1)
    walls = np.vstack([np.c_[a_, a_ + n, b_], np.c_[b_, a_ + n, b_ + n]])
    m = trimesh.Trimesh(np.vstack([top, bot]), np.vstack([tf, tf[:, ::-1] + n, walls]))
    if m.volume < 0:
        m.invert()
    return m


def footprint_samples(v, step):
    lo, hi = v[:, :2].min(0), v[:, :2].max(0)
    gx = np.arange(lo[0], hi[0] + step, step)
    gy = np.arange(lo[1], hi[1] + step, step)
    g = np.array(np.meshgrid(gx, gy)).reshape(2, -1).T
    return np.vstack([v[:, :2], g])


def repair(m):
    m.merge_vertices()
    m.update_faces(m.nondegenerate_faces())
    m.update_faces(m.unique_faces())
    m.remove_unreferenced_vertices()
    if not m.is_watertight:
        trimesh.repair.fill_holes(m)
    trimesh.repair.fix_normals(m)
    return m


# --------------------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser()
    p.add_argument("model", nargs="?", default=str(ROOT / "data/rhino/brahim_ib9a.3dm"))
    p.add_argument("--gcp")
    p.add_argument("--offset", nargs=2, type=float)
    p.add_argument("--rot", type=float, default=0.0)
    p.add_argument("--margin", type=float, default=150.0, help="terrain margin around model (m)")
    p.add_argument("--terrain-res", type=float, default=10.0, help="terrain mesh step (m), HD")
    p.add_argument("--terrain-res-opt", type=float, default=20.0, help="terrain step, optimised")
    p.add_argument("--smooth", type=float, default=0.0, help="gaussian smoothing of DSM (m)")
    p.add_argument("--embed", type=float, default=1.0, help="sink building bases (m) for union")
    p.add_argument("--base", type=float, default=5.0, help="print base thickness (mm)")
    p.add_argument("--size-mm", type=float, default=200.0, help="longest side of the print")
    p.add_argument("--zexag", type=float, default=1.0, help="vertical exaggeration")
    p.add_argument("--min-feature-mm", type=float, default=0.8, help="nozzle-printable width")
    a = p.parse_args()

    model = r3.File3dm.Read(a.model)
    if model is None:
        sys.exit(f"Cannot read {a.model}")
    unit, to_m = unit_scale(model)
    layers = {i: model.Layers[i].FullPath for i in range(len(model.Layers))}

    # ---- collect geometry (model units, blocks expanded)
    solids, curves, surfaces, skipped = [], [], [], {}
    for g, attr, xf in iter_objects(model):
        lay = layers.get(attr.LayerIndex, "?")
        if isinstance(g, r3.Curve):
            pts = curve_points(g, 5.0 / to_m)
            curves.append((lay, pts if xf is None else _xf(pts, xf)))
            continue
        res = geometry_to_mesh(g)
        if res is None:
            skipped[type(g).__name__] = skipped.get(type(g).__name__, 0) + 1
            continue
        v, f, how = res
        v = v if xf is None else _xf(v, xf)
        closed = trimesh.Trimesh(v, f, process=True).is_watertight or how == "extrusion"
        (solids if closed else surfaces).append((lay, v, f, how))

    allpts = np.vstack([s[1] for s in solids + surfaces] + [c[1] for c in curves])
    geo, ginfo = make_georef(a, allpts.min(0), to_m)

    # ---- terrain on model extent
    U = geo(allpts)
    q = max(a.terrain_res, a.terrain_res_opt)  # common grid for HD and OPT
    x0, y0 = np.floor((U[:, :2].min(0) - a.margin) / q) * q
    x1, y1 = np.ceil((U[:, :2].max(0) + a.margin) / q) * q
    xs, ys, Z, zat = load_dem(x0, y0, x1, y1, a.terrain_res, a.smooth)

    # ---- buildings: rigid vertical lift, XY untouched
    built, methods, lifts = [], {}, []
    for lay, v, f, how in solids:
        w = geo(v)
        zg = zat(footprint_samples(w, a.terrain_res)).min()
        dz = zg - w[:, 2].min()
        w = w + [0, 0, dz]
        base = np.isclose(w[:, 2], zg, atol=1e-6)
        w[base, 2] -= a.embed  # sink the base slightly for a clean union
        built.append((lay, w, f, how))
        methods[how] = methods.get(how, 0) + 1
        lifts.append(dz)
    # ---- open surfaces / meshes: draped vertex by vertex
    draped_srf = [(lay, np.c_[geo(v)[:, :2], zat(geo(v)[:, :2]) + geo(v)[:, 2]], f, how)
                  for lay, v, f, how in surfaces]
    # ---- curves (streets): draped point by point
    draped_crv = []
    for lay, pts in curves:
        w = geo(pts)
        dense = [w[0]]
        for p0, p1 in zip(w[:-1], w[1:]):
            n = max(1, int(np.ceil(np.linalg.norm(p1[:2] - p0[:2]) / a.terrain_res)))
            dense += [p0 + (p1 - p0) * k / n for k in range(1, n + 1)]
        dense = np.array(dense)
        dense[:, 2] = zat(dense[:, :2]) + dense[:, 2]
        draped_crv.append((lay, dense))

    origin = np.array([x0, y0, 0.0])
    write_fused_3dm(model, layers, built, draped_srf, draped_crv, xs, ys, Z, origin)

    # ---- print models
    exp = ROOT / "export_3d_print"
    exp.mkdir(exist_ok=True)
    span = max(x1 - x0, y1 - y0)
    k = a.size_mm / span  # mm per metre
    report = {"units": unit, "georef": ginfo, "origin_utm": origin[:2].tolist(),
              "extent_m": [x1 - x0, y1 - y0], "scale": f"1:{round(1000 / k):,}",
              "mm_per_m": k, "zexag": a.zexag, "buildings": len(built),
              "building_mesh_methods": methods, "open_surfaces": len(surfaces),
              "curves": len(curves), "skipped": skipped,
              "building_lift_m": [float(np.min(lifts)), float(np.max(lifts))] if lifts else None,
              "elev_range_m": [float(Z.min()), float(Z.max())]}
    small = [float(np.ptp(w[:, :2], 0).min() * k) for _, w, _, _ in built]
    report["buildings_below_min_feature"] = int(sum(s < a.min_feature_mm for s in small))

    for tag, res in (("HD", a.terrain_res), ("OPT", a.terrain_res_opt)):
        xs_, ys_, Z_, _ = (xs, ys, Z, None) if res == a.terrain_res else load_dem(x0, y0, x1, y1, res, a.smooth)
        report[tag] = build_print(tag, xs_, ys_, Z_, built, origin, k, a, exp)

    (ROOT / "results/fusion_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))


def _xf(pts, m):
    h = np.c_[pts, np.ones(len(pts))] @ m.T
    return h[:, :3] / h[:, 3:4]


def to_print(v, origin, k, zexag, zmin):
    q = v - origin
    q[:, :2] *= k
    q[:, 2] = (q[:, 2] - zmin) * k * zexag
    return q


def build_print(tag, xs, ys, Z, built, origin, k, a, exp):
    import manifold3d as mf
    zmin = float(Z.min())
    X = (xs - origin[0]) * k
    Y = (ys - origin[1]) * k
    Zp = (Z - zmin) * k * a.zexag
    terr = terrain_solid(X, Y, Zp, -a.base)
    t = _to_manifold(terr)
    if t is None:
        sys.exit(f"{tag}: terrain block is not a valid manifold")
    parts, bad, dropped = [t], 0, 0
    for _, w, f, _ in built:
        m = repair(trimesh.Trimesh(to_print(w.copy(), origin, k, a.zexag, zmin), f, process=True))
        if not m.is_watertight:
            m = m.convex_hull
            bad += 1
        mm = _to_manifold(m)
        if mm is None:
            dropped += 1
        else:
            parts.append(mm)
    solid = mf.Manifold.batch_boolean(parts, mf.OpType.Add)
    out = solid.to_mesh()
    res = trimesh.Trimesh(np.asarray(out.vert_properties)[:, :3], np.asarray(out.tri_verts), process=True)
    repair(res)
    stl, obj = exp / f"oran_print_{tag}.stl", exp / f"oran_print_{tag}.obj"
    res.export(stl)
    res.export(obj)
    chk = trimesh.load(stl)
    return {
        "stl": stl.name, "obj": obj.name, "triangles": len(res.faces),
        "size_mm": chk.extents.round(2).tolist(),
        "watertight": bool(chk.is_watertight), "winding_consistent": bool(chk.is_winding_consistent),
        "bodies": len(chk.split(only_watertight=False)), "volume_cm3": round(chk.volume / 1000, 2),
        "buildings_hull_fallback": bad, "buildings_rejected": dropped,
    }


def _to_manifold(m):
    import manifold3d as mf
    mesh = mf.Mesh(vert_properties=np.asarray(m.vertices, np.float32),
                   tri_verts=np.asarray(m.faces, np.uint32))
    out = mf.Manifold(mesh)
    if out.status() != mf.Error.NoError or out.is_empty():
        print("  manifold rejected:", out.status())
        return None
    return out


def write_fused_3dm(src, layers, built, srfs, crvs, xs, ys, Z, origin):
    """Fused model in real metres, local origin = `origin` (UTM 30N)."""
    out = r3.File3dm()
    out.Settings.ModelUnitSystem = r3.UnitSystem.Meters
    lon, lat = warp_tf(UTM, "EPSG:4326", [origin[0]], [origin[1]])
    ea = out.Settings.EarthAnchorPoint
    ea.EarthBasepointLatitude, ea.EarthBasepointLongitude = lat[0], lon[0]
    ea.Description = f"Local origin = UTM 30N (EPSG:32630) E {origin[0]:.1f} N {origin[1]:.1f}"
    out.Settings.EarthAnchorPoint = ea
    lidx = {}
    for i in range(len(src.Layers)):
        L = r3.Layer()
        L.Name, L.Color = src.Layers[i].Name, src.Layers[i].Color
        lidx[layers[i]] = out.Layers.Add(L)
    t = r3.Layer()
    t.Name = "Terrain_Copernicus_GLO30"
    tl = out.Layers.Add(t)

    def add_mesh(v, f, layer):
        m = r3.Mesh()
        for p in v - origin:
            m.Vertices.Add(*map(float, p))
        for a, b, c in f:
            m.Faces.AddFace(int(a), int(b), int(c))
        at = r3.ObjectAttributes()
        at.LayerIndex = layer
        out.Objects.AddMesh(m, at)

    for lay, v, f, _ in built + srfs:
        add_mesh(v, f, lidx.get(lay, 0))
    for lay, pts in crvs:
        pl = r3.Polyline([r3.Point3d(*map(float, p)) for p in pts - origin])
        at = r3.ObjectAttributes()
        at.LayerIndex = lidx.get(lay, 0)
        out.Objects.AddPolyline(pl, at)
    X, Y = np.meshgrid(xs, ys)
    h, w = Z.shape
    i = (np.arange(h - 1)[:, None] * w + np.arange(w - 1)[None, :]).ravel()
    add_mesh(np.c_[X.ravel(), Y.ravel(), Z.ravel()],
             np.vstack([np.c_[i, i + 1, i + w], np.c_[i + 1, i + w + 1, i + w]]), tl)
    path = ROOT / "results/oran_fused_relief.3dm"
    out.Write(str(path), 7)
    print("wrote", path)


if __name__ == "__main__":
    main()
