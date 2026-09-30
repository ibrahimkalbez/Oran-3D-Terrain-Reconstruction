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
  * every exported file is re-loaded and checked: watertight, consistent
    winding, positive volume, single body, no degenerate/duplicate faces
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
    n = len(top)
    bot = top.copy()
    bot[:, 2] = 0.0
    idx = np.arange(n).reshape(ny, nx)
    ring = np.r_[idx[0, :], idx[1:, -1], idx[-1, -2::-1], idx[-2:0:-1, 0]]
    a, b = ring, np.roll(ring, -1)
    walls = np.vstack([np.c_[a, a + n, b], np.c_[b, a + n, b + n]])
    return np.vstack([top, bot]), np.vstack([tf, tf[:, ::-1] + n, walls])


def to_manifold(V, T):
    m = mf.Manifold(mf.Mesh(vert_properties=np.ascontiguousarray(V, np.float32),
                            tri_verts=np.ascontiguousarray(T, np.uint32)))
    return m if m.status() == mf.Error.NoError and not m.is_empty() else None


def check(path):
    m = trimesh.load(path, process=True)
    bodies = m.split(only_watertight=False)
    return {
        "file": path.name, "size_MB": round(path.stat().st_size / 1e6, 1),
        "triangles": int(len(m.faces)), "vertices": int(len(m.vertices)),
        "dimensions_mm": m.extents.round(2).tolist(),
        "watertight": bool(m.is_watertight), "winding_consistent": bool(m.is_winding_consistent),
        "is_volume": bool(m.is_volume), "bodies": int(len(bodies)),
        "volume_cm3": round(float(m.volume) / 1000, 2),
        "degenerate_faces": int((~m.nondegenerate_faces()).sum()),
        "duplicate_faces": int(len(m.faces) - len(m.unique_faces())),
        "min_z_mm": round(float(m.bounds[0, 2]), 4),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--size-mm", type=float, default=200.0, help="longest side of the maquette")
    p.add_argument("--zexag", type=float, default=2.0, help="vertical exaggeration")
    p.add_argument("--base-mm", type=float, default=4.0, help="socle thickness under sea level")
    p.add_argument("--embed-mm", type=float, default=0.3, help="building bases sunk into terrain")
    p.add_argument("--terrain-step", type=int, default=2, help="HD terrain: every Nth 15 m node")
    p.add_argument("--tol-mm", type=float, default=0.02, help="OPT simplification tolerance")
    p.add_argument("--nozzle-mm", type=float, default=0.4)
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

    parts, stats = [terr], {"buildings_in": 0, "kept": 0, "open_or_invalid": 0, "outside": 0}
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
            Vm[Vm[:, 2] <= zb + 1e-9, 2] -= a.embed_mm
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
    assert solid.status() == mf.Error.NoError
    print(f"union: {solid.num_tri()} tris, genus {solid.genus()} ({time.time() - t0:.0f}s)")

    EXP.mkdir(exist_ok=True)
    report = {"scale": f"1:{round(1000 / k):,}", "mm_per_m": k, "zexag": a.zexag,
              "base_mm": a.base_mm, "embed_mm": a.embed_mm,
              "terrain_step_m": float((tx[1] - tx[0]) * a.terrain_step),
              "extent_m": [float(tx[-1] - tx[0]), float(ty[-1] - ty[0])], "parts": stats,
              "buildings_below_nozzle": int((np.array(min_dims) < a.nozzle_mm).sum()),
              "building_min_dim_mm_percentiles_5_50_95":
                  np.percentile(min_dims, [5, 50, 95]).round(3).tolist()}
    for tag, m in (("HD", solid), ("OPT", solid.simplify(a.tol_mm))):
        mesh = m.to_mesh64() if hasattr(m, "to_mesh64") else m.to_mesh()
        tm = trimesh.Trimesh(np.asarray(mesh.vert_properties)[:, :3], np.asarray(mesh.tri_verts),
                             process=False)
        for ext in ("stl", "obj"):
            path = EXP / f"Oran_maquette_{tag}.{ext}"
            tm.export(path)
            report[f"{tag}_{ext}"] = check(path)
            print(tag, ext, report[f"{tag}_{ext}"], f"({time.time() - t0:.0f}s)")
    (EXP / "print_report.json").write_text(json.dumps(report, indent=1))
    preview(tx, ty, tz, d, names, k, a)


def preview(tx, ty, tz, d, names, k, a):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LightSource
    res = 15.0
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
