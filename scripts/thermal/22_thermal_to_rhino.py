"""Step 22 - project the 10/07/2024 thermal raster onto the WHITE Rhino maquette.

The white maquette (Oran_maquette_blanche_complete_Rhino8.3dm, local Transverse
Mercator metres, EarthAnchor 35.699 N / 0.635 W) is read as is; every original
object is kept untouched and the thermal data are ADDED on a dedicated layer
tree that can be switched off independently:

THERMAL_ORAN_2024_07_10
  NUAGE_POINTS_THERMIQUE::NUAGE_<a>-<b>   coloured point cloud, 1 point per raster cell,
                                          point value = thermal value (Rhino point-cloud values),
                                          one sub-layer per thermal class -> zone by zone
  SURFACE_THERMIQUE_TERRAIN::SURF_<a>-<b> one coloured square per raster cell draped on the
                                          terrain (+DZ m), one sub-layer per class
  TOITURES_THERMIQUES                     copies of roof faces coloured with the raster value
                                          under each vertex (NOT a per-building measurement)
  LIMITE_ZONE_ETUDIEE                     outline of the study area on the terrain
  LEGENDE_THERMIQUE                       colour bar + text dots, next to the model

Usage: python scripts/thermal/22_thermal_to_rhino.py <white maquette .3dm> <output dir>
Outputs: <out>/Oran_Original_Backup.3dm (byte copy, SHA-256 checked),
         <out>/Oran_Thermal_2024_3D.3dm, results/thermal/rhino_thermal_report.json,
         results/thermal/category_samples.npz (for step 23)
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import rasterio
import rhino3dm as r3
from scipy.interpolate import RegularGridInterpolator

from thermal_common import LAYER, WORK, load_config, lut_colors

DZ_SURF = 1.0       # thermal surface above the terrain (m) - above draped streets (<= 0.5 m)
DZ_CLOUD = 3.0      # point cloud above the terrain (m)
DZ_ROOF = 0.3       # roof copies above the roofs (m)
CATEGORIES = {      # layer names of the white maquette (OSM keys cut to 10 characters) -> category
    "batiments": ("BUILDING", "HEIGHT_", "EXTRA_BUILD"),
    "routes_voies": ("HIGHWAY_", "ROUTE_ROAD", "ROUTE_TRAM", "ROUTE_RAILWAY", "ROUTE_TRAIN", "RAILWAY_",
                     "AEROWAY_RU", "AEROWAY_TA"),
    "espaces_verts": ("LEISURE_PA", "LEISURE_GA", "LEISURE_PI", "LANDUSE_GR", "LANDUSE_FO", "LANDUSE_ME",
                      "LANDUSE_OR", "LANDUSE_VI", "LANDUSE_FA", "LANDUSE_CE", "LANDUSE_V0", "NATURAL_GR",
                      "NATURAL_SC", "NATURAL_WO", "NATURAL_TR"),
    "eau": ("NATURAL_WA", "WATERWAY_", "BAY"),
}


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def mesh_np(g):
    V = np.array([[p.X, p.Y, p.Z] for p in g.Vertices], np.float64)
    F = np.array([tuple(g.Faces[k]) for k in range(g.Faces.Count)], np.int64).reshape(-1, 4)
    q = F[:, 2] != F[:, 3]
    return V, np.r_[F[:, [0, 1, 2]], F[q][:, [0, 2, 3]]]


def category(name):
    for c, pre in CATEGORIES.items():
        if name.startswith(pre):
            return c
    return None


def terrain_interp(model):
    """Regular terrain grid recovered from the top of the TERRAIN_ET_SOCLE block."""
    for o in model.Objects:
        if model.Layers[o.Attributes.LayerIndex].Name == "TERRAIN_ET_SOCLE":
            V = np.array([[p.X, p.Y, p.Z] for p in o.Geometry.Vertices])
            top = V[V[:, 2] > V[:, 2].min() + 1e-6]
            xs, ys = np.unique(top[:, 0]), np.unique(top[:, 1])
            Z = np.full((len(ys), len(xs)), np.nan)
            Z[np.searchsorted(ys, top[:, 1]), np.searchsorted(xs, top[:, 0])] = top[:, 2]
            if np.isnan(Z).any():
                raise RuntimeError("terrain top is not a full regular grid")
            return RegularGridInterpolator((ys, xs), Z, bounds_error=False, fill_value=np.nan), xs, ys
    raise RuntimeError("layer TERRAIN_ET_SOCLE not found")


def add_layer(model, name, color, parent=None):
    L = r3.Layer(); L.Name = name; L.Color = color
    if parent is not None:
        L.ParentLayerId = model.Layers[parent].Id
    return model.Layers.Add(L)


def attrs(layer, name):
    a = r3.ObjectAttributes(); a.LayerIndex = layer; a.Name = name
    return a


def add_colored_mesh(model, V, F, C, at):
    m = r3.Mesh()
    m.Vertices.UseDoublePrecisionVertices = True
    for x, y, z in V.tolist():
        m.Vertices.Add(x, y, z)
    for f in F.tolist():
        if len(f) == 4:
            m.Faces.AddFace(*f)
        else:
            m.Faces.AddFace(f[0], f[1], f[2])
    for c in C.tolist():
        m.VertexColors.Add(*c)
    m.Normals.ComputeNormals()
    return model.Objects.AddMesh(m, at)


def main():
    src, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    rep = {"source_model": src.name, "source_sha256": sha(src)}
    backup = out_dir / "Oran_Original_Backup.3dm"
    shutil.copy2(src, backup)
    rep["backup_identical"] = sha(backup) == rep["source_sha256"]
    lut = np.load(WORK / "legend_lut.npz"); lut_v, lut_rgb = lut["v"], lut["rgb"].astype(float)
    with rasterio.open(WORK / "Oran_Thermal_2024.tif") as ds:
        T = ds.read(1, masked=True).filled(np.nan).astype(np.float64); tr = ds.transform
    res = tr.a
    edges = json.loads((WORK / "georef_report.json").read_text())["class_edges"]
    model = r3.File3dm.Read(str(src))
    n_orig = len(model.Objects)
    terr, txs, tys = terrain_interp(model)
    ny, nx = T.shape
    cx = tr.c + (np.arange(nx) + 0.5) * res
    cy = tr.f - (np.arange(ny) + 0.5) * res
    X, Y = np.meshgrid(cx, cy)
    Zc = terr(np.c_[Y.ravel(), X.ravel()]).reshape(ny, nx)
    valid = np.isfinite(T) & np.isfinite(Zc)
    rep["raster_cells_valid"] = int(np.isfinite(T).sum())
    rep["raster_cells_on_model_terrain"] = int(valid.sum())
    rep["raster_fraction_on_model"] = float(valid.sum() / max(1, np.isfinite(T).sum()))
    rep["model_terrain_bounds_m"] = [float(txs[0]), float(tys[0]), float(txs[-1]), float(tys[-1])]

    root = add_layer(model, LAYER, (215, 25, 28, 255))
    l_cloud = add_layer(model, "NUAGE_POINTS_THERMIQUE", (253, 141, 60, 255), root)
    l_surf = add_layer(model, "SURFACE_THERMIQUE_TERRAIN", (255, 255, 51, 255), root)
    l_roof = add_layer(model, "TOITURES_THERMIQUES", (215, 25, 28, 255), root)
    l_lim = add_layer(model, "LIMITE_ZONE_ETUDIEE", (0, 0, 0, 255), root)
    l_leg = add_layer(model, "LEGENDE_THERMIQUE", (0, 0, 0, 255), root)
    unit = cfg.get("unit", "")
    cls = np.digitize(T, edges[1:-1])                     # 0 .. n-1
    rep["classes"] = {}
    for k, (lo, hi) in enumerate(zip(edges[:-1], edges[1:])):
        lab = f"{lo:g}-{hi:g}"
        sel = valid & (cls == k)
        n = int(sel.sum()); rep["classes"][lab] = n
        if not n:
            continue
        mid_rgb = tuple(int(c) for c in lut_colors([(lo + hi) / 2], lut_v, lut_rgb)[0]) + (255,)
        lc = add_layer(model, f"NUAGE_{lab}", mid_rgb, l_cloud)
        ls = add_layer(model, f"SURF_{lab}", mid_rgb, l_surf)
        vals = T[sel]; col = lut_colors(vals, lut_v, lut_rgb)
        # point cloud: colour + value per point
        pc = r3.PointCloud()
        up = r3.Vector3d(0, 0, 1)
        for x, y, z, v, c in zip(X[sel], Y[sel], Zc[sel] + DZ_CLOUD, vals, col.tolist()):
            pc.Add(r3.Point3d(x, y, z), up, (c[0], c[1], c[2], 255), float(v))
        model.Objects.AddPointCloud(pc, attrs(lc, f"Nuage thermique {lab} {unit} - 10/07/2024"))
        # draped squares: one square per cell, corners on the terrain
        h = res / 2
        off = np.array([[-h, -h], [h, -h], [h, h], [-h, h]])
        P = np.stack([X[sel], Y[sel]], -1)[:, None, :] + off[None]          # (n,4,2)
        Zq = terr(np.c_[P[..., 1].ravel(), P[..., 0].ravel()]).reshape(-1, 4)
        Zq = np.where(np.isfinite(Zq), Zq, Zc[sel][:, None]) + DZ_SURF
        V = np.c_[P.reshape(-1, 2), Zq.ravel()]
        F = np.arange(4 * n).reshape(n, 4)
        add_colored_mesh(model, V, F, np.repeat(col, 4, 0),
                         attrs(ls, f"Surface thermique {lab} {unit} - 10/07/2024"))

    def sample(xy):
        c = np.floor((xy[:, 0] - tr.c) / res).astype(int)
        r = np.floor((tr.f - xy[:, 1]) / res).astype(int)
        ok = (c >= 0) & (c < nx) & (r >= 0) & (r < ny)
        out = np.full(len(xy), np.nan); out[ok] = T[r[ok], c[ok]]
        return out

    # roofs + per-category samples (face centroid value, horizontal area weight)
    samples = {c: [] for c in CATEGORIES}
    roofV, roofF, roofC = [], [], []
    nroof = 0
    for o in list(model.Objects)[:n_orig]:
        g = o.Geometry
        if not isinstance(g, r3.Mesh):
            continue
        cat = category(model.Layers[o.Attributes.LayerIndex].Name)
        if cat is None:
            continue
        V, F = mesh_np(g)
        A, B, C = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
        nrm = np.cross(B - A, C - A); a2 = np.linalg.norm(nrm, axis=1)
        nz = np.divide(nrm[:, 2], a2, out=np.zeros_like(a2), where=a2 > 0)
        cen = (A + B + C) / 3
        up = np.abs(nz) > 0.9
        if cat == "batiments":
            ground = terr(np.c_[cen[:, 1], cen[:, 0]])
            up &= cen[:, 2] > ground + 1.5                                  # roofs, not floors
        val = sample(cen[up, :2])
        samples[cat].append(np.c_[val, 0.5 * np.abs(nrm[up, 2])])
        if cat == "batiments":
            Fr = F[up][np.isfinite(val)]
            if len(Fr):
                Vr = V[Fr].reshape(-1, 3); vv = sample(Vr[:, :2])
                vv = np.where(np.isfinite(vv), vv, np.repeat(val[np.isfinite(val)], 3))
                roofV.append(Vr + [0, 0, DZ_ROOF]); roofC.append(lut_colors(vv, lut_v, lut_rgb))
                roofF.append(np.arange(len(Vr)).reshape(-1, 3) + nroof); nroof += len(Vr)
                if nroof > 3_000_000:                                       # flush in chunks
                    add_colored_mesh(model, np.vstack(roofV), np.vstack(roofF) - np.vstack(roofF).min(),
                                     np.vstack(roofC), attrs(l_roof, "Toitures thermiques 10/07/2024"))
                    roofV, roofF, roofC, nroof = [], [], [], 0
    if roofV:
        add_colored_mesh(model, np.vstack(roofV), np.vstack(roofF) - np.vstack(roofF).min(),
                         np.vstack(roofC), attrs(l_roof, "Toitures thermiques 10/07/2024"))
    np.savez(WORK / "category_samples.npz",
             **{c: (np.vstack(s) if s else np.zeros((0, 2))) for c, s in samples.items()})
    rep["category_faces_sampled"] = {c: int(sum(len(x) for x in s)) for c, s in samples.items()}
    rep["category_faces_with_value"] = {c: int(sum(np.isfinite(x[:, 0]).sum() for x in s))
                                        for c, s in samples.items()}

    # study-area outline
    import geopandas as gpd
    sa = gpd.read_file(WORK / "Oran_Thermal_2024.gpkg", layer="study_area").geometry.iloc[0]
    for poly in getattr(sa, "geoms", [sa]):
        ring = np.array(poly.exterior.coords)
        z = terr(np.c_[ring[:, 1], ring[:, 0]]); z = np.where(np.isfinite(z), z, 0) + DZ_CLOUD
        model.Objects.AddPolyline(r3.Polyline([r3.Point3d(x, y, zz) for (x, y), zz in zip(ring[:, :2], z)]),
                                  attrs(l_lim, "Limite zone etudiee - carte 10/07/2024"))
    # legend bar beside the model (east side)
    x0 = float(np.nanmax(X[valid])) + 600; y0 = float(np.nanmin(Y[valid])); H = 4000.0; W = 300.0
    k = len(lut_v)
    ys = y0 + np.linspace(0, H, k + 1)
    Vl = np.vstack([[[x0, ys[i], 0], [x0 + W, ys[i], 0], [x0 + W, ys[i + 1], 0], [x0, ys[i + 1], 0]]
                    for i in range(k)])
    add_colored_mesh(model, Vl, np.arange(4 * k).reshape(k, 4), np.repeat(lut_rgb.astype(int), 4, 0),
                     attrs(l_leg, "Legende thermique"))
    for e in edges:
        y = y0 + H * (e - lut_v[0]) / (lut_v[-1] - lut_v[0])
        model.Objects.AddTextDot(f"{e:g} {unit}", r3.Point3d(x0 + W + 250, y, 0), attrs(l_leg, f"{e:g}"))
    model.Objects.AddTextDot(f"{cfg.get('quantity', '')} - 10/07/2024", r3.Point3d(x0, y0 + H + 400, 0),
                             attrs(l_leg, "titre"))

    invalid = [o.Attributes.Name for o in list(model.Objects)[n_orig:]
               if hasattr(o.Geometry, "IsValid") and not o.Geometry.IsValid]
    out = out_dir / "Oran_Thermal_2024_3D.3dm"
    model.Write(str(out), 8)
    chk = r3.File3dm.Read(str(out))
    names = [chk.Layers[i].FullPath for i in range(len(chk.Layers))]
    rep.update({"output": out.name, "output_MB": round(out.stat().st_size / 1e6, 1),
                "objects_original": n_orig, "objects_output": len(chk.Objects),
                "objects_thermal_added": len(chk.Objects) - n_orig, "invalid_thermal_objects": invalid,
                "thermal_layers": [n for n in names if n.startswith(LAYER)],
                "roof_triangles": int(sum(o.Geometry.Faces.Count for o in chk.Objects
                                          if chk.Layers[o.Attributes.LayerIndex].Name == "TOITURES_THERMIQUES")),
                "offsets_m": {"surface": DZ_SURF, "cloud": DZ_CLOUD, "roofs": DZ_ROOF}})
    (WORK / "rhino_thermal_report.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
