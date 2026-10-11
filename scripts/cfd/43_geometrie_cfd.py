"""Step 43 - derived CFD geometry (Model A: aerodynamics, Model B: + thermal data),
built from the detailed Rhino 8 maquette WITHOUT modifying it.

Sources (read only)
  * Oran_maquette_urbaine_detaillee_Rhino8.3dm
      - buildings : layer BUILDINGS_EMPRISES_REELLES_OSM_GOOGLE_MICROSOFT (132 121 real footprints,
        closed prisms, heights from OSM storeys).  The main BUILDINGS::EXTRA_BUILD layer is NOT used for
        CFD: its footprints are generated (median IoU 0.03 with real buildings, rapport_maquette_urbaine §5)
        -> technical reason documented; nothing is deleted.
      - terrain   : TERRAIN_ET_SOCLE (Copernicus GLO-30, corrected Earth link, 25 m grid)
  * Oran_Thermal_2024_3D.3dm / results/thermal*/ : LST rasters (10/07/2024 and 1984-2024 stack)
      registered on the WHITE maquette coastline; the detailed maquette uses the corrected Earth link
      real = model + d(x,y) (dx = 3.58 - 9.84e-5 x - 6.25e-4 y ; dy = -32.69 - 1.35e-4 x + 4.518e-3 y),
      so a thermal value at real position r belongs to model position r - d(r).

Outputs (results/cfd/geometrie/)
  buildings_<SECTEUR>.stl     closed building prisms of the sector + 300 m buffer (solid <SECTEUR>)
  ground.stl                  terrain of the CFD domain (solid ground)
  batiments_cfd.csv           one row per building: id, sector, Rhino source (layer, object id,
                              component), centroid, footprint, height, base/roof z, LST 2024 under the
                              roof (corrected), LST stack 1984-2024
  geometrie_report.json
"""
import csv
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
import rhino3dm as r3
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results/cfd/geometrie"
REAL = "BUILDINGS_EMPRISES_REELLES_OSM_GOOGLE_MICROSO"      # prefix (full name truncated in some readers)
DOMAIN = (-5500.0, -4500.0, 11500.0, 5500.0)                # model metres, covers the 3 sectors
BUFFER = 300.0


def d_corr(x, y):
    return (3.5831863 - 9.84e-05 * x - 6.252e-04 * y, -32.6866685 - 1.353e-04 * x + 4.518e-03 * y)


def mesh_np(g):
    V = np.array([[p.X, p.Y, p.Z] for p in g.Vertices], np.float64)
    F = np.array([tuple(g.Faces[k]) for k in range(g.Faces.Count)], np.int64).reshape(-1, 4)
    q = F[:, 2] != F[:, 3]
    return V, np.r_[F[:, [0, 1, 2]], F[q][:, [0, 2, 3]]]


def weld(V, T, tol=1e-3):
    key = np.round(V / tol).astype(np.int64)
    _, idx, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return V[idx], inv.ravel()[T]


def components(nv, T):
    r = np.r_[T[:, 0], T[:, 1], T[:, 2]]; c = np.r_[T[:, 1], T[:, 2], T[:, 0]]
    A = coo_matrix((np.ones(len(r)), (r, c)), shape=(nv, nv))
    n, lab = connected_components(A, directed=False)
    return n, lab[T[:, 0]]


def write_stl(path, name, P):
    """P: (n,3,3) triangles, ascii STL with one named solid."""
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]
    with open(path, "w") as f:
        f.write(f"solid {name}\n")
        for t, nn in zip(P, n):
            f.write(f" facet normal {nn[0]:.5e} {nn[1]:.5e} {nn[2]:.5e}\n  outer loop\n")
            for v in t:
                f.write(f"   vertex {v[0]:.3f} {v[1]:.3f} {v[2]:.3f}\n")
            f.write("  endloop\n endfacet\n")
        f.write(f"endsolid {name}\n")


def sampler(tif):
    ds = rasterio.open(tif)
    A = ds.read(masked=True).filled(np.nan).astype(np.float64); tr = ds.transform

    def s(x, y):
        dx, dy = d_corr(x, y)                    # model -> real position of the thermal raster
        c = np.floor((x + dx - tr.c) / tr.a).astype(int); r = np.floor((tr.f - (y + dy)) / -tr.e).astype(int)
        ok = (c >= 0) & (c < A.shape[2]) & (r >= 0) & (r < A.shape[1])
        out = np.full((A.shape[0], len(x)), np.nan); out[:, ok] = A[:, r[ok], c[ok]]
        return out
    return s


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sect = json.loads((ROOT / "results/cfd/secteurs.json").read_text())
    m = r3.File3dm.Read(sys.argv[1])
    rep = {"source": Path(sys.argv[1]).name, "domain_m": DOMAIN}
    bld = []; terr = None
    for o in m.Objects:
        ln = m.Layers[o.Attributes.LayerIndex].FullPath
        if not isinstance(o.Geometry, r3.Mesh):
            continue
        if ln.split("::")[-1].startswith(REAL):
            bld.append((ln, str(o.Attributes.Id), o.Geometry))
        elif ln.split("::")[-1] == "TERRAIN_ET_SOCLE":
            terr = o.Geometry
    rep["building_layers_used"] = sorted({b[0] for b in bld})
    # ---- terrain grid -> ground.stl over the domain (+200 m)
    V = np.array([[p.X, p.Y, p.Z] for p in terr.Vertices])
    top = V[V[:, 2] > V[:, 2].min() + 1e-6]
    xs, ys = np.unique(top[:, 0]), np.unique(top[:, 1])
    Z = np.zeros((len(ys), len(xs)))
    Z[np.searchsorted(ys, top[:, 1]), np.searchsorted(xs, top[:, 0])] = top[:, 2]
    np.savez_compressed(OUT / "terrain_grid.npz", xs=xs, ys=ys, Z=Z)
    ix = (xs >= DOMAIN[0] - 200) & (xs <= DOMAIN[2] + 200); iy = (ys >= DOMAIN[1] - 200) & (ys <= DOMAIN[3] + 200)
    gx, gy, gz = xs[ix], ys[iy], Z[np.ix_(iy, ix)]
    X, Y = np.meshgrid(gx, gy); P = np.c_[X.ravel(), Y.ravel(), gz.ravel()]
    n2 = len(gx); i = (np.arange(len(gy) - 1)[:, None] * n2 + np.arange(n2 - 1)[None, :]).ravel()
    T = np.r_[np.c_[i, i + 1, i + n2 + 1], np.c_[i, i + n2 + 1, i + n2]]
    write_stl(OUT / "ground.stl", "ground", P[T])
    rep["ground"] = {"grid": [int(n2), int(len(gy))], "triangles": int(len(T)), "z_range": [float(gz.min()), float(gz.max())]}
    # ---- buildings: split the real-footprint meshes into individual closed prisms
    lst = sampler(ROOT / "results/thermal/Oran_Thermal_2024.tif")
    stack = sampler(ROOT / "results/thermal_evolution/Oran_LST_1984_2024_stack.tif")
    dates = json.loads((ROOT / "results/thermal_evolution/extract_all_report.json").read_text()).keys()
    rows = []; per = {k: [] for k in sect}
    for ln, oid, g in bld:
        Vb, Tb = mesh_np(g)
        c3 = Vb[Tb].mean(1)
        keep = np.zeros(len(Tb), bool)
        for k, s in sect.items():
            b = s["model_box_m"]
            keep |= (c3[:, 0] > b[0] - BUFFER) & (c3[:, 0] < b[2] + BUFFER) & (c3[:, 1] > b[1] - BUFFER) & (c3[:, 1] < b[3] + BUFFER)
        Tb = Tb[keep]
        if not len(Tb):
            continue
        Vw, Tw = weld(Vb, Tb)
        n, lab = components(len(Vw), Tw)
        order = np.argsort(lab, kind="stable"); cuts = np.flatnonzero(np.diff(lab[order])) + 1
        for grp in np.split(order, cuts):
            tri = Vw[Tw[grp]]
            xy = tri[:, :, :2].reshape(-1, 2)
            cx, cy = xy.mean(0); zmin, zmax = tri[:, :, 2].min(), tri[:, :, 2].max()
            sec = next((k for k, s in sect.items() if s["model_box_m"][0] - BUFFER < cx < s["model_box_m"][2] + BUFFER
                        and s["model_box_m"][1] - BUFFER < cy < s["model_box_m"][3] + BUFFER), None)
            if sec is None or zmax - zmin < 2.0:
                continue
            core = any(s["model_box_m"][0] < cx < s["model_box_m"][2] and s["model_box_m"][1] < cy < s["model_box_m"][3]
                       for s in [sect[sec]])
            nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
            roof = nrm[:, 2] > 0.9 * np.linalg.norm(nrm, axis=1)
            area = 0.5 * np.abs(nrm[roof, 2]).sum()
            rc = tri[roof].mean(1)
            v24 = lst(rc[:, 0], rc[:, 1])[0]
            st = stack(np.array([cx]), np.array([cy]))[:, 0]
            bid = f"{sec}_B{len(per[sec]) + 1:05d}"
            per[sec].append(tri)
            rows.append({"id": bid, "secteur": sec, "coeur_secteur": core, "rhino_calque": ln, "rhino_objet_id": oid,
                         "x": round(cx, 2), "y": round(cy, 2), "z_base": round(zmin, 2), "z_toit": round(zmax, 2),
                         "hauteur_m": round(zmax - zmin, 2), "emprise_m2": round(area, 1), "triangles": len(grp),
                         "lst_2024_toit_moy": round(float(np.nanmean(v24)), 2) if np.isfinite(v24).any() else "",
                         **{f"lst_{d[:4]}": (round(float(v), 2) if np.isfinite(v) else "") for d, v in zip(dates, st)}})
    with open(OUT / "batiments_cfd.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    rep["buildings"] = {}
    for k, tris in per.items():
        P = np.concatenate(tris)
        write_stl(OUT / f"buildings_{k}.stl", k, P)
        hs = [r["hauteur_m"] for r in rows if r["secteur"] == k]
        rep["buildings"][k] = {"n": len(tris), "n_core": sum(r["coeur_secteur"] for r in rows if r["secteur"] == k),
                               "triangles": int(len(P)), "h_median": float(np.median(hs)), "h_p95": float(np.percentile(hs, 95)),
                               "h_max": float(np.max(hs)),
                               "lst2024_with_value": sum(1 for r in rows if r["secteur"] == k and r["lst_2024_toit_moy"] != "")}
    rep["thermal_alignment_correction"] = "thermal raster sampled at model + d(x,y), d ~ (+3.6, -32.7) m near the centre"
    (OUT / "geometrie_report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    print(json.dumps(rep, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
