"""Shared helpers: read a Rhino .3dm with rhino3dm and turn its geometry into
numpy triangle meshes / point lists, without Rhino installed.

Supported geometry
  Mesh                -> triangles as stored (quads split)
  Extrusion           -> exact prism rebuilt from its profile(s) and path
  Brep                -> cached render mesh of each face (saved by Rhino when
                         the model was shaded); fallback = convex hull (flagged)
  Curves              -> polyline sampled every `step` metres (roads, lines)
  InstanceReference   -> block geometry expanded with its transform
"""
import numpy as np
import rhino3dm as r3

# Rhino UnitSystem -> metres
UNIT_TO_M = {
    "None": 1.0, "Millimeters": 1e-3, "Centimeters": 1e-2, "Meters": 1.0,
    "Kilometers": 1e3, "Inches": 0.0254, "Feet": 0.3048, "Miles": 1609.344,
    "Decimeters": 0.1, "Microns": 1e-6,
}


def unit_scale(model):
    name = str(model.Settings.ModelUnitSystem).split(".")[-1]
    return name, UNIT_TO_M.get(name, 1.0)


def xform_to_np(x):
    return np.array([[getattr(x, f"M{i}{j}") for j in range(4)] for i in range(4)])


def apply_xform(pts, m):
    if m is None:
        return pts
    h = np.c_[pts, np.ones(len(pts))] @ m.T
    return h[:, :3] / h[:, 3:4]


def mesh_to_np(mesh):
    v = np.array([[p.X, p.Y, p.Z] for p in mesh.Vertices], float)
    tris = []
    for i in range(mesh.Faces.Count):
        a, b, c, d = mesh.Faces[i]
        tris.append((a, b, c))
        if c != d:
            tris.append((a, c, d))
    return v, np.array(tris, int).reshape(-1, 3)


def triangulate_polygon(pts2d):
    """Ear clipping for a simple polygon (building footprints). Returns index triples."""
    n = len(pts2d)
    idx = list(range(n))
    area = 0.5 * sum(pts2d[i][0] * pts2d[(i + 1) % n][1] - pts2d[(i + 1) % n][0] * pts2d[i][1]
                     for i in range(n))
    if area < 0:
        idx.reverse()
    out, guard = [], 0
    while len(idx) > 3 and guard < 10 * n:
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = pts2d[i0], pts2d[i1], pts2d[i2]
            cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if cross <= 1e-12:
                continue
            inside = False
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                p = pts2d[j]
                d1 = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
                d2 = (c[0] - b[0]) * (p[1] - b[1]) - (c[1] - b[1]) * (p[0] - b[0])
                d3 = (a[0] - c[0]) * (p[1] - c[1]) - (a[1] - c[1]) * (p[0] - c[0])
                if d1 >= 0 and d2 >= 0 and d3 >= 0:
                    inside = True
                    break
            if not inside:
                out.append((i0, i1, i2))
                idx.pop(k)
                break
    if len(idx) == 3:
        out.append(tuple(idx))
    return out


def curve_points(crv, step=5.0):
    """Polyline vertices kept exactly; other curves sampled every ~step units."""
    pl = crv.TryGetPolyline() if crv.IsPolyline() else None
    if pl is not None and pl.Count >= 2:
        return np.array([[pl[i].X, pl[i].Y, pl[i].Z] for i in range(pl.Count)], float)
    t0, t1 = crv.Domain.T0, crv.Domain.T1
    coarse = np.array([[p.X, p.Y, p.Z] for p in (crv.PointAt(t) for t in np.linspace(t0, t1, 65))])
    length = np.linalg.norm(np.diff(coarse, axis=0), axis=1).sum()
    n = max(2, int(np.ceil(length / step)) + 1)
    return np.array([[p.X, p.Y, p.Z] for p in (crv.PointAt(t) for t in np.linspace(t0, t1, n))])


def extrusion_to_np(ex):
    """Closed prism from an Extrusion's outer profile (holes ignored) and path."""
    bottom = curve_points(ex.Profile3d(0, 0.0), 1.0)
    if np.allclose(bottom[0], bottom[-1]):
        bottom = bottom[:-1]
    d = np.array([ex.PathEnd.X - ex.PathStart.X, ex.PathEnd.Y - ex.PathStart.Y,
                  ex.PathEnd.Z - ex.PathStart.Z])
    top = bottom + d
    n = len(bottom)
    v = np.vstack([bottom, top])
    # 2D projection on the profile plane for cap triangulation
    ax = np.argsort(np.abs(d))[:2]
    caps = triangulate_polygon([tuple(p[ax]) for p in bottom])
    f = [(a, c, b) for a, b, c in caps] + [(a + n, b + n, c + n) for a, b, c in caps]
    for i in range(n):
        j = (i + 1) % n
        f += [(i, j, j + n), (i, j + n, i + n)]
    return v, np.array(f, int)


def brep_to_np(brep):
    vs, fs, off = [], [], 0
    for i in range(len(brep.Faces)):
        m = brep.Faces[i].GetMesh(r3.MeshType.Any)
        if m is None:
            return None
        v, f = mesh_to_np(m)
        vs.append(v)
        fs.append(f + off)
        off += len(v)
    if not vs:
        return None
    return np.vstack(vs), np.vstack(fs)


def hull_fallback(geom):
    from scipy.spatial import ConvexHull
    try:
        pts = [[v.Location.X, v.Location.Y, v.Location.Z] for v in geom.Vertices]
    except Exception:
        pts = []
    if len(pts) < 4:
        bb = geom.GetBoundingBox()
        pts = [[x, y, z] for x in (bb.Min.X, bb.Max.X) for y in (bb.Min.Y, bb.Max.Y)
               for z in (bb.Min.Z, bb.Max.Z)]
    pts = np.array(pts, float)
    h = ConvexHull(pts)
    return pts, h.simplices


def iter_objects(model):
    """Yield (geometry, attributes, 4x4 transform or None), blocks expanded."""
    by_id = {str(o.Attributes.Id): o for o in model.Objects}
    idefs = {str(d.Id): d for d in model.InstanceDefinitions}

    def expand(obj, xf, depth=0):
        g = obj.Geometry
        if isinstance(g, r3.InstanceReference) and depth < 8:
            m = xform_to_np(g.Xform)
            m = m if xf is None else xf @ m
            d = idefs.get(str(g.ParentIdefId))
            if d is None:
                return
            for oid in d.GetObjectIds():
                child = by_id.get(str(oid))
                if child is not None:
                    yield from expand(child, m, depth + 1)
        else:
            yield obj, xf

    for o in model.Objects:
        if o.Attributes.IsInstanceDefinitionObject:
            continue
        for obj, xf in expand(o, None):
            yield obj.Geometry, o.Attributes, xf


def geometry_to_mesh(g):
    """Return (V, F, method) or None for non-solid geometry."""
    if isinstance(g, r3.Mesh):
        v, f = mesh_to_np(g)
        return v, f, "mesh"
    if isinstance(g, r3.Extrusion):
        return (*extrusion_to_np(g), "extrusion")
    if isinstance(g, r3.Brep):
        res = brep_to_np(g)
        if res is not None:
            return (*res, "brep_render_mesh")
        return (*hull_fallback(g), "brep_convex_hull")
    return None
