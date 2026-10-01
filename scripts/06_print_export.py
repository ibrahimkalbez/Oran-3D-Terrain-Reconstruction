"""Print-ready Oran maquette: terrain + buildings fused into ONE watertight
manifold solid standing on a flat solid base (socle), exported as STL + OBJ.

Input : results/cache/relief_parts.npz (from 05_build_oran_relief.py)
Output: export_3d_print/Oran_maquette_HD.{stl,obj}
        export_3d_print/Oran_maquette_OPT.{stl,obj}
        export_3d_print/print_report.json, results/print_preview_*.png

Steps
  * scale: longest side = --size-mm; heights x --zexag (terrain and buildings
    alike, so their proportions stay true)
  * socle: terrain surface + vertical walls + flat bottom at z = 0; sea level
    sits --base-mm above the bottom (solid block, no hollow)
  * every building = its own closed solid; its base is extended --embed-mm
    down into the terrain (hidden) so the boolean union is clean
  * union with manifold3d (exact, guaranteed manifold output)
  * OPT = HD simplified with manifold3d.simplify(--tol-mm) (stays manifold)
  * checks on the exported mesh: watertight, no non-manifold edge, consistent
    winding, single body, no degenerate face; the STL is re-read from disk
Parts that are open surfaces (aircraft models) or lie outside the terrain
are left out and counted in the report. Roads / land use (0.5 m thick) are
far below the printable size and are not part of the print solid.
"""
import argparse
import json
import time
from pathlib import Path

import manifold3d as mf
import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "results/cache/relief_parts.npz"  
EXP = ROOT / "export_3d_print"


def tris(F):
    q = F[:, 2] != F[:, 3]
    return np.r_[F[:, [0, 1, 2]], F[q][:, [0, 2, 3]]]


def terrain_block(tx, ty, tz, step, to_mm):
    """Solid socle: terrain surface on top, vertical walls, flat bottom at z = 0
    (bottom = fan on the boundary ring -> few triangles, fully conforming)."""
    iy = np.r_[np.arange(0, len(ty), step)]
    ix = np.r_[np.arange(0, len(tx), step)]
    if iy[-1] != len(ty) - 1:
        iy = np.r_[iy, len(ty) - 1]
    if ix[-1] != len(tx) - 1:
        ix = np.r_[ix, len(tx) - 1]
    X, Y = np.meshgrid(tx[ix], ty[iy])
    top = to_mm(np.c_[X.ravel(), Y.ravel(), tz[np.ix_(iy, ix)].ravel()])
    ny, nx = len(iy), len(ix)
    i = (np.arange(ny - 1)[:, None] * nx + np.arange(nx - 1)[None, :]).ravel()
    tf = np.vstack([np.c_[i, i + 1, i + nx], np.c_[i + 1, i + nx + 1, i + nx]])
    idx = np.arange(nx * ny).reshape(ny, nx)
    ring = np.r_[idx[0, :], idx[1:, -1], idx[-1, -2::-1], idx[-2:0:-1, 0]]   # CCW seen from above
    n = len(top)
    bot = top[ring].copy()
    bot[:, 2] = 0.0
    br = n + np.arange(len(ring))                  # bottom ring vertex ids
    c = n + len(ring)                              # bottom centre
    centre = np.r_[bot[:, :2].mean(0), 0.0]
    a_, b_ = ring, np.roll(ring, -1)
    ba, bb = br, np.roll(br, -1)
    walls = np.vstack([np.c_[a_, ba, b_], np.c_[b_, ba, bb]])
    bottom = np.c_[np.full(len(ring), c), bb, ba]  # facing down
    return np.vstack([top, bot, centre]), np.vstack([tf, walls, bottom])


def to_manifold(V, T):
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    if np.einsum("ij,ij->i", a, np.cross(b, c)).sum() < 0:   # inward normals -> flip
        T = T[:, [0, 2, 1]]
    m = mf.Manifold(mf.Mesh(vert_properties=np.ascontiguousarray(V, np.float32),
                            tri_verts=np.ascontiguousarray(T, np.uint32)))
    return m if m.status() == mf.Error.NoError and not m.is_empty() else None


def keep_main_body(V, T):
    """Largest connected body (by volume) + statistics of the removed ones.
    Uses the exact manifold topology of manifold3d's output (bodies touching
    only at a point/edge, e.g. zero-volume sheets, stay separate and are removed)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    e = np.r_[T[:, [0, 1]], T[:, [1, 2]]]
    _, lab = connected_components(coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])),
                                             shape=(len(V), len(V))), directed=False)
    tl = lab[T[:, 0]]
    P = V[T]
    vol = np.bincount(tl, weights=np.einsum("ij,ij->i", P[:, 0], np.cross(P[:, 1], P[:, 2])) / 6)
    main = int(np.argmax(vol))
    keep = tl == main
    others = np.setdiff1d(np.unique(tl), [main])
    info = {"count": int(len(others)), "volume_mm3": round(float(vol[others].sum()), 4),
            "largest_mm3": round(float(vol[others].max()), 4) if len(others) else 0.0}
    T = T[keep]
    used = np.unique(T)
    rm = -np.ones(len(V), np.int64); rm[used] = np.arange(len(used))
    return V[used], rm[T], info


def write_stl(path, V, T):
    """Binary STL written with numpy (fast, low memory)."""
    P = V[T].astype(np.float32)
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30)
    rec = np.zeros(len(T), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = n, P
    with open(path, "wb") as f:
        f.write(b"Oran maquette - Copernicus GLO-30 + OSM Rhino model, units mm".ljust(80, b" "))
        f.write(np.uint32(len(T)).tobytes())
        rec.tofile(f)


def write_obj(path, V, T, chunk=1_000_000):
    with open(path, "w") as f:
        f.write("# Oran maquette (mm)\n")
        for i in range(0, len(V), chunk):
            np.savetxt(f, V[i:i + chunk], fmt="v %.5f %.5f %.5f")
        for i in range(0, len(T), chunk):
            np.savetxt(f, T[i:i + chunk] + 1, fmt="f %d %d %d")


def verify_stl(path, V, T):
    raw = np.fromfile(path, dtype=np.uint8)
    n = int(np.frombuffer(raw[80:84].tobytes(), np.uint32)[0])
    rec = np.frombuffer(raw[84:].tobytes(), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    return bool(n == len(T) and len(rec) == n and
                np.allclose(rec["v"][:: max(1, n // 1000)], V[T][:: max(1, n // 1000)], atol=1e-4))


def check_mesh(V, T):
    """Watertight / manifold / orientation / bodies / degenerate checks (numpy)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    e = np.r_[T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]
    und = np.sort(e, 1)
    key = und[:, 0] * (len(V) + 1) + und[:, 1]
    _, cnt = np.unique(key, return_counts=True)
    dkey = e[:, 0] * (len(V) + 1) + e[:, 1]
    dup_directed = len(dkey) - len(np.unique(dkey))       # same directed edge twice = bad winding
    g = coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(len(V), len(V)))
    nb, lab = connected_components(g, directed=False)
    nb = len(np.unique(lab[np.unique(T)]))            # bodies among used vertices only
    P = V[T]
    area = 0.5 * np.linalg.norm(np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]), axis=1)
    vol = float(np.einsum("ij,ij->i", P[:, 0], np.cross(P[:, 1], P[:, 2])).sum() / 6)
    return {"triangles": int(len(T)), "vertices": int(len(V)),
            "dimensions_mm": (V.max(0) - V.min(0)).round(2).tolist(),
            "min_z_mm": round(float(V[:, 2].min()), 4),
            "watertight": bool((cnt == 2).all()), "open_edges": int((cnt == 1).sum()),
            "nonmanifold_edges": int((cnt > 2).sum()), "winding_consistent": dup_directed == 0,
            "bodies": int(nb), "volume_cm3": round(vol / 1000, 2),
            "degenerate_faces": int((area < 1e-12).sum())}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--size-mm", type=float, default=200.0, help="longest side of the maquette")
    p.add_argument("--zexag", type=float, default=2.0, help="vertical exaggeration")
    p.add_argument("--base-mm", type=float, default=4.0, help="socle thickness under sea level")
    p.add_argument("--embed-mm", type=float, default=0.3, help="building bases sunk into terrain")
    p.add_argument("--terrain-step", type=int, default=1, help="HD terrain: every Nth 25 m node")
    p.add_argument("--tol-mm", type=float, default=0.02, help="OPT simplification tolerance")
    p.add_argument("--nozzle-mm", type=float, default=0.4)
    p.add_argument("--limit", type=int, default=0, help="test run: only the first N parts")
    a = p.parse_args()
    t0 = time.time()
    d = np.load(CACHE)
    tx, ty, tz = d["tx"], d["ty"], d["tz"]
    x0, y0 = tx[0], ty[0]
    k = a.size_mm / max(tx[-1] - tx[0], ty[-1] - ty[0])       # mm per metre

    def to_mm(P):
        return np.c_[(P[:, 0] - x0) * k, (P[:, 1] - y0) * k, P[:, 2] * k * a.zexag + a.base_mm]

    TV, TT = terrain_block(tx, ty, tz, a.terrain_step, to_mm)
    terr = to_manifold(TV, TT)
    assert terr is not None, "terrain block not manifold"
    print(f"terrain block {len(TT)} tris, volume {terr.volume() / 1000:.1f} cm3")

    from scipy.interpolate import RegularGridInterpolator
    _g = RegularGridInterpolator((ty, tx), tz, bounds_error=False, fill_value=None)

    def ground(x, y):
        return _g(np.c_[y, x])

    parts, stats = [terr], {"buildings_in": 0, "kept": 0, "open_or_invalid": 0, "outside": 0,
                            "floating_extended": 0}
    min_dims = []
    names = d["names"]
    for i, name in enumerate(names):
        V, F, P = d[f"s{i}_V"], d[f"s{i}_F"], d[f"s{i}_P"]
        T = tris(F)
        tp = P[T[:, 0]]
        order = np.argsort(tp, kind="stable")
        T, tp = T[order], tp[order]
        cuts = np.r_[0, np.nonzero(np.diff(tp))[0] + 1, len(tp)]
        for s, e in zip(cuts[:-1], cuts[1:]):
            if a.limit and stats["buildings_in"] >= a.limit:
                break
            stats["buildings_in"] += 1
            Tp = T[s:e]
            u, inv = np.unique(Tp.ravel(), return_inverse=True)
            Vp = V[u].copy()
            if (Vp[:, 0].min() < tx[0] or Vp[:, 0].max() > tx[-1] or
                    Vp[:, 1].min() < ty[0] or Vp[:, 1].max() > ty[-1]):
                stats["outside"] += 1
                continue
            Vm = to_mm(Vp)
            zb = Vm[:, 2].min()
            base = Vm[:, 2] <= zb + 1e-9
            # ground under the part's bottom (print units); a part whose base is above
            # the ground (upper building part, footbridge...) is extended down to it
            gz = to_mm(np.c_[Vp[base, :2], ground(Vp[base, 0], Vp[base, 1])])[:, 2]
            if zb > gz.min() + 0.01:              # > 0.01 mm above the ground
                stats["floating_extended"] += 1
            Vm[base, 2] = min(zb, gz.min()) - a.embed_mm
            m = to_manifold(Vm, inv.reshape(-1, 3))
            if m is None:
                stats["open_or_invalid"] += 1
                continue
            ext = Vm[:, :2].max(0) - Vm[:, :2].min(0)
            min_dims.append(ext.min())
            parts.append(m)
            stats["kept"] += 1
    print(f"{stats} ({time.time() - t0:.0f}s)")
    solid = mf.Manifold.batch_boolean(parts, mf.OpType.Add)
    del parts
    assert solid.status() == mf.Error.NoError
    print(f"union: {solid.num_tri()} tris, genus {solid.genus()} ({time.time() - t0:.0f}s)", flush=True)
    # keep the main body (socle + everything standing on it); report any detached piece
    mesh = solid.to_mesh64() if hasattr(solid, "to_mesh64") else solid.to_mesh()
    del solid
    V = np.asarray(mesh.vert_properties)[:, :3].astype(np.float64)
    T = np.asarray(mesh.tri_verts).astype(np.int64)
    del mesh
    V, T, detached = keep_main_body(V, T)
    print("detached bodies removed:", detached, flush=True)
    EXP.mkdir(exist_ok=True)
    report = {"scale": f"1:{round(1000 / k):,}", "mm_per_m": k, "zexag": a.zexag,
              "base_mm": a.base_mm, "embed_mm": a.embed_mm,
              "terrain_step_m": float((tx[1] - tx[0]) * a.terrain_step),
              "extent_m": [float(tx[-1] - tx[0]), float(ty[-1] - ty[0])], "parts": stats,
              "detached_bodies_removed": detached,
              "buildings_below_nozzle": int((np.array(min_dims) < a.nozzle_mm).sum()),
              "building_min_dim_mm_percentiles_5_50_95":
                  np.percentile(min_dims, [5, 50, 95]).round(3).tolist()}
    for tag in ("HD", "OPT"):
        if tag == "OPT":
            m = mf.Manifold(mf.Mesh(vert_properties=V.astype(np.float32), tri_verts=T.astype(np.uint32)))
            m = m.simplify(a.tol_mm)
            mm = m.to_mesh64() if hasattr(m, "to_mesh64") else m.to_mesh()
            del m
            V = np.asarray(mm.vert_properties)[:, :3].astype(np.float64)
            T = np.asarray(mm.tri_verts).astype(np.int64)
            del mm
            V, T, report["OPT_simplify_detached"] = keep_main_body(V, T)
        write_stl(EXP / f"Oran_maquette_{tag}.stl", V, T)
        write_obj(EXP / f"Oran_maquette_{tag}.obj", V, T)
        report[tag] = check_mesh(V, T)
        report[tag]["stl_MB"] = round((EXP / f"Oran_maquette_{tag}.stl").stat().st_size / 1e6, 1)
        report[tag]["obj_MB"] = round((EXP / f"Oran_maquette_{tag}.obj").stat().st_size / 1e6, 1)
        report[tag]["stl_reread_ok"] = verify_stl(EXP / f"Oran_maquette_{tag}.stl", V, T)
        print(tag, report[tag], f"({time.time() - t0:.0f}s)", flush=True)
    (EXP / "print_report.json").write_text(json.dumps(report, indent=1))
    preview(tx, ty, tz, d, names, k, a)


def preview(tx, ty, tz, d, names, k, a):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LightSource
    res = float(tx[1] - tx[0])
    H = tz.copy()
    for i in range(len(names)):
        V, F = d[f"s{i}_V"], d[f"s{i}_F"]
        c = np.clip(np.round((V[:, 0] - tx[0]) / res).astype(int), 0, len(tx) - 1)
        r = np.clip(np.round((V[:, 1] - ty[0]) / res).astype(int), 0, len(ty) - 1)
        np.maximum.at(H, (r, c), V[:, 2])
    ls = LightSource(azdeg=315, altdeg=40)
    rgb = ls.shade(H[::-1] * a.zexag, cmap=plt.cm.gist_earth, vert_exag=3, dx=res, dy=res,
                   blend_mode="soft", vmin=-60, vmax=600)
    fig, ax = plt.subplots(figsize=(13, 11))
    ext = [0, (tx[-1] - tx[0]) * k, 0, (ty[-1] - ty[0]) * k]
    ax.imshow(rgb, extent=ext)
    ax.set(xlabel="mm", ylabel="mm",
           title=f"Maquette Oran - relief Copernicus + bâtiments (échelle 1:{round(1000 / k):,}, "
                 f"Z x{a.zexag})")
    fig.savefig(ROOT / "results/print_preview_top.png", dpi=110, bbox_inches="tight")


if __name__ == "__main__":
    main()
