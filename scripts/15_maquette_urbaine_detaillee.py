"""Detailed urban maquette of Oran (Rhino 8): road hierarchy, sidewalks, curbs,
islands, crossings, markings, urban furniture, materials, clean layers.

Input : results/Oran_maquette_blanche_complete_Rhino8.3dm   (white maquette, script 10)
        results/cache/bati_14.pkl                            (script 14: georef, terrain, buildings)
        data/overture/oran_segments.parquet                  (script 13b: road axes, OSM crossings)
Output: results/Oran_maquette_urbaine_detaillee_Rhino8.3dm   (+ results/textures/*.png)
        results/maquette_urbaine_report.json

Rules (nothing of the existing urban morphology is moved: XY of every road,
building, parcel and corridor is kept; only the relief link is the corrected
one of script 14):
 * every draped layer is moved vertically by dT = T_new - T_old, evaluated on
   the SAME terrain triangulation -> it stays exactly draped (+0.5 / +1.0 m).
 * ROAD HIERARCHY: OSM highway layers -> ROAD_PRIMARY (motorway, trunk,
   primary + links), ROAD_SECONDARY (secondary, tertiary + links), ROAD_LOCAL
   (residential, unclassified, living street, road), ROAD_MINOR (service,
   track, construction...), PEDESTRIAN_ZONE (footway, pedestrian, path, steps).
   Same geometry, own layer, colour and material.
 * SIDEWALKS only where the geometry proves a pedestrian strip: between the
   carriageway edge and a building frontage located <= 30 m away; nominal
   width 4.0 / 3.0 / 2.0 m (primary / secondary / local), limited by the
   building fronts, other carriageways and pedestrian ways; strips narrower
   than 1.2 m are dropped (morphological opening).  Not on motorways, trunk
   links, service roads or in open country.
 * CURB: 0.20 m strip of the sidewalk along the carriageway, top 0.17 m above
   the road; sidewalk top 0.15 m above the road.
 * ISLANDS: holes of the carriageway network without buildings, 15-6000 m2:
   roundabout centres (circularity > 0.7), medians (mean width < 6 m),
   traffic islands; raised, with curbs; green when wider than 2 m.
 * CROSSINGS: OSM-mapped crossings + crossings on each arm of the junctions
   (road axes graph, degree >= 3) involving a primary/secondary road, only
   when sidewalks exist on BOTH ends of the crossing line.  Zebra: 0.50 m
   stripes / 0.50 m gaps, 3.0 m long, 0.5 m from each curb.
 * MARKINGS: dashed axis (3 m dash / 10 m gap, 0.15 m) on primary/secondary
   carriageways >= 6.5 m wide, on the measured centre of the carriageway
   (midpoint of the perpendicular chord), stopped before junctions.
 * FURNITURE (block instances, light): street lamps every 25 m on the
   primary/secondary sidewalks, alignment trees every 8 m on sidewalks
   >= 3 m wide, park trees (12 m spacing), benches, bollards and a sign at
   each crossing, bus shelters on the public-transport platforms.
"""
import json
import math
import pickle
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import rhino3dm as r3
import shapely
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).parent))
import urban_lib as u  # noqa: E402
from oran_georef import ORIGIN_LAT, ORIGIN_LON, _LOC  # noqa: E402

ROOT = u.ROOT
BATI = u.CACHE / "bati_14.pkl"
OV_S = ROOT / "data/overture/oran_segments.parquet"
OUT = ROOT / "results/Oran_maquette_urbaine_detaillee_Rhino8.3dm"
TEX = ROOT / "results/textures"
REPORT = ROOT / "results/maquette_urbaine_report.json"
SOCLE_M = 60.0
H_CONTOUR, H_MASTER = 10.0, 50.0

ROAD_TOP = 1.00          # existing road slabs: T + 0.5 .. T + 1.0
SLAB_BOT = 0.50
SIDEWALK_TOP = ROAD_TOP + 0.15
CURB_TOP = ROAD_TOP + 0.17
CURB_W = 0.20
MARK_TOP = ROAD_TOP + 0.02
TILE = 2000.0

CLASSES = {
    "ROAD_PRIMARY": ["HIGHWAY_MOTORWAY", "HIGHWAY_MOTORWAY_LINK", "HIGHWAY_TRUNK", "HIGHWAY_TRUNK_LINK",
                     "HIGHWAY_PRIMARY", "HIGHWAY_PRIMARY_LINK"],
    "ROAD_SECONDARY": ["HIGHWAY_SECONDARY", "HIGHWAY_SECONDARY_LINK", "HIGHWAY_TERTIARY", "HIGHWAY_TERTIARY_LINK"],
    "ROAD_LOCAL": ["HIGHWAY_RESIDENTIAL", "HIGHWAY_RESIDENTIAL_1000", "HIGHWAY_UNCLASSIFIED",
                   "HIGHWAY_LIVING_STREET", "HIGHWAY_ROAD"],
    "ROAD_MINOR": ["HIGHWAY_SERVICE", "HIGHWAY_SERVICE_1000", "HIGHWAY_SERVICES", "HIGHWAY_TRACK",
                   "HIGHWAY_CONSTRUCTION", "HIGHWAY_RACEWAY", "HIGHWAY_REST_AREA", "HIGHWAY_BRIDLEWAY",
                   "HIGHWAY_CORRIDOR"],
    "PEDESTRIAN_ZONE": ["HIGHWAY_FOOTWAY", "HIGHWAY_PEDESTRIAN", "HIGHWAY_PATH", "HIGHWAY_STEPS"],
}
NO_SIDEWALK = {"HIGHWAY_MOTORWAY", "HIGHWAY_MOTORWAY_LINK", "HIGHWAY_TRUNK_LINK"}
SIDEWALK_W = {"ROAD_PRIMARY": 4.0, "ROAD_SECONDARY": 3.0, "ROAD_LOCAL": 2.0}
GREEN = ["LEISURE_PA", "LEISURE_GA", "LANDUSE_G0", "LANDUSE_GR", "LANDUSE_V0", "LANDUSE_R0", "LANDUSE_ME",
         "LANDUSE_OR", "LANDUSE_VI", "LANDUSE_FO", "NATURAL_WO", "NATURAL_SC", "NATURAL_GR", "NATURAL_TR",
         "LANDUSE_CE", "LANDUSE_F0", "LANDUSE_PL"]
PARKS = ["LEISURE_PA", "LEISURE_GA", "LANDUSE_G0", "LANDUSE_GR", "LANDUSE_V0", "LANDUSE_R0"]
WATER = ["BAY", "NATURAL_WA", "WATERWAY_C", "WATERWAY_0", "WATERWAY_D"]
RIGID = ["BAY", "NATURAL_WA", "POWER_SUBS", "POWER_PLAN"] + ["PLANE"] + [f"PLANE_{i}" for i in range(1, 11)]
RELATIONS = ("ROUTE_", "RESTRICTION_")
CONTEXT_GROUPS = [
    ("EAU", ("BAY", "NATURAL_WA", "WATERWAY")),
    ("FERROVIAIRE_TRAM", ("RAILWAY", "PUBLIC_TRA")),
    ("AEROPORT", ("AEROWAY", "PLANE")),
    ("EQUIPEMENTS", ("AMENITY", "TOURISM", "HISTORIC", "SHOP", "TYPE_DESTI")),
    ("OCCUPATION_DU_SOL", ("LANDUSE", "LEISURE", "NATURAL", "AREA_YES")),
    ("OUVRAGES_RESEAUX", ("MAN_MADE", "POWER", "BARRIER", "WAY_")),
    ("LIEUX_NOMMES", ("NAME_",)),
]

# palette: black / dark grey / mid grey / light grey / white, discreet greens
COL = {
    "ROAD_PRIMARY": (38, 38, 40), "ROAD_SECONDARY": (70, 71, 74), "ROAD_LOCAL": (110, 111, 114),
    "ROAD_MINOR": (156, 157, 159), "PEDESTRIAN_ZONE": (198, 192, 180),
    "SIDEWALK": (196, 195, 190), "CURB": (128, 128, 128), "CROSSWALK": (244, 244, 241),
    "ROAD_MARKING": (244, 244, 241), "ROAD_ISLAND": (176, 176, 172), "GREEN_SPACE": (140, 163, 118),
    "URBAN_FURNITURE": (62, 64, 67), "BUILDINGS": (246, 245, 241), "TERRAIN_ET_SOCLE": (228, 225, 217),
    "CONTEXTE": (236, 234, 228), "EAU": (170, 190, 202), "FERROVIAIRE_TRAM": (96, 96, 98),
    "OSM_RELATIONS": (200, 200, 200), "TREE_CROWN": (104, 134, 88), "TREE_TRUNK": (102, 92, 80),
}
rep = {}


def log(*a):
    print(*a, flush=True)


# ===================================================================== geometry helpers
def top_footprint(V, F, T, min_dz=0.9):
    """2D outline of the top faces of a draped slab layer (z >= T + min_dz)."""
    TT = u.tris(F)
    if not len(TT):
        return shapely.Polygon()
    N = np.cross(V[TT[:, 1]] - V[TT[:, 0]], V[TT[:, 2]] - V[TT[:, 0]])
    nz = N[:, 2] / (np.linalg.norm(N, axis=1) + 1e-12)
    dz = V[:, 2] - T(V[:, 0], V[:, 1])
    top = ((nz > 0.3) if min_dz > -1e8 else (np.abs(nz) > 0.3)) & (dz[TT].min(1) > min_dz)
    outs, holes = [], []
    for r in u.boundary_rings(TT[top], V):
        P = V[r, :2]
        (outs if u.ring_area(P) > 0 else holes).append(P)
    if not outs:
        return shapely.Polygon()
    g = shapely.union_all([shapely.make_valid(shapely.Polygon(o)) for o in outs])
    if holes:
        g = shapely.difference(g, shapely.union_all([shapely.make_valid(shapely.Polygon(h)) for h in holes]))
    return polys_only(g)


def orient_layer(V, F):
    """Clean normals: closed parts -> outward (positive volume), open sheets ->
    facing up. Returns F and the number of flipped faces."""
    T = u.tris(F)
    if not len(T):
        return F, 0
    n, lab = u.mesh_parts(V, T)
    tl = lab[T[:, 0]]
    E = np.sort(np.r_[T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]], 1)
    _, inv, cnt = np.unique(E, axis=0, return_inverse=True, return_counts=True)
    open_part = np.zeros(n, bool)
    open_part[np.r_[tl, tl, tl][cnt[inv.ravel()] == 1]] = True
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    N = np.cross(b - a, c - a)
    vol = np.bincount(tl, np.einsum("ij,ij->i", a, np.cross(b, c)), minlength=n)
    up = np.bincount(tl, N[:, 2], minlength=n)
    flip_part = np.where(open_part, up < 0, vol < 0)
    flip = flip_part[tl]
    T = np.where(flip[:, None], T[:, ::-1], T)
    return np.c_[T, T[:, 2]], int(flip.sum())


def polys_only(g):
    parts = [q for q in shapely.get_parts(shapely.make_valid(g)) if q.geom_type == "Polygon" and q.area > 0.01]
    return shapely.MultiPolygon(parts) if parts else shapely.Polygon()


def tiles_of(bounds, size=TILE):
    x0, y0, x1, y1 = bounds
    return [(x, y, x + size, y + size) for x in np.arange(np.floor(x0 / size) * size, x1, size)
            for y in np.arange(np.floor(y0 / size) * size, y1, size)]


def chop(g, cell=250.0):
    """Split a big polygon into grid pieces (fast local queries)."""
    if g.is_empty:
        return np.array([], dtype=object)
    out = []
    for p in shapely.get_parts(g):
        if p.geom_type != "Polygon" or p.is_empty:
            continue
        x0, y0, x1, y1 = p.bounds
        if max(x1 - x0, y1 - y0) <= cell:
            out.append(p)
            continue
        for b in tiles_of(p.bounds, cell):
            try:
                c = shapely.clip_by_rect(p, *b)
            except shapely.errors.GEOSException:
                c = shapely.intersection(shapely.make_valid(p), shapely.box(*b))
            if not c.is_empty:
                out += [q for q in shapely.get_parts(c) if q.geom_type == "Polygon" and q.area > 0.01]
    return np.array(out, dtype=object)


class Local:
    """Fast local geometry queries on a chopped polygon."""

    def __init__(self, g, cell=250.0):
        self.parts = chop(g, cell)
        self.tree = shapely.STRtree(self.parts)

    def near(self, geom):
        idx = self.tree.query(geom)
        return shapely.union_all(self.parts[idx]) if len(idx) else shapely.Polygon()

    def contains_xy(self, x, y):
        pts = shapely.points(x, y)
        qi, pi = self.tree.query(pts, predicate="within")
        out = np.zeros(len(pts), bool)
        out[qi] = True
        return out


def refine(V, T, max_edge, terrain=None, tol=0.03, min_edge=0.5):
    """Conforming refinement: every edge longer than max_edge, or whose midpoint
    is more than `tol` off the terrain (linear interpolation of its ends), is
    bisected (1->2, 1->3 or 1->4 split), repeated; shared edges get the same midpoint."""
    for _ in range(16):
        E = np.sort(np.stack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]], 1), axis=2)      # (n,3,2)
        L = np.linalg.norm(V[E[:, :, 0]] - V[E[:, :, 1]], axis=2)
        lng = L > max_edge
        if terrain is not None:
            ue0, iv0 = np.unique(E.reshape(-1, 2), axis=0, return_inverse=True)
            A, Bv = V[ue0[:, 0]], V[ue0[:, 1]]
            Mm = 0.5 * (A + Bv)
            dev = np.abs(terrain(Mm[:, 0], Mm[:, 1]) - 0.5 * (terrain(A[:, 0], A[:, 1]) + terrain(Bv[:, 0], Bv[:, 1])))
            Le = np.linalg.norm(A - Bv, axis=1)
            lng |= ((dev > tol) & (Le > min_edge))[iv0.ravel()].reshape(lng.shape)
        if not lng.any():
            break
        ue, inv = np.unique(E[lng], axis=0, return_inverse=True)
        mid = len(V) + np.arange(len(ue))
        V = np.r_[V, 0.5 * (V[ue[:, 0]] + V[ue[:, 1]])]
        M = -np.ones(lng.shape, np.int64)
        M[lng] = mid[inv.ravel()]
        k = lng.sum(1)
        out = [T[k == 0]]
        # one long edge: rotate so that it is edge 0 (a,b), split -> (a,m,c), (m,b,c)
        for r in range(3):
            s1 = (k == 1) & lng[:, r]
            if s1.any():
                t = np.roll(T[s1], -r, 1)
                m = M[s1, r]
                out += [np.c_[t[:, 0], m, t[:, 2]], np.c_[m, t[:, 1], t[:, 2]]]
        # two long edges: rotate so that the short one is edge 2 (c,a)
        for r in range(3):
            s2 = (k == 2) & ~lng[:, (r + 2) % 3]
            if s2.any():
                t = np.roll(T[s2], -r, 1)
                m1, m2 = M[s2, r], M[s2, (r + 1) % 3]
                out += [np.c_[m1, t[:, 1], m2], np.c_[t[:, 0], m1, m2], np.c_[t[:, 0], m2, t[:, 2]]]
        s3 = k == 3
        if s3.any():
            t = T[s3]
            m0, m1, m2 = M[s3, 0], M[s3, 1], M[s3, 2]
            out += [np.c_[t[:, 0], m0, m2], np.c_[m0, t[:, 1], m1], np.c_[m2, m1, t[:, 2]], np.c_[m0, m1, m2]]
        T = np.vstack(out)
    return V, T


def slab(polys, terrain, z_bot, z_top, max_edge=16.0, seg=8.0):
    """Closed slabs draped on the terrain: top z = T + z_top, bottom z = T + z_bot.
    Constrained Delaunay on the boundary densified to `seg` m, conforming
    refinement of every edge longer than `max_edge` -> the slab follows the
    25 m terrain triangulation closely. Walls on the boundary edges."""
    flat = []
    for g in polys:
        g = shapely.set_precision(g, 0.001)
        if not g.is_valid:
            g = shapely.make_valid(g)
        flat += [q for q in shapely.get_parts(g) if q.geom_type == "Polygon" and q.area > 0.05]
    if not flat:
        return np.zeros((0, 3)), np.zeros((0, 4), np.int64)
    # pinched rings (touching themselves at a vertex) -> offset inwards by 2 mm (manifold slabs)
    out_ = []
    for q in flat:
        c = shapely.get_coordinates(q)
        if len(c) - len(shapely.get_rings(q)) > len(np.unique(np.round(c, 4), axis=0)):
            out_ += [r for r in shapely.get_parts(shapely.buffer(q, -0.002, join_style="mitre"))
                     if r.geom_type == "Polygon" and r.area > 0.05]
        else:
            out_.append(q)
    flat = out_
    if not flat:
        return np.zeros((0, 3)), np.zeros((0, 4), np.int64)
    P = shapely.segmentize(np.array(flat, dtype=object), seg)
    tg = shapely.constrained_delaunay_triangles(P)
    tparts, pidx = shapely.get_parts(tg, return_index=True)
    tri = shapely.get_coordinates(tparts).reshape(-1, 4, 2)[:, :3]
    if not len(tri):
        return np.zeros((0, 3)), np.zeros((0, 4), np.int64)
    cr = (tri[:, 1, 0] - tri[:, 0, 0]) * (tri[:, 2, 1] - tri[:, 0, 1]) - \
         (tri[:, 1, 1] - tri[:, 0, 1]) * (tri[:, 2, 0] - tri[:, 0, 0])
    keep = np.abs(cr) > 1e-9
    tri = np.where((cr < 0)[:, None, None], tri[:, ::-1], tri)[keep]
    pidx = pidx[keep]
    # weld inside each slab only: neighbouring slabs stay separate closed solids
    key = np.c_[np.round(tri.reshape(-1, 2) * 1e6).astype(np.int64), np.repeat(pidx, 3)]
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    V2 = tri.reshape(-1, 2)[first]
    T = inv.reshape(-1, 3)
    V2, T = refine(V2, T, max_edge, terrain)
    nv = len(V2)
    tz = terrain(V2[:, 0], V2[:, 1])
    V = np.r_[np.c_[V2, tz + z_top], np.c_[V2, tz + z_bot]]
    # directed boundary edges (used by one triangle) -> outward walls
    E = np.r_[T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]
    _, ii, cnt = np.unique(np.sort(E, 1), axis=0, return_inverse=True, return_counts=True)
    Bd = E[cnt[ii.ravel()] == 1]
    F = np.r_[np.c_[T, T[:, 2]], np.c_[T[:, ::-1] + nv, T[:, 0] + nv],
              np.c_[Bd[:, 0] + nv, Bd[:, 1] + nv, Bd[:, 1], Bd[:, 0]]]
    return V, F


def flat_quads(quads, terrain, z_bot, z_top):
    """Small rectangles (n,4,2) -> closed thin boxes draped at their corners."""
    if not len(quads):
        return np.zeros((0, 3)), np.zeros((0, 4), np.int64)
    q = np.asarray(quads, float)
    a = (q[:, 1, 0] - q[:, 0, 0]) * (q[:, 2, 1] - q[:, 0, 1]) - (q[:, 1, 1] - q[:, 0, 1]) * (q[:, 2, 0] - q[:, 0, 0])
    q[a < 0] = q[a < 0][:, ::-1]
    n = len(q)
    tz = terrain(q[:, :, 0].ravel(), q[:, :, 1].ravel()).reshape(n, 4)
    top = np.c_[q.reshape(-1, 2), (tz + z_top).ravel()]
    bot = np.c_[q.reshape(-1, 2), (tz + z_bot).ravel()]
    V = np.r_[top, bot]
    b = np.arange(n)[:, None] * 4
    o = 4 * n
    F = [b + np.array([0, 1, 2, 3]), b + o + np.array([3, 2, 1, 0])]
    for i in range(4):
        j = (i + 1) % 4
        F.append(np.c_[b + o + i, b + o + j, b + j, b + i])
    return V, np.vstack(F)


# ===================================================================== sidewalks / curbs / islands
def tile_job(args):
    (b, carr_cls, carr_all, ped, bld, water) = args
    x0, y0, x1, y1 = b
    m = 60.0
    box = shapely.box(x0, y0, x1, y1)
    clip = lambda g: polys_only(shapely.clip_by_rect(g, x0 - m, y0 - m, x1 + m, y1 + m)) if not g.is_empty else g
    CA = clip(carr_all)
    if CA.is_empty:
        return b, [], [], []
    B = clip(bld)
    P = clip(ped)
    Wt = clip(water)
    urban = shapely.buffer(B, 30.0) if not B.is_empty else shapely.Polygon()
    blocked = shapely.union_all([CA, B, P, Wt])
    sw = {}
    for cls, w in SIDEWALK_W.items():
        C = clip(carr_cls[cls])
        if C.is_empty or urban.is_empty:
            continue
        band = shapely.buffer(C, w, join_style="mitre", mitre_limit=2.0)
        band = shapely.difference(band, blocked)
        band = shapely.intersection(band, urban)
        band = shapely.buffer(shapely.buffer(band, -0.6, join_style="mitre"), 0.6, join_style="mitre")  # opening 1.2 m
        band = shapely.intersection(band, shapely.buffer(C, w + 0.01))
        band = polys_only(shapely.intersection(band, box))
        if not band.is_empty:
            sw[cls] = band
    # one sidewalk geometry, class priority primary > secondary > local
    done = shapely.Polygon()
    sidewalks, curbs = [], []
    for cls in SIDEWALK_W:
        if cls not in sw:
            continue
        g = polys_only(shapely.difference(sw[cls], done))
        done = shapely.union(done, g)
        if g.is_empty:
            continue
        curb = polys_only(shapely.intersection(g, shapely.buffer(CA, CURB_W, join_style="mitre")))
        walk = polys_only(shapely.difference(g, curb))
        sidewalks.append((cls, walk))
        curbs.append((cls, curb))
    # islands = holes of the carriageway network, no building inside
    isl = []
    for poly in shapely.get_parts(CA):
        for h in poly.interiors:
            hp = shapely.Polygon(h)
            if not (15 <= hp.area <= 6000):
                continue
            c = hp.representative_point()
            if not box.contains(c):
                continue
            if not B.is_empty and shapely.intersects(hp, B):
                continue
            per = hp.length
            circ = 4 * math.pi * hp.area / per ** 2
            mean_w = 2 * hp.area / per
            kind = "ROND_POINT" if (circ > 0.7 and hp.area > 30) else ("TERRE_PLEIN_CENTRAL" if mean_w < 6.0
                                                                         else "ILOT")
            isl.append((kind, hp))
    return b, sidewalks, curbs, isl


# ===================================================================== road axes, junctions
def segments_model(geo):
    t = pq.read_table(OV_S).to_pylist()
    g = shapely.from_wkb([r["geometry"] for r in t])
    g = shapely.transform(g, lambda xy: np.c_[geo.to_model(*_LOC.transform(xy[:, 0], xy[:, 1]))])
    cls = np.array([r["class"] or "" for r in t])
    sub = np.array([r["subclass"] or "" for r in t])
    typ = np.array([r["subtype"] or "" for r in t])
    return g, cls, sub, typ


MAJOR = {"motorway", "trunk", "primary", "secondary", "tertiary"}
VEH = MAJOR | {"residential", "unclassified", "living_street", "service", "unknown"}


def chord(loc, p, t, half=25.0):
    """Perpendicular chord of the carriageway through p (direction t):
    returns (A, B, mid, width) or None."""
    n = np.array([-t[1], t[0]])
    line = shapely.LineString([p - half * n, p + half * n])
    cg = loc.near(line)
    if cg.is_empty:
        return None
    seg = shapely.intersection(line, cg)
    best, bd = None, 3.0
    P = shapely.Point(p)
    for s in shapely.get_parts(seg):
        if s.geom_type != "LineString" or s.length < 1.0:
            continue
        d = s.distance(P)
        if d < bd:
            best, bd = s, d
    if best is None:
        return None
    c = np.asarray(best.coords)
    A, B = c[0], c[-1]
    return A, B, 0.5 * (A + B), float(np.hypot(*(B - A)))


def zebra(A, B, t, length=3.0, stripe=0.5, gap=0.5, margin=0.5):
    """Stripes parallel to the road direction t, along the chord A->B."""
    v = B - A
    L = np.hypot(*v)
    if L < 2 * margin + stripe:
        return []
    e = v / L
    out = []
    s = margin
    while s + stripe <= L - margin + 1e-6:
        p0 = A + e * s
        p1 = A + e * (s + stripe)
        h = t * length / 2
        out.append([p0 - h, p1 - h, p1 + h, p0 + h])
        s += stripe + gap
    return out


def crossings_and_markings(sg, scls, ssub, styp, carr_loc, sw_loc, veh, road, rng):
    # junction graph (vehicle axes): endpoints rounded to 0.5 m
    vi = np.nonzero(veh)[0]
    ends = []
    for k in vi:
        c = shapely.get_coordinates(sg[k])
        ends.append((tuple(np.round(c[0] * 2).astype(int)), k, 0))
        ends.append((tuple(np.round(c[-1] * 2).astype(int)), k, -1))
    node = {}
    for key, k, e in ends:
        node.setdefault(key, []).append((k, e))
    stripes, cross_lines, n_cross = [], [], {"carrefours": 0, "osm": 0}
    junction_zones = []
    for key, arms in node.items():
        if len(arms) < 3:
            continue
        p = np.array(key) / 2.0
        cl = [scls[k] for k, _ in arms]
        if not any(c in ("primary", "secondary", "tertiary", "trunk") for c in cl):
            continue
        if "motorway" in cl:
            continue
        dirs, widths = [], []
        for k, e in arms:
            g = sg[k]
            L = g.length
            q = shapely.line_interpolate_point(g, min(12.0, L) if e == 0 else max(L - 12.0, 0)).coords[0]
            t = np.array(q) - p
            nt = np.hypot(*t)
            if nt < 1e-6:
                dirs.append(None)
                widths.append(0)
                continue
            t = t / nt
            ch = chord(carr_loc, p + t * min(8.0, L * 0.5), t)
            dirs.append(t)
            widths.append(ch[3] if ch else 0.0)
        R = 0.5 * max(widths) + 1.5
        junction_zones.append(shapely.Point(p).buffer(R + 4.0))
        for (k, e), t, w in zip(arms, dirs, widths):
            if t is None or w < 4.0 or w > 30:
                continue
            if scls[k] not in VEH or scls[k] == "service":
                continue
            s = R + 1.5
            if s + 3 > sg[k].length:
                continue
            ch = chord(carr_loc, p + t * s, t)
            if ch is None or sw_loc is None:
                continue
            A, Bp, mid, wd = ch
            okA = sw_loc.tree.query(shapely.Point(A).buffer(1.5)).size > 0
            okB = sw_loc.tree.query(shapely.Point(Bp).buffer(1.5)).size > 0
            if not (okA and okB):
                continue
            stripes += zebra(A, Bp, t)
            cross_lines.append((A, Bp, t))
            n_cross["carrefours"] += 1
    # OSM-mapped crossings (footway=crossing)
    for g in sg[(scls == "footway") & (ssub == "crosswalk")]:
        c = shapely.get_coordinates(g)
        if len(c) < 2:
            continue
        A, Bp = c[0], c[-1]
        v = Bp - A
        L = np.hypot(*v)
        if L < 3:
            continue
        t = np.array([-v[1], v[0]]) / L   # road direction = perpendicular to the crossing
        stripes += zebra(A, Bp, t)
        cross_lines.append((A, Bp, t))
        n_cross["osm"] += 1
    jz = Local(shapely.union_all(junction_zones)) if junction_zones else None
    # dashed axis on primary / secondary carriageways
    dashes = []
    mk = road & np.isin(scls, ["trunk", "primary", "secondary", "tertiary"])
    for g in sg[mk]:
        L = g.length
        s = 2.0
        while s + 3.0 < L - 2.0:
            a = np.array(shapely.line_interpolate_point(g, s).coords[0])
            b = np.array(shapely.line_interpolate_point(g, s + 3.0).coords[0])
            t = b - a
            nt = np.hypot(*t)
            s += 13.0
            if nt < 1e-6:
                continue
            t /= nt
            ch = chord(carr_loc, 0.5 * (a + b), t)
            if ch is None or ch[3] < 6.5 or ch[3] > 30:
                continue
            c = ch[2]
            if jz is not None and jz.contains_xy(np.array([c[0]]), np.array([c[1]]))[0]:
                continue
            n = np.array([-t[1], t[0]]) * 0.075
            h = t * 1.5
            dashes.append([c - h - n, c + h - n, c + h + n, c - h + n])
    return stripes, cross_lines, n_cross, dashes, jz


# ===================================================================== furniture blocks
def box_mesh(x0, y0, z0, x1, y1, z1):
    m = r3.Mesh()
    for x, y, z in [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                    (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]:
        m.Vertices.Add(x, y, z)
    for f in [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]:
        m.Faces.AddFace(*f)
    m.Normals.ComputeNormals()
    return m


def cyl_mesh(r, z0, z1, n=10, x=0.0, y=0.0, r_top=None):
    r_top = r if r_top is None else r_top
    m = r3.Mesh()
    for k in range(n):
        a = 2 * math.pi * k / n
        m.Vertices.Add(x + r * math.cos(a), y + r * math.sin(a), z0)
    for k in range(n):
        a = 2 * math.pi * k / n
        m.Vertices.Add(x + r_top * math.cos(a), y + r_top * math.sin(a), z1)
    m.Vertices.Add(x, y, z0)
    m.Vertices.Add(x, y, z1)
    for k in range(n):
        j = (k + 1) % n
        m.Faces.AddFace(k, j, n + j, n + k)
        m.Faces.AddFace(2 * n, j, k)
        m.Faces.AddFace(2 * n + 1, n + k, n + j)
    m.Normals.ComputeNormals()
    return m


def blob_mesh(rx, ry, rz, cz, n=8, m_=5):
    """Low-poly ellipsoid (tree crown)."""
    m = r3.Mesh()
    m.Vertices.Add(0, 0, cz - rz)
    for i in range(1, m_):
        ph = math.pi * i / m_
        for k in range(n):
            a = 2 * math.pi * k / n
            m.Vertices.Add(rx * math.sin(ph) * math.cos(a), ry * math.sin(ph) * math.sin(a), cz - rz * math.cos(ph))
    m.Vertices.Add(0, 0, cz + rz)
    top = 1 + n * (m_ - 1)
    for k in range(n):
        j = (k + 1) % n
        m.Faces.AddFace(0, 1 + j, 1 + k)
        m.Faces.AddFace(top, 1 + n * (m_ - 2) + k, 1 + n * (m_ - 2) + j)
    for i in range(m_ - 2):
        for k in range(n):
            j = (k + 1) % n
            a, b = 1 + i * n, 1 + (i + 1) * n
            m.Faces.AddFace(a + k, a + j, b + j, b + k)
    m.Normals.ComputeNormals()
    return m


def make_blocks(model, mats, lay_furn):
    def att(color, mat):
        a = r3.ObjectAttributes()
        a.LayerIndex = lay_furn
        a.ColorSource = r3.ObjectColorSource.ColorFromObject
        a.ObjectColor = color + (255,)
        a.MaterialSource = r3.ObjectMaterialSource.MaterialFromObject
        a.MaterialIndex = mats[mat]
        return a
    steel, wood, glass = COL["URBAN_FURNITURE"], (122, 104, 84), (190, 205, 210)
    defs = {
        "ARBRE_ALIGNEMENT": [(cyl_mesh(0.14, 0, 2.6, 8, r_top=0.11), att(COL["TREE_TRUNK"], "TRONC")),
                             (blob_mesh(2.2, 2.2, 2.0, 4.6), att(COL["TREE_CROWN"], "FEUILLAGE"))],
        "ARBRE_PARC": [(cyl_mesh(0.18, 0, 3.0, 8, r_top=0.13), att(COL["TREE_TRUNK"], "TRONC")),
                       (blob_mesh(3.0, 3.0, 2.6, 5.4), att(COL["TREE_CROWN"], "FEUILLAGE"))],
        "LAMPADAIRE": [(cyl_mesh(0.09, 0, 8.0, 8, r_top=0.06), att(steel, "METAL")),
                       (box_mesh(-0.04, -0.04, 7.85, 1.6, 0.04, 7.95), att(steel, "METAL")),
                       (box_mesh(1.2, -0.18, 7.70, 1.9, 0.18, 7.85), att((230, 230, 226), "LUMINAIRE"))],
        "BANC": [(box_mesh(-0.9, -0.22, 0.42, 0.9, 0.22, 0.48), att(wood, "BOIS")),
                 (box_mesh(-0.9, 0.18, 0.48, 0.9, 0.24, 0.85), att(wood, "BOIS")),
                 (box_mesh(-0.8, -0.2, 0, -0.7, 0.2, 0.42), att(steel, "METAL")),
                 (box_mesh(0.7, -0.2, 0, 0.8, 0.2, 0.42), att(steel, "METAL"))],
        "POTELET": [(cyl_mesh(0.06, 0, 0.9, 8), att(steel, "METAL"))],
        "PANNEAU_PASSAGE_PIETON": [(cyl_mesh(0.035, 0, 2.6, 6), att((150, 150, 150), "METAL")),
                                   (box_mesh(-0.3, -0.02, 2.0, 0.3, 0.02, 2.6), att((236, 236, 232), "PANNEAU"))],
        "ABRIBUS": [(box_mesh(-2.1, -0.85, 2.45, 2.1, 0.85, 2.55), att(steel, "METAL")),
                    (box_mesh(-2.0, 0.7, 0.1, 2.0, 0.74, 2.45), att(glass, "VERRE")),
                    (box_mesh(-2.05, -0.8, 0, -1.95, 0.8, 2.45), att(steel, "METAL")),
                    (box_mesh(1.95, -0.8, 0, 2.05, 0.8, 2.45), att(steel, "METAL")),
                    (box_mesh(-1.5, 0.3, 0.42, 1.5, 0.65, 0.48), att(steel, "METAL"))],
    }
    ids = {}
    for name, items in defs.items():
        geoms = tuple(g for g, _ in items)
        atts = tuple(a for _, a in items)
        i = model.InstanceDefinitions.Add(name, f"Mobilier urbain : {name.lower()}", "", "",
                                          r3.Point3d(0, 0, 0), geoms, atts)
        ids[name] = model.InstanceDefinitions.FindIndex(i).Id
    return ids


def place(model, ids, name, pts, z, ang, layer):
    if not len(pts):
        return 0
    if isinstance(layer, tuple):
        layer = layer[0].add(*layer[1:])
    a = r3.ObjectAttributes()
    a.LayerIndex = layer
    n = 0
    for (x, y), zz, th in zip(pts, z, ang):
        xf = r3.Transform.Multiply(r3.Transform.Translation(float(x), float(y), float(zz)),
                                   r3.Transform.Rotation(float(th), r3.Vector3d(0, 0, 1), r3.Point3d(0, 0, 0)))
        model.Objects.AddInstanceObject(r3.InstanceReference(ids[name], xf), a)
        n += 1
        for k, (x0, y0, x1, y1) in PREVIEW.items():
            if x0 - 20 < x < x1 + 20 and y0 - 20 < y < y1 + 20:
                PREV_PTS[k].append((name, float(x), float(y), float(zz), float(th)))
    return n


def poisson(poly, spacing, rng, max_n=200000):
    """Blue-noise points inside poly (grid jitter + min distance)."""
    x0, y0, x1, y1 = poly.bounds
    xs = np.arange(x0, x1, spacing)
    ys = np.arange(y0, y1, spacing)
    if not len(xs) or not len(ys):
        return np.zeros((0, 2))
    X, Y = np.meshgrid(xs, ys)
    P = np.c_[X.ravel(), Y.ravel()] + rng.uniform(0.15, 0.85, (X.size, 2)) * spacing
    P = P[:max_n]
    shapely.prepare(poly)
    return P[shapely.contains_xy(poly, P[:, 0], P[:, 1])]


# ===================================================================== textures / materials
def make_textures():
    TEX.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)

    def noise(n=512, amp=1.0, blur=1.2):
        a = rng.normal(0, 1, (n, n))
        im = Image.fromarray(np.uint8(np.clip(128 + 40 * a, 0, 255))).filter(ImageFilter.GaussianBlur(blur))
        return (np.asarray(im, float) - 128) / 40 * amp

    def save(name, base, amp, joints=None, blur=1.2, speck=0.0):
        n = 512
        z = noise(n, amp, blur) + noise(n, amp * 0.6, 6)
        img = np.clip(np.array(base, float)[None, None, :] + z[:, :, None] * 6, 0, 255)
        if speck:
            s = rng.random((n, n)) < speck
            img[s] = np.clip(img[s] + rng.normal(0, 25, (s.sum(), 1)), 0, 255)
        if joints:
            px = n // joints[0], n // joints[1]
            img[::px[1], :, :] *= 0.86
            img[:, ::px[0], :] *= 0.86
        Image.fromarray(np.uint8(img)).save(TEX / name)
    save("asphalte_anthracite.png", COL["ROAD_PRIMARY"], 1.0, speck=0.02)
    save("asphalte_gris_fonce.png", COL["ROAD_SECONDARY"], 1.0, speck=0.02)
    save("asphalte_gris_moyen.png", COL["ROAD_LOCAL"], 1.0, speck=0.02)
    save("enrobe_gris_clair.png", COL["ROAD_MINOR"], 1.0, speck=0.01)
    save("dalles_beton_trottoir.png", COL["SIDEWALK"], 0.7, joints=(4, 8))       # 1 m x 0.5 m slabs (4 m tile)
    save("pierre_pietonne.png", COL["PEDESTRIAN_ZONE"], 0.8, joints=(8, 8))       # 0.5 m stone pavers
    save("granit_bordure.png", COL["CURB"], 0.9, blur=0.8)
    save("herbe.png", COL["GREEN_SPACE"], 1.6, blur=0.7)


def material(model, name, color, tex=None):
    m = r3.Material()
    m.Name = name
    m.DiffuseColor = color + (255,)
    m.AmbientColor = (0, 0, 0, 255)
    m.SpecularColor = (0, 0, 0, 255)
    m.Shine = 0.0
    m.Reflectivity = 0.0
    m.Transparency = 0.0
    if tex:
        t = r3.Texture()
        t.FileName = f"textures/{tex}"
        m.SetBitmapTexture(t)
    return model.Materials.Add(m)


# ===================================================================== Rhino writing
PREVIEW = {"centre_ville": (-1150.0, 50.0, -250.0, 750.0), "carrefour": (-560.0, 230.0, -380.0, 410.0)}
PREV_TRI = {k: [] for k in PREVIEW}
PREV_PTS = {k: [] for k in PREVIEW}
LAYER_COL = {}
PREV_T = []      # terrain function for the previews (set in main)

PLANE_MAP = r3.TextureMapping.CreatePlaneMapping(r3.Plane.WorldXY(), r3.Interval(0, 4), r3.Interval(0, 4),
                                                 r3.Interval(0, 1))


def add_mesh(model, V, F, layer, name="", textured=False):
    if not len(F):
        return None
    if isinstance(layer, tuple):            # (Layers, path, colour, material): created on first use
        layer = layer[0].add(*layer[1:])
    if F.shape[1] == 3:
        F = np.c_[F, F[:, 2]]
    V, F = cull_degenerate(V, F)
    if not len(F):
        return None
    TT = u.tris(F)
    cen = V[TT].mean(1)
    for k, (x0, y0, x1, y1) in PREVIEW.items():
        mg = 200.0
        sel = (cen[:, 0] > x0 - mg) & (cen[:, 0] < x1 + mg) & (cen[:, 1] > y0 - mg) & (cen[:, 1] < y1 + mg)
        if sel.any() and PREV_T:
            tq = V[TT[sel]]
            dz = tq[:, :, 2].mean(1) - PREV_T[0](tq[:, :, 0].mean(1), tq[:, :, 1].mean(1))
            PREV_TRI[k].append((tq, LAYER_COL.get(layer, (200, 200, 200)), dz))
    m = r3.Mesh()
    m.Vertices.UseDoublePrecisionVertices = True
    for x, y, z in V.tolist():
        m.Vertices.Add(x, y, z)
    for a, b, c, d in F.tolist():
        if c == d:
            m.Faces.AddFace(a, b, c)
        else:
            m.Faces.AddFace(a, b, c, d)
    m.Normals.ComputeNormals()
    if textured:
        m.SetTextureCoordinates(PLANE_MAP, r3.Transform(1.0), False)
    at = r3.ObjectAttributes()
    at.LayerIndex = layer
    at.Name = name
    return model.Objects.AddMesh(m, at)


def cull_degenerate(V, F):
    """Rhino 'Weld' + 'CullDegenerateMeshFaces' inside each connected piece:
    vertices at the same position in the same piece are merged (edge collapse,
    stays closed), faces with a repeated corner, zero area or duplicated are
    removed, unused vertices dropped. Separate pieces are never merged."""
    T = u.tris(F)
    n, lab = u.mesh_parts(V, T)
    # Rhino also stores single-precision vertices (~1 mm resolution at 10-15 km
    # from the origin): vertices identical in float32 are welded too
    key = np.c_[V.astype(np.float32).view(np.int32).reshape(-1, 3), lab]
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    V = V[first]
    T = inv.ravel()[T]
    ok = (T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 2] != T[:, 0])
    T = T[ok]
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    T = T[np.linalg.norm(np.cross(b - a, c - a), axis=1) > 1e-12]
    _, u_ = np.unique(np.sort(T, 1), axis=0, return_index=True)
    T = T[np.sort(u_)]
    Tq = np.c_[T, T[:, 2]]
    Tq, _ = orient_layer(V, Tq)                  # closed pieces outward, sheets facing up
    T = Tq[:, :3]
    used = np.unique(T)
    rm = -np.ones(len(V), np.int64)
    rm[used] = np.arange(len(used))
    return V[used], np.c_[rm[T], rm[T][:, 2]]


class Layers:
    def __init__(self, model):
        self.m = model
        self.idx = {}

    def add(self, path, color, mat=None, visible=True):
        if path in self.idx:
            return self.idx[path]
        parts = path.split("::")
        parent = self.add("::".join(parts[:-1]), color, mat, True) if len(parts) > 1 else None
        L = r3.Layer()
        L.Name = parts[-1]
        L.Color = tuple(color) + (255,)
        L.Visible = visible
        if mat is not None:
            L.RenderMaterialIndex = mat
        if parent is not None:
            L.ParentLayerId = self.m.Layers.FindIndex(parent).Id
        i = self.m.Layers.Add(L)
        self.idx[path] = i
        LAYER_COL[i] = tuple(color) if visible else None
        return i


def contours(V, T, level):
    """Exact level set of a triangle mesh -> polylines (script 10)."""
    z = V[:, 2] - level
    z = np.where(z == 0, 1e-9, z)
    s = np.sign(z[T])
    cross = (s.min(1) < 0) & (s.max(1) > 0)
    Tc = T[cross]
    if not len(Tc):
        return []
    E = np.stack([Tc[:, [0, 1]], Tc[:, [1, 2]], Tc[:, [2, 0]]], 1)
    sc = np.sign(z[E])
    ec = sc[:, :, 0] != sc[:, :, 1]
    Es = np.sort(E, axis=2)
    seg = Es[ec].reshape(-1, 2, 2)
    keys = seg[:, :, 0].astype(np.int64) * len(V) + seg[:, :, 1]
    uk, inv = np.unique(keys.ravel(), return_inverse=True)
    inv = inv.reshape(-1, 2)
    a, bb = uk // len(V), uk % len(V)
    t = z[a] / (z[a] - z[bb])
    P = V[a] + t[:, None] * (V[bb] - V[a])
    P[:, 2] = level
    nb = [[] for _ in range(len(uk))]
    for i, (p, q) in enumerate(inv.tolist()):
        nb[p].append(q)
        nb[q].append(p)
    seen = np.zeros(len(uk), bool)
    lines = []
    for st in sorted(range(len(uk)), key=lambda k: len(nb[k])):
        if seen[st]:
            continue
        ch = [st]
        seen[st] = True
        cur = st
        while True:
            nx = [k for k in nb[cur] if not seen[k]]
            if not nx:
                break
            cur = nx[0]
            seen[cur] = True
            ch.append(cur)
        if len(nb[st]) == 2 and st in nb[ch[-1]] and len(ch) > 2:
            ch.append(st)
        if len(ch) >= 2:
            lines.append(P[ch])
    return lines


def terrain_block(tx, ty, tz):
    X, Y = np.meshgrid(tx, ty)
    ny, nx = tz.shape
    top = np.c_[X.ravel(), Y.ravel(), tz.ravel()]
    i = (np.arange(ny - 1)[:, None] * nx + np.arange(nx - 1)[None, :]).ravel()
    tf = np.vstack([np.c_[i, i + 1, i + nx], np.c_[i + 1, i + nx + 1, i + nx]])
    idx = np.arange(nx * ny).reshape(ny, nx)
    ring = np.r_[idx[0, :], idx[1:, -1], idx[-1, -2::-1], idx[-2:0:-1, 0]]
    n = len(top)
    bot = top[ring].copy()
    bot[:, 2] = -SOCLE_M
    br = n + np.arange(len(ring))
    c = n + len(ring)
    centre = np.r_[bot[:, :2].mean(0), -SOCLE_M]
    a_, b_ = ring, np.roll(ring, -1)
    ba, bb = br, np.roll(br, -1)
    F = np.vstack([tf, np.c_[a_, ba, b_], np.c_[b_, ba, bb], np.c_[np.full(len(ring), c), bb, ba]])
    return np.vstack([top, bot, centre]), F, tf, top


# ===================================================================== main
def main():
    t0 = time.time()
    data = u.load_maquette()
    M = data["meshes"]
    B = pickle.load(open(BATI, "rb"))
    sys.path.insert(0, str(Path(__file__).parent))
    geo_mod = __import__("14_bati_georef_hauteurs")
    geo = geo_mod.Georef(*B["georef"])
    T_old = u.Terrain(B["terrain_x"], B["terrain_y"], B["Z_old"])
    T_new = u.Terrain(B["terrain_x"], B["terrain_y"], B["Z_new"])
    dT = u.Terrain(B["terrain_x"], B["terrain_y"], B["Z_new"] - B["Z_old"])
    PREV_T.append(T_new)
    log("loaded", round(time.time() - t0))

    # ---------------------------------------------------------------- 1. re-drape every layer
    layers = {}
    outside = 0
    flipped = 0
    for ln, (V, F) in M.items():
        if ln == "TERRAIN_ET_SOCLE" or ln.startswith(geo_mod.BUILD_PREFIX):
            continue
        V = V.copy()
        if ln in RIGID:
            TT = u.tris(F)
            n, lab = u.mesh_parts(V, TT)
            d = dT(V[:, 0], V[:, 1])
            med = np.array([np.median(d[lab == k]) for k in range(n)])
            V[:, 2] += med[lab]
        else:
            V[:, 2] += dT(V[:, 0], V[:, 1])
        F, nf = orient_layer(V, F)
        flipped += nf
        outside += int(((V[:, 0] < T_new.x[0]) | (V[:, 0] > T_new.x[-1]) |
                        (V[:, 1] < T_new.y[0]) | (V[:, 1] > T_new.y[-1])).sum())
        layers[ln] = (V, F)
    rep["redrape"] = {"layers": len(layers), "vertices_outside_terrain_grid": outside,
                      "faces_reoriented_normals": flipped}
    log("redraped", round(time.time() - t0))

    # ---------------------------------------------------------------- 2. 2D footprints
    fcache = u.CACHE / "stage15_footprints.pkl"
    if fcache.exists():
        fp, carr_cls, carr_all, ped, bld, water = pickle.load(open(fcache, "rb"))
    else:
        fp = {}
        for cls, lns in CLASSES.items():
            for ln in lns:
                if ln in layers:
                    fp[ln] = top_footprint(*layers[ln], T_new)
        carr_cls = {c: polys_only(shapely.union_all([fp[l] for l in CLASSES[c] if l in fp and l not in NO_SIDEWALK]))
                    for c in ("ROAD_PRIMARY", "ROAD_SECONDARY", "ROAD_LOCAL")}
        carr_all = polys_only(shapely.union_all([fp[l] for c in ("ROAD_PRIMARY", "ROAD_SECONDARY", "ROAD_LOCAL",
                                                                 "ROAD_MINOR") for l in CLASSES[c] if l in fp]))
        ped = polys_only(shapely.union_all([fp[l] for l in CLASSES["PEDESTRIAN_ZONE"] if l in fp]))
        bld_polys = []
        for ln, (V, F) in B["buildings"].items():
            TT = u.tris(F)
            N = np.cross(V[TT[:, 1]] - V[TT[:, 0]], V[TT[:, 2]] - V[TT[:, 0]])
            top = N[:, 2] > 0.5 * np.linalg.norm(N, axis=1)
            tri2 = V[TT[top]][:, :, :2]
            bld_polys.append(shapely.polygons(np.concatenate([tri2, tri2[:, :1]], 1)))
        bld = shapely.union_all(np.concatenate(bld_polys), grid_size=0.001)
        water = polys_only(shapely.union_all([top_footprint(*layers[w], T_new, min_dz=-1e9) for w in WATER if w in layers]))
        pickle.dump((fp, carr_cls, carr_all, ped, bld, water), open(fcache, "wb"), protocol=4)
    rep["road_area_km2"] = {c: round(sum(fp[l].area for l in CLASSES[c] if l in fp) / 1e6, 3) for c in CLASSES}
    log("footprints", rep["road_area_km2"], round(time.time() - t0))

    # ---------------------------------------------------------------- 3. sidewalks / curbs / islands (tiles)
    scache = u.CACHE / "stage15_sidewalks.pkl"
    if scache.exists():
        res = pickle.load(open(scache, "rb"))
        jobs = None
    else:
        res = None
    tl = tiles_of(carr_all.bounds)
    jobs = [] if res is None else None
    for b in (tl if jobs is not None else []):
        bx = shapely.box(b[0] - 70, b[1] - 70, b[2] + 70, b[3] + 70)
        cut = lambda g: polys_only(shapely.intersection(g, bx)) if not g.is_empty else g
        CA = cut(carr_all)
        if CA.is_empty:
            continue
        jobs.append((b, {k: cut(v) for k, v in carr_cls.items()}, CA, cut(ped), cut(bld), cut(water)))
    if res is None:
        with Pool(4) as pool:
            res = pool.map(tile_job, jobs)
        pickle.dump(res, open(scache, "wb"), protocol=4)
    SW, CU, ISL = {}, {}, {}
    for b, sws, cus, isl in res:
        for cls, g in sws:
            SW.setdefault(cls, []).extend(shapely.get_parts(g))
        for cls, g in cus:
            CU.setdefault(cls, []).extend(shapely.get_parts(g))
        for kind, g in isl:
            ISL.setdefault(kind, []).append(g)
    sidewalk_all = polys_only(shapely.union_all(sum(SW.values(), []) + sum(CU.values(), [])))
    rep["sidewalks"] = {c: {"area_m2": round(sum(p.area for p in SW.get(c, []))), "pieces": len(SW.get(c, []))}
                        for c in SIDEWALK_W}
    rep["curbs_length_km"] = round(sum(p.area for v in CU.values() for p in v) / CURB_W / 1000, 1)
    rep["islands"] = {k: len(v) for k, v in ISL.items()}
    log("sidewalks", rep["sidewalks"], rep["islands"], round(time.time() - t0))

    # ---------------------------------------------------------------- 4. junctions, crossings, markings
    sg, scls, ssub, styp = segments_model(geo)
    road = (styp == "road")
    carr_loc = Local(carr_all)
    sw_loc = Local(sidewalk_all) if not sidewalk_all.is_empty else None
    # validation of the georeferencing: share of real axes lying on model carriageways
    veh = road & np.isin(scls, list(VEH))
    lens = shapely.length(sg[veh])
    samp = [shapely.line_interpolate_point(sg[veh], f, normalized=True) for f in (0.25, 0.5, 0.75)]
    on = np.mean([carr_loc.contains_xy(shapely.get_x(s), shapely.get_y(s)).mean() for s in samp])
    raw = shapely.transform(sg[veh], lambda xy: np.c_[geo.to_real(xy[:, 0], xy[:, 1])])
    samp0 = [shapely.line_interpolate_point(raw, f, normalized=True) for f in (0.25, 0.5, 0.75)]
    on0 = np.mean([carr_loc.contains_xy(shapely.get_x(s), shapely.get_y(s)).mean() for s in samp0])
    rng = np.random.default_rng(3)
    rep["georef_validation_road_axes_on_carriageway"] = {"without_correction": round(float(on0), 3),
                                                         "with_correction": round(float(on), 3),
                                                         "axes_km": round(float(lens.sum() / 1000), 1)}
    log("axes validation", rep["georef_validation_road_axes_on_carriageway"])
    stripes, cross_lines, n_cross, dashes, jz = crossings_and_markings(sg, scls, ssub, styp, carr_loc, sw_loc,
                                                                       veh, road, rng)
    rep["crossings"] = {**n_cross, "stripes": len(stripes)}
    # preview window on the busiest real junction near the centre (most crossings within 60 m)
    if cross_lines:
        cm = np.array([0.5 * (a + b) for a, b, _ in cross_lines])
        near_c = np.hypot(cm[:, 0] + 700, cm[:, 1] - 400) < 2000
        if near_c.any():
            from scipy.spatial import cKDTree
            kd = cKDTree(cm)
            cnt = np.array([len(kd.query_ball_point(q, 60.0)) for q in cm])
            cnt[~near_c] = -1
            q = cm[int(np.argmax(cnt))]
            PREVIEW["carrefour"] = (q[0] - 90, q[1] - 90, q[0] + 90, q[1] + 90)
            rep["preview_junction_xy"] = [round(float(q[0]), 1), round(float(q[1]), 1)]
    rep["axis_dashes"] = len(dashes)
    log("markings", len(dashes), round(time.time() - t0))

    # ---------------------------------------------------------------- 5. Rhino model
    make_textures()
    out = r3.File3dm()
    out.Settings.ModelUnitSystem = r3.UnitSystem.Meters
    out.Settings.ModelAbsoluteTolerance = 0.001
    lon0, lat0 = geo.lonlat(np.array([0.0]), np.array([0.0]))
    ea = out.Settings.EarthAnchorPoint
    ea.EarthBasepointLatitude = float(lat0[0])
    ea.EarthBasepointLongitude = float(lon0[0])
    ea.EarthBasepointElevation = 0.0
    out.Settings.EarthAnchorPoint = ea
    mats = {
        "ROAD_PRIMARY": material(out, "Asphalte anthracite", COL["ROAD_PRIMARY"], "asphalte_anthracite.png"),
        "ROAD_SECONDARY": material(out, "Asphalte gris fonce", COL["ROAD_SECONDARY"], "asphalte_gris_fonce.png"),
        "ROAD_LOCAL": material(out, "Asphalte gris moyen", COL["ROAD_LOCAL"], "asphalte_gris_moyen.png"),
        "ROAD_MINOR": material(out, "Enrobe gris clair", COL["ROAD_MINOR"], "enrobe_gris_clair.png"),
        "PEDESTRIAN_ZONE": material(out, "Pierre pietonne", COL["PEDESTRIAN_ZONE"], "pierre_pietonne.png"),
        "SIDEWALK": material(out, "Beton dalles trottoir", COL["SIDEWALK"], "dalles_beton_trottoir.png"),
        "CURB": material(out, "Granit bordure", COL["CURB"], "granit_bordure.png"),
        "CROSSWALK": material(out, "Peinture routiere blanche", COL["CROSSWALK"]),
        "ROAD_MARKING": material(out, "Marquage blanc", COL["ROAD_MARKING"]),
        "ROAD_ISLAND": material(out, "Beton ilot", COL["ROAD_ISLAND"], "dalles_beton_trottoir.png"),
        "GREEN_SPACE": material(out, "Vegetation", COL["GREEN_SPACE"], "herbe.png"),
        "BUILDINGS": material(out, "Bati neutre mat", COL["BUILDINGS"]),
        "TERRAIN_ET_SOCLE": material(out, "Terrain", COL["TERRAIN_ET_SOCLE"]),
        "CONTEXTE": material(out, "Contexte neutre", COL["CONTEXTE"]),
        "EAU": material(out, "Eau mate", COL["EAU"]),
        "FERROVIAIRE_TRAM": material(out, "Rail", COL["FERROVIAIRE_TRAM"]),
        "URBAN_FURNITURE": material(out, "Metal gris", COL["URBAN_FURNITURE"]),
        "TRONC": material(out, "Tronc", COL["TREE_TRUNK"]),
        "FEUILLAGE": material(out, "Feuillage", COL["TREE_CROWN"]),
        "METAL": material(out, "Metal mat", COL["URBAN_FURNITURE"]),
        "BOIS": material(out, "Bois", (122, 104, 84)),
        "VERRE": material(out, "Verre", (190, 205, 210)),
        "LUMINAIRE": material(out, "Luminaire", (230, 230, 226)),
        "PANNEAU": material(out, "Panneau", (236, 236, 232)),
    }
    LY = Layers(out)
    TX = True
    # roads (existing geometry, re-organised)
    for cls, lns in CLASSES.items():
        for ln in lns:
            if ln in layers:
                li = LY.add(f"{cls}::{ln}", COL[cls], mats[cls])
                add_mesh(out, *layers[ln], li, ln, textured=TX)
    # sidewalks, curbs, islands (new, per class, per 2 km tile)
    stats_dev = []
    for cls in SIDEWALK_W:
        for kind, D_, ztop, mat in [("SIDEWALK", SW, SIDEWALK_TOP, "SIDEWALK"), ("CURB", CU, CURB_TOP, "CURB")]:
            ps = D_.get(cls, [])
            if not ps:
                continue
            li = LY.add(f"{kind}::{kind}_{cls.split('_')[1]}", COL[mat], mats[mat])
            groups = {}
            for p in ps:
                if p.is_empty or p.area < 0.05:
                    continue
                c = p.representative_point()
                groups.setdefault((int(c.x // TILE), int(c.y // TILE)), []).append(p)
            for (i, j), g in groups.items():
                V, F = slab(g, T_new, SLAB_BOT, ztop, *((16.0, 8.0) if kind == "SIDEWALK" else (24.0, 16.0)))
                add_mesh(out, V, F, li, f"{kind} {cls} tuile {i},{j}", textured=TX)
                if len(F) and kind == "SIDEWALK":
                    TT = u.tris(F)
                    Nn = np.cross(V[TT[:, 1]] - V[TT[:, 0]], V[TT[:, 2]] - V[TT[:, 0]])
                    upf = Nn[:, 2] > 0.9 * np.linalg.norm(Nn, axis=1)
                    q = V[TT[upf]]
                    pts_ = np.r_[q.mean(1), 0.5 * (q[:, 0] + q[:, 1]), 0.5 * (q[:, 1] + q[:, 2])]
                    stats_dev.append(float(np.abs(pts_[:, 2] - T_new(pts_[:, 0], pts_[:, 1]) - ztop).max()))
    rep["sidewalk_drape_max_dev_m"] = round(float(max(stats_dev)) if stats_dev else 0.0, 3)
    for kind, ps in ISL.items():
        green = [p for p in ps if 2 * p.area / p.length >= 2.0 and kind != "ILOT"]
        paved = [p for p in ps if p not in green]
        ring = [polys_only(shapely.difference(p, shapely.buffer(p, -CURB_W, join_style="mitre"))) for p in ps]
        inner = [polys_only(shapely.buffer(p, -CURB_W, join_style="mitre")) for p in green]
        li = (LY, f"ROAD_ISLAND::{kind}", COL["ROAD_ISLAND"], mats["ROAD_ISLAND"])
        V, F = slab([q for g in inner for q in shapely.get_parts(g)], T_new, SLAB_BOT, SIDEWALK_TOP - 0.02)
        lg = (LY, "GREEN_SPACE::ILOTS_VEGETALISES", COL["GREEN_SPACE"], mats["GREEN_SPACE"])
        add_mesh(out, V, F, lg, f"{kind} vegetalise", textured=TX)
        V, F = slab(paved, T_new, SLAB_BOT, SIDEWALK_TOP)
        add_mesh(out, V, F, li, f"{kind} mineral", textured=TX)
        lc = (LY, "CURB::CURB_ILOTS", COL["CURB"], mats["CURB"])
        V, F = slab([q for g in ring for q in shapely.get_parts(g)], T_new, SLAB_BOT, CURB_TOP, 24.0, 16.0)
        add_mesh(out, V, F, lc, f"Bordure {kind}", textured=TX)
    # crossings & markings
    li = LY.add("CROSSWALK", COL["CROSSWALK"], mats["CROSSWALK"])
    V, F = flat_quads(stripes, T_new, ROAD_TOP - 0.005, MARK_TOP)
    add_mesh(out, V, F, li, "Passages pietons (bandes 0.50 m)")
    li = LY.add("ROAD_MARKING::AXE_DISCONTINU_T1", COL["ROAD_MARKING"], mats["ROAD_MARKING"])
    V, F = flat_quads(dashes, T_new, ROAD_TOP - 0.005, MARK_TOP)
    add_mesh(out, V, F, li, "Marquage axial 3 m / 10 m")
    log("roads written", round(time.time() - t0))

    # green spaces and context (existing geometry, re-organised)
    for ln in GREEN:
        if ln in layers:
            li = LY.add(f"GREEN_SPACE::{ln}", COL["GREEN_SPACE"], mats["GREEN_SPACE"])
            add_mesh(out, *layers[ln], li, ln, textured=TX)
    for ln, (V, F) in sorted(layers.items()):
        if any(ln in v for v in CLASSES.values()) or ln in GREEN:
            continue
        if ln.startswith(RELATIONS):
            li = LY.add(f"OSM_RELATIONS::{ln}", COL["OSM_RELATIONS"], mats["CONTEXTE"], visible=False)
        else:
            grp = next((g for g, pre in CONTEXT_GROUPS if ln.startswith(pre)), "AUTRES")
            col = COL.get(grp, COL["CONTEXTE"])
            mat = mats.get(grp, mats["CONTEXTE"])
            li = LY.add(f"CONTEXTE::{grp}::{ln}", col, mat)
        add_mesh(out, V, F, li, ln)
    # buildings (script 14)
    for ln, (V, F) in sorted(B["buildings"].items()):
        li = LY.add(f"BUILDINGS::{ln}", COL["BUILDINGS"], mats["BUILDINGS"])
        add_mesh(out, V, F, li, ln)
    li = LY.add("BUILDINGS_EMPRISES_REELLES_OSM_GOOGLE_MICROSOFT", (214, 206, 196), mats["BUILDINGS"], visible=False)
    add_mesh(out, *B["real_buildings"], li, "Emprises reelles (Overture) - variante")
    log("buildings written", round(time.time() - t0))

    # terrain + contours on the new relief
    Vt, Ft, tf, top = terrain_block(T_new.x, T_new.y, T_new.Z)
    li = LY.add("TERRAIN_ET_SOCLE", COL["TERRAIN_ET_SOCLE"], mats["TERRAIN_ET_SOCLE"])
    add_mesh(out, Vt, Ft, li, f"Terrain Copernicus GLO-30 recale + socle {SOCLE_M:.0f} m")
    l10 = LY.add("COURBES_NIVEAU_10m", (175, 175, 175))
    l50 = LY.add("COURBES_NIVEAU_50m", (120, 120, 120))
    nl = 0
    for k in range(1, int(T_new.Z.max() // H_CONTOUR) + 1):
        lev = k * H_CONTOUR
        for P in contours(top, tf, lev):
            at = r3.ObjectAttributes()
            at.LayerIndex = l50 if lev % H_MASTER == 0 else l10
            at.Name = f"Courbe {lev:.0f} m"
            out.Objects.AddPolyline(r3.Polyline([r3.Point3d(*map(float, q)) for q in P]), at)
            nl += 1
    rep["contour_polylines"] = nl

    # ---------------------------------------------------------------- 6. furniture
    furn = LY.add("URBAN_FURNITURE", COL["URBAN_FURNITURE"], mats["URBAN_FURNITURE"])
    ids = make_blocks(out, mats, furn)
    lf = {k: (LY, f"URBAN_FURNITURE::{k}", COL["TREE_CROWN"] if "ARBRE" in k else COL["URBAN_FURNITURE"],
                    mats["FEUILLAGE"] if "ARBRE" in k else mats["METAL"])
          for k in ("LAMPADAIRES", "ARBRES_ALIGNEMENT", "ARBRES_PARCS", "BANCS", "POTELETS", "PANNEAUX", "ABRIBUS")}
    fr = {}
    # lamps / alignment trees along the curb of primary & secondary sidewalks
    lamps, lamp_a, trees, tree_a = [], [], [], []
    for cls, step_l in (("ROAD_PRIMARY", 25.0), ("ROAD_SECONDARY", 25.0)):
        for p in SW.get(cls, []):
            edge = shapely.intersection(p.boundary, shapely.buffer(carr_loc.near(p), CURB_W + 0.05))
            inner_l = shapely.buffer(p, -0.45)
            inner_t = shapely.buffer(p, -1.2)
            for ln_ in shapely.get_parts(shapely.line_merge(edge) if not edge.is_empty else edge):
                if ln_.geom_type != "LineString" or ln_.length < 6:
                    continue
                for s in np.arange(3.0, ln_.length - 2.0, 8.0):
                    a = np.array(ln_.interpolate(s).coords[0])
                    b = np.array(ln_.interpolate(min(s + 0.5, ln_.length)).coords[0])
                    t = b - a
                    if np.hypot(*t) < 1e-6:
                        continue
                    t /= np.hypot(*t)
                    n = np.array([-t[1], t[0]])
                    for sign in (1, -1):
                        q = a + sign * n * 0.6
                        if inner_l.contains(shapely.Point(q)):
                            break
                    else:
                        continue
                    k = int(round((s - 3.0) / 8.0))
                    if k % 3 == 0:
                        lamps.append(q)
                        lamp_a.append(math.atan2(-sign * n[1], -sign * n[0]))
                    else:
                        qt = a + sign * n * 1.3
                        if inner_t.contains(shapely.Point(qt)):
                            trees.append(qt)
                            tree_a.append(rng.uniform(0, 2 * math.pi))
    lamps, trees = np.array(lamps).reshape(-1, 2), np.array(trees).reshape(-1, 2)
    fr["lampadaires"] = place(out, ids, "LAMPADAIRE", lamps, T_new(*lamps.T) + SIDEWALK_TOP if len(lamps) else [],
                              lamp_a, lf["LAMPADAIRES"])
    fr["arbres_alignement"] = place(out, ids, "ARBRE_ALIGNEMENT", trees,
                                    T_new(*trees.T) + SIDEWALK_TOP if len(trees) else [], tree_a,
                                    lf["ARBRES_ALIGNEMENT"])
    # parks: trees + benches
    pk = polys_only(shapely.union_all([top_footprint(*layers[l], T_new, min_dz=-1e9) for l in PARKS if l in layers]))
    if not pk.is_empty:
        near = shapely.buffer(pk, 5.0)
        obst = shapely.union_all([shapely.intersection(g, near) for g in (carr_all, ped, bld) if not g.is_empty])
        pk = polys_only(shapely.difference(pk, shapely.buffer(obst, 2.5)))
    pt = poisson(pk, 12.0, rng) if not pk.is_empty else np.zeros((0, 2))
    fr["arbres_parcs"] = place(out, ids, "ARBRE_PARC", pt, T_new(*pt.T) + 0.45 if len(pt) else [],
                               rng.uniform(0, 6.28, len(pt)), lf["ARBRES_PARCS"])
    pedb = polys_only(shapely.intersection(shapely.buffer(shapely.intersection(ped, shapely.buffer(pk, 5.0)), 1.6),
                                           shapely.buffer(pk, 0.5))) if not pk.is_empty else pk
    benches = []
    for pl in shapely.get_parts(pedb):
        for ln_ in shapely.get_parts(pl.exterior):
            for s in np.arange(5, ln_.length, 30.0):
                benches.append(np.array(ln_.interpolate(s).coords[0]))
    benches = np.array(benches).reshape(-1, 2)
    fr["bancs"] = place(out, ids, "BANC", benches, T_new(*benches.T) + 0.45 if len(benches) else [],
                        rng.uniform(0, 6.28, len(benches)), lf["BANCS"])
    # crossings: bollards (2 per end) and one sign
    bol, bol_z, sgn, sgn_a = [], [], [], []
    for A, Bp, t in cross_lines:
        e = (Bp - A) / max(np.hypot(*(Bp - A)), 1e-6)
        for P_, sgn_e in ((A, -1), (Bp, 1)):
            for off in (-2.2, 2.2):
                bol.append(P_ + sgn_e * e * 0.5 + t * off)
        sgn.append(Bp + e * 0.6 + t * 2.4)
        sgn_a.append(math.atan2(t[1], t[0]))
    bol = np.array(bol).reshape(-1, 2)
    sgn = np.array(sgn).reshape(-1, 2)
    if sw_loc is not None and len(bol):
        okb = sw_loc.contains_xy(bol[:, 0], bol[:, 1])
        bol = bol[okb]
        oks = sw_loc.contains_xy(sgn[:, 0], sgn[:, 1])
        sgn, sgn_a = sgn[oks], np.array(sgn_a)[oks]
    fr["potelets"] = place(out, ids, "POTELET", bol, T_new(*bol.T) + SIDEWALK_TOP if len(bol) else [],
                           np.zeros(len(bol)), lf["POTELETS"])
    fr["panneaux"] = place(out, ids, "PANNEAU_PASSAGE_PIETON", sgn, T_new(*sgn.T) + SIDEWALK_TOP if len(sgn) else [],
                           sgn_a, lf["PANNEAUX"])
    # bus shelters on the public transport platforms
    sh, sh_a = [], []
    if "PUBLIC_TRA" in layers:
        for p in shapely.get_parts(top_footprint(*layers["PUBLIC_TRA"], T_new, min_dz=-1e9)):
            r = shapely.minimum_rotated_rectangle(p)
            if r.geom_type != "Polygon" or p.area < 4.0:
                continue
            c = np.asarray(r.exterior.coords)[:4]
            e = c[1] - c[0] if np.hypot(*(c[1] - c[0])) > np.hypot(*(c[2] - c[1])) else c[2] - c[1]
            sh.append(np.asarray(p.representative_point().coords[0]))
            sh_a.append(math.atan2(e[1], e[0]))
    sh = np.array(sh).reshape(-1, 2)
    fr["abribus"] = place(out, ids, "ABRIBUS", sh, T_new(*sh.T) + 0.5 if len(sh) else [], sh_a, lf["ABRIBUS"])
    rep["furniture"] = fr
    log("furniture", fr, round(time.time() - t0))

    # ---------------------------------------------------------------- 7. named views
    views(out, T_new, cross_lines)
    pickle.dump({"win": PREVIEW, "tri": {k: [(t, c, d) for t, c, d in v if c is not None] for k, v in PREV_TRI.items()},
                 "pts": PREV_PTS}, open(u.CACHE / "preview_15.pkl", "wb"), protocol=4)
    out.Write(str(OUT), 8)
    rep["file_MB"] = round(OUT.stat().st_size / 1e6, 1)
    rep["layers"] = len(out.Layers)
    rep["runtime_s"] = round(time.time() - t0)
    REPORT.write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    log(json.dumps(rep, indent=1, ensure_ascii=False))


def views(out, T, cross_lines):
    def add(name, cam, target, persp=True, lens=35):
        vi = r3.ViewInfo()
        vi.Name = name
        vp = vi.Viewport
        d = np.asarray(target, float) - np.asarray(cam, float)
        if persp:
            vp.ChangeToPerspectiveProjection(float(np.linalg.norm(d)), True, lens)
        else:
            vp.ChangeToParallelProjection(True)
        vp.SetCameraLocation(r3.Point3d(*map(float, cam)))
        vp.SetCameraDirection(r3.Vector3d(*map(float, d)))
        vp.SetCameraUp(r3.Vector3d(0, 0, 1) if persp else r3.Vector3d(0, 1, 0))
        vi.Viewport = vp
        out.NamedViews.Add(vi)
    zc = float(T(np.array([0.0]), np.array([-1500.0]))[0])
    z1 = float(T(np.array([-700.0]), np.array([400.0]))[0])
    add("01 Vue generale (plan)", (0, -1500, 9000), (0, -1499, zc), persp=False)
    add("02 Vue aerienne", (-2500, -8000, 3500), (-500, 0, z1), lens=50)
    add("03 Perspective 3D centre-ville", (-1600, -900, 420), (-600, 450, z1), lens=35)
    if cross_lines:
        c = np.array([0.5 * (a + b) for a, b, _ in cross_lines])
        d = np.hypot(c[:, 0] + 470, c[:, 1] - 320)
        A, Bp, t = cross_lines[int(np.argmin(d))]
        p = 0.5 * (A + Bp)
        z = float(T(np.array([p[0]]), np.array([p[1]]))[0])
        add("04 Zoom rue", (p[0] - 120 * t[0] - 40, p[1] - 120 * t[1] - 40, z + 45), (p[0], p[1], z), lens=35)
        add("05 Zoom carrefour et trottoirs", (p[0] - 30, p[1] - 30, z + 22), (p[0], p[1], z + 1), lens=28)


if __name__ == "__main__":
    main()
