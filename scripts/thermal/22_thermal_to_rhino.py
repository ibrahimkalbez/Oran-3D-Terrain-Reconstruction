"""Step 22 - project the 10/07/2024 thermal raster onto the WHITE Rhino maquette.

The white maquette (Oran_maquette_blanche_complete_Rhino8.3dm, local Transverse
Mercator metres, EarthAnchor 35.699 N / 0.635 W) is read as is; every original
object is kept untouched and the thermal data are ADDED on a dedicated layer
tree that can be switched off independently:

THERMAL_ORAN_2024_07_10
  Colours are carried by the LAYERS: one layer per 1 degC class, layer colour = exact
  legend colour at the class centre, plus a Rhino render material of the same colour;
  every thermal object takes "colour from layer" and "material from layer".
  NUAGE_POINTS_THERMIQUE::NUAGE_<a>-<b>   point cloud, 1 point per raster cell, value (degC)
                                          attached to each point
  SURFACE_THERMIQUE_TERRAIN::SURF_<a>-<b> one square per raster cell draped on the terrain (+DZ m)
  TOITURES_THERMIQUES::TOIT_<a>-<b>       copies of the roof faces, class of the raster cell under
                                          the face centre (NOT a per-building measurement)
  ZONES_CHAUDES / ZONES_FRAICHES          outlines of the 8 largest areas > P90 / < P10 with a
                                          label (mean, extreme, area) - zone by zone reading
  GRILLE_1KM                              1 km squares draped on the terrain + label with the
                                          mean / min / max value of each square
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


def add_layer(model, name, color, parent=None, material=None):
    L = r3.Layer(); L.Name = name; L.Color = color
    if parent is not None:
        L.ParentLayerId = model.Layers[parent].Id
    if material is not None:
        L.RenderMaterialIndex = material
    return model.Layers.Add(L)


def add_material(model, name, rgb):
    """Plain Rhino material of the exact legend colour (shaded / rendered views)."""
    m = r3.Material(); m.Name = name
    m.DiffuseColor = m.AmbientColor = rgb
    m.SpecularColor = (0, 0, 0, 255); m.Shine = 0.0
    model.Materials.Add(m)
    return len(model.Materials) - 1


def attrs(layer, name, color=None, material=None):
    """Objects follow their LAYER colour and LAYER material, unless an explicit
    colour/material is given (legend swatches)."""
    a = r3.ObjectAttributes(); a.LayerIndex = layer; a.Name = name
    if color is None:
        a.ColorSource = r3.ObjectColorSource.ColorFromLayer
        a.MaterialSource = r3.ObjectMaterialSource.MaterialFromLayer
    else:
        a.ColorSource = r3.ObjectColorSource.ColorFromObject; a.ObjectColor = color
        a.MaterialSource = r3.ObjectMaterialSource.MaterialFromObject; a.MaterialIndex = material
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
    if C is not None:
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
    styles = {}                                           # class -> (label, rgb, material, roof layer)
    for k, (lo, hi) in enumerate(zip(edges[:-1], edges[1:])):
        lab = f"{lo:g}-{hi:g}"
        sel = valid & (cls == k)
        n = int(sel.sum()); rep["classes"][lab] = n
        if not n:
            continue
        # exact legend colour at the class centre -> layer colour + render material
        rgb = tuple(int(c) for c in lut_colors([(lo + hi) / 2], lut_v, lut_rgb)[0]) + (255,)
        mat = add_material(model, f"LST_2024_{lab}", rgb)
        lc = add_layer(model, f"NUAGE_{lab}", rgb, l_cloud, mat)
        ls = add_layer(model, f"SURF_{lab}", rgb, l_surf, mat)
        lr = add_layer(model, f"TOIT_{lab}", rgb, l_roof, mat)
        styles[k] = (lab, rgb, mat, lr)
        vals = T[sel]
        # point cloud: one point per cell, colour = layer colour, value (degC) attached to each point
        pc = r3.PointCloud()
        for x, y, z, v in zip(X[sel], Y[sel], Zc[sel] + DZ_CLOUD, vals):
            pc.Add(r3.Point3d(x, y, z), float(v))
        model.Objects.AddPointCloud(pc, attrs(lc, f"Nuage thermique {lab} {unit} - 10/07/2024"))
        # draped squares: one square per cell, corners on the terrain, colour = layer colour
        h = res / 2
        off = np.array([[-h, -h], [h, -h], [h, h], [-h, h]])
        P = np.stack([X[sel], Y[sel]], -1)[:, None, :] + off[None]          # (n,4,2)
        Zq = terr(np.c_[P[..., 1].ravel(), P[..., 0].ravel()]).reshape(-1, 4)
        Zq = np.where(np.isfinite(Zq), Zq, Zc[sel][:, None]) + DZ_SURF
        V = np.c_[P.reshape(-1, 2), Zq.ravel()]
        F = np.arange(4 * n).reshape(n, 4)
        add_colored_mesh(model, V, F, None, attrs(ls, f"Surface thermique {lab} {unit} - 10/07/2024"))
    rep["class_colors"] = {v[0]: list(v[1][:3]) for v in styles.values()}

    def sample(xy):
        c = np.floor((xy[:, 0] - tr.c) / res).astype(int)
        r = np.floor((tr.f - xy[:, 1]) / res).astype(int)
        ok = (c >= 0) & (c < nx) & (r >= 0) & (r < ny)
        out = np.full(len(xy), np.nan); out[ok] = T[r[ok], c[ok]]
        return out

    # roofs + per-category samples (face centroid value, horizontal area weight)
    samples = {c: [] for c in CATEGORIES}
    roofs = {k: [] for k in styles}
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
            ok = np.isfinite(val)
            Fr, kr = F[up][ok], np.digitize(val[ok], edges[1:-1])
            for k in np.unique(kr):
                if k in roofs:
                    roofs[k].append(V[Fr[kr == k]].reshape(-1, 3) + [0, 0, DZ_ROOF])
    nroof = 0
    for k, parts in roofs.items():
        if parts:
            Vr = np.vstack(parts); nroof += len(Vr) // 3
            lab, rgb, mat, lr = styles[k]
            add_colored_mesh(model, Vr, np.arange(len(Vr)).reshape(-1, 3), None,
                             attrs(lr, f"Toitures thermiques {lab} {unit} - 10/07/2024"))
    np.savez(WORK / "category_samples.npz",
             **{c: (np.vstack(s) if s else np.zeros((0, 2))) for c, s in samples.items()})
    rep["category_faces_sampled"] = {c: int(sum(len(x) for x in s)) for c, s in samples.items()}
    rep["category_faces_with_value"] = {c: int(sum(np.isfinite(x[:, 0]).sum() for x in s))
                                        for c, s in samples.items()}

    import geopandas as gpd
    gpkg = WORK / "Oran_Thermal_2024.gpkg"

    def drape(ring, dz):
        ring = np.asarray(ring)[:, :2]
        z = terr(np.c_[ring[:, 1], ring[:, 0]])
        z = np.where(np.isfinite(z), z, 0) + dz
        return r3.Polyline([r3.Point3d(float(x), float(y), float(zz)) for (x, y), zz in zip(ring, z)])

    def densify(ring, step=25.0):
        ring = np.asarray(ring)[:, :2]; pts = []
        for a, b in zip(ring[:-1], ring[1:]):
            k = max(1, int(np.ceil(np.linalg.norm(b - a) / step)))
            pts.append(a + (b - a) * np.arange(k)[:, None] / k)
        return np.vstack(pts + [ring[-1:]])

    # hot / cool zones
    l_hot = add_layer(model, "ZONES_CHAUDES", (180, 0, 0, 255), root)
    l_cool = add_layer(model, "ZONES_FRAICHES", (0, 90, 160, 255), root)
    zones = gpd.read_file(gpkg, layer="zones_chaudes_fraiches")
    for _, z in zones.iterrows():
        lay = l_hot if z["kind"] == "chaude" else l_cool
        ext = "max" if z["kind"] == "chaude" else "min"
        txt = (f"{z['name']} : moy {z['mean']:.1f} {unit}, {ext} {z['extreme']:.1f}, {z['area_km2']:.2f} km2")
        for poly in getattr(z.geometry, "geoms", [z.geometry]):
            model.Objects.AddPolyline(drape(densify(poly.exterior.coords), DZ_CLOUD + 2), attrs(lay, txt))
        zc = terr([[z["y"], z["x"]]])[0]
        model.Objects.AddTextDot(f"{z['name'].replace('Zone ', '')} {z['mean']:.1f}",
                                 r3.Point3d(z["x"], z["y"], (0 if np.isnan(zc) else zc) + 40), attrs(lay, txt))
    rep["zones"] = int(len(zones))
    # 1 km grid with values
    l_grid = add_layer(model, "GRILLE_1KM", (90, 90, 90, 255), root)
    l_gl = add_layer(model, "GRILLE_1KM_VALEURS", (0, 0, 0, 255), root)
    grid = gpd.read_file(gpkg, layer="grille_1km")
    for _, c in grid.iterrows():
        txt = (f"{c['id']} : moy {c['mean']:.1f} {unit} (min {c['min']:.1f}, max {c['max']:.1f}, "
               f"mediane {c['median']:.1f}, couverture {100 * c['cover']:.0f} %)")
        model.Objects.AddPolyline(drape(densify(c.geometry.exterior.coords), DZ_CLOUD), attrs(l_grid, txt))
        cx_, cy_ = c["x0"] + c["size"] / 2, c["y0"] + c["size"] / 2
        zc = terr([[cy_, cx_]])[0]
        if np.isfinite(zc):
            model.Objects.AddTextDot(f"{c['mean']:.1f}", r3.Point3d(cx_, cy_, zc + 20), attrs(l_gl, txt))
    rep["grid_cells_1km"] = int(len(grid))
    # study-area outline
    sa = gpd.read_file(gpkg, layer="study_area").geometry.iloc[0]
    for poly in getattr(sa, "geoms", [sa]):
        ring = np.array(poly.exterior.coords)
        z = terr(np.c_[ring[:, 1], ring[:, 0]]); z = np.where(np.isfinite(z), z, 0) + DZ_CLOUD
        model.Objects.AddPolyline(r3.Polyline([r3.Point3d(x, y, zz) for (x, y), zz in zip(ring[:, :2], z)]),
                                  attrs(l_lim, "Limite zone etudiee - carte 10/07/2024"))
    # legend beside the model (east side): one swatch per class, same colour / material as its layers
    x0 = float(np.nanmax(X[valid])) + 600; y0 = float(np.nanmin(Y[valid])); Wd = 300.0
    Hs = 4000.0 / max(1, len(styles))
    for i, k in enumerate(sorted(styles)):
        lab, rgb, mat, _ = styles[k]
        y = y0 + i * Hs
        Vl = np.array([[x0, y, 0], [x0 + Wd, y, 0], [x0 + Wd, y + Hs, 0], [x0, y + Hs, 0]], float)
        add_colored_mesh(model, Vl, np.array([[0, 1, 2, 3]]), None,
                         attrs(l_leg, f"Legende {lab} {unit}", color=rgb, material=mat))
        model.Objects.AddTextDot(f"{lab} {unit}", r3.Point3d(x0 + Wd + 350, y + Hs / 2, 0), attrs(l_leg, lab))
    model.Objects.AddTextDot(f"{cfg.get('quantity', '')} - 10/07/2024",
                             r3.Point3d(x0, y0 + len(styles) * Hs + 400, 0), attrs(l_leg, "titre"))

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
                "roof_triangles": int(nroof), "materials": len(chk.Materials),
                "offsets_m": {"surface": DZ_SURF, "cloud": DZ_CLOUD, "roofs": DZ_ROOF}})
    (WORK / "rhino_thermal_report.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
