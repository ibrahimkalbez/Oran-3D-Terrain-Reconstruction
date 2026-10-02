"""Fly-through of the USTO quarter, facing the USTO-MB university (Bir El Djir),
built from the user's Rhino model: white maquette file (terrain + buildings +
draped streets), same look as the Sidi El Houari video.
Red: the university polygon of the file (layer AMENITY_UN, exact) and the USTO
quarter facing it (outline traced on the file's road network, approximate:
the model has no named USTO polygon); the university's buildings are bright red,
the quarter's buildings light red.
Writes /tmp/claude-0/vusto/*.bin + scene.json for scripts/video/render.mjs"""
import json
from pathlib import Path
import numpy as np
import rhino3dm as r3
from scipy.ndimage import gaussian_filter1d
from scipy.interpolate import LinearNDInterpolator
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union
from shapely import contains_xy

ROOT = Path(__file__).resolve().parents[1]
OUT = Path("/tmp/claude-0/vusto"); OUT.mkdir(parents=True, exist_ok=True)
X0, X1, Y0, Y1 = 2400, 9600, -2900, 3700
CX, CY = 5600.0, 400.0
SRC = ROOT / "results/Oran_maquette_blanche_complete_Rhino8.3dm"
ROADS = ("HIGHWAY_", "ROUTE_ROAD", "RAILWAY_", "ROUTE_TRAM", "LEISURE_PA")
# USTO quarter facing the campus (south-east side of the campus boulevard / tram),
# traced on the roads of the file: campus south road, roundabout (5650, 860),
# north-east boulevard, roundabout (7270, 650), southern bypass, roundabout (5450, -500).
USTO_QUARTER = Polygon([(4960, 380), (5300, 560), (5650, 840), (6250, 1130), (6620, 1190),
                        (7270, 650), (6900, -930), (6400, -780), (5480, -500), (5300, -260),
                        (4960, -230)])


def to3(P):
    return np.c_[P[:, 0] - CX, P[:, 2], -(P[:, 1] - CY)].astype(np.float32)


def mesh_np(g):
    V = np.array([[p.X, p.Y, p.Z] for p in g.Vertices])
    F = np.array([tuple(g.Faces[k]) for k in range(g.Faces.Count)]).reshape(-1, 4)
    q = F[:, 2] != F[:, 3]
    return V, np.r_[F[:, [0, 1, 2]], F[q][:, [0, 2, 3]]]


def inbox(V, T):
    c = V[T].mean(1)
    return (c[:, 0] > X0) & (c[:, 0] < X1) & (c[:, 1] > Y0) & (c[:, 1] < Y1)


def outline(poly, step=5.0):
    segs = []
    for gg in getattr(poly, "geoms", [poly]):
        k_ = np.linspace(0, 1, max(2, int(gg.exterior.length / step)))
        pts = np.array([gg.exterior.interpolate(t, normalized=True).coords[0] for t in k_])
        segs.append(np.repeat(pts, 2, 0)[1:-1])
    return np.vstack(segs)


m = r3.File3dm.Read(str(SRC))
groups = {"terrain": [], "bld": [], "road": []}
campus = None
for o in m.Objects:
    g = o.Geometry
    if not isinstance(g, r3.Mesh):
        continue
    name = m.Layers[o.Attributes.LayerIndex].Name
    if name == "TERRAIN_ET_SOCLE":
        key = "terrain"
    elif name == "AMENITY_UN":                      # university polygons of the file
        V, T = mesh_np(g)
        T = T[inbox(V, T)]
        U = unary_union([p for p in (Polygon(V[t, :2]) for t in T) if p.area > 1e-3]).buffer(1).buffer(-1)
        campus = max(getattr(U, "geoms", [U]), key=lambda p: p.area)   # USTO-MB, 89 ha
        continue
    elif name.startswith(("BUILDING", "HEIGHT_", "EXTRA_BUILD")):
        key = "bld"
    elif name.startswith(ROADS):
        key = "road"
    else:
        continue
    V, T = mesh_np(g)
    T = T[inbox(V, T)]
    if len(T):
        groups[key].append(V[T].reshape(-1, 3))
P = np.vstack(groups.pop("bld"))
c = P.reshape(-1, 3, 3).mean(1)
inu = np.repeat(contains_xy(campus.buffer(5), c[:, 0], c[:, 1]), 3)
inq = np.repeat(contains_xy(USTO_QUARTER, c[:, 0], c[:, 1]), 3) & ~inu
groups["bld"], groups["bldu"], groups["bldq"] = [P[~inu & ~inq]], [P[inu]], [P[inq]]
for k, L in groups.items():
    P = np.vstack(L); to3(P).tofile(OUT / f"{k}_pos.bin"); print(k, len(P) // 3, "tris")

# ground height from the top surface of the terrain mesh (socle bottom is at -60 m)
TP = np.vstack(groups["terrain"]); TP = TP[TP[:, 2] > -50]
TP = np.unique(TP.round(2), axis=0)
G = LinearNDInterpolator(TP[:, :2], TP[:, 2], fill_value=0.0)
gz = lambda x, y: float(G(x, y))

lines = {"Université USTO-MB": outline(campus), "Quartier USTO": outline(USTO_QUARTER)}
for k, L in lines.items():
    L3 = np.c_[L, G(L[:, 0], L[:, 1]) + 3.0]
    to3(L3).tofile(OUT / ("lines_campus_pos.bin" if k.startswith("Univ") else "lines_quarter_pos.bin"))
print("outlines", {k: len(v) // 2 for k, v in lines.items()},
      "campus", round(campus.area / 1e4, 1), "ha, quarter", round(USTO_QUARTER.area / 1e4, 1), "ha")

# ---------------------------------------------------------------- camera
U0 = np.array(campus.centroid.coords[0])           # university centre
Q0 = np.array(USTO_QUARTER.centroid.coords[0])     # quarter centre
C0 = (U0 + Q0) / 2
FPS = 24


def lerp(a, b, n, ease=True):
    t = np.linspace(0, 1, n); t = t * t * (3 - 2 * t) if ease else t
    return a[None] + (b - a)[None] * t[:, None]


def low_flight(wp, n, h_cam=80, h_tgt=25, lead=50):
    s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(wp, axis=0), axis=1))]
    t = np.linspace(0, s[-1], n + lead)
    path = gaussian_filter1d(np.c_[np.interp(t, s, wp[:, 0]), np.interp(t, s, wp[:, 1])], 8, axis=0)
    zc = G(path[:, 0], path[:, 1])
    return np.c_[path[:n], zc[:n] + h_cam], np.c_[path[lead:n + lead], zc[lead:n + lead] + h_tgt]


shots = []
# 1. approach from the north, over Bir El Djir (5 s)
n = 5 * FPS
cam = lerp(np.r_[C0[0] - 400, C0[1] + 3300, 750], np.r_[C0[0] - 200, C0[1] + 1800, 450], n)
tgt = np.repeat([np.r_[C0, 110]], n, 0)
shots.append((cam, tgt, "USTO — arrivée par Bir El Djir"))
# 2. orbit around the university and the quarter facing it (12 s)
n = 12 * FPS
ang = np.linspace(np.radians(100), np.radians(100 + 290), n)
R = 1500
cam = np.c_[C0[0] + R * np.cos(ang), C0[1] + R * np.sin(ang), np.full(n, 430)]
tgt = np.repeat([np.r_[C0, 110]], n, 0)
shots.append((cam, tgt, "Université USTO-MB et quartier USTO (contours rouges)"))
# 2b. close orbit around the university itself (9 s)
n = 9 * FPS
a0 = np.arctan2(*(cam[-1, :2] - U0)[::-1])
ang = np.linspace(a0, a0 + np.radians(200), n)
Rr = np.linspace(1500, 750, n)
cam = np.c_[U0[0] + Rr * np.cos(ang), U0[1] + Rr * np.sin(ang), np.linspace(430, 260, n)]
tgt = np.repeat([np.r_[U0, gz(*U0) + 15]], n, 0)
shots.append((cam, tgt, "Université des Sciences et de la Technologie d'Oran — USTO-MB"))
# 3. low flight: campus -> across the boulevard -> through the USTO quarter (12 s)
n = 12 * FPS
wp = np.array([[4750, 1050], [5150, 900], [5650, 860], [5800, 450], [5950, 150],
               [6250, -100], [6550, -350], [6800, -500]])
cam, tgt = low_flight(wp, n)
shots.append((cam, tgt, "Du campus vers le quartier USTO, en face (vol à 80 m)"))
# 4. rise above the quarter and look back at the university (6 s)
n = 6 * FPS
cam = lerp(cam[-1], np.r_[Q0[0] + 900, Q0[1] - 700, 380], n)
tgt = lerp(tgt[-1], np.r_[U0, 110], n)
shots.append((cam, tgt, "Quartier USTO — regard vers l'université"))
# 5. final (4 s)
n = 4 * FPS
cam = lerp(cam[-1], np.r_[C0[0] + 300, C0[1] - 2200, 1350], n)
tgt = lerp(tgt[-1], np.r_[C0 + [0, 250], 100], n)
shots.append((cam, tgt, "USTO — maquette blanche, relief Copernicus"))
CAM = gaussian_filter1d(np.vstack([s_[0] for s_ in shots]), 5, axis=0)
TGT = gaussian_filter1d(np.vstack([s_[1] for s_ in shots]), 5, axis=0)
labels, t0 = [], 0
for c_, _, name in shots:
    labels.append([t0, t0 + len(c_), name]); t0 += len(c_)
json.dump({"fps": FPS, "cam": to3(CAM).tolist(), "tgt": to3(TGT).tolist(), "labels": labels},
          open(OUT / "scene.json", "w"))
print("frames", len(CAM), "university centre", U0.round(0), "quarter centre", Q0.round(0),
      "ground at centres", round(gz(*U0)), round(gz(*Q0)))
