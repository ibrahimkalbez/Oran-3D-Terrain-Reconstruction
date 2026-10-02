"""Fly-through of the USTO quarter, facing the USTO-MB university (Bir El Djir),
along the tram line and the streets around the campus (outside), with a short
pass inside the campus; ground altitude and slope shown live, contour lines of
the file (10 m / 50 m) drawn on the terrain.
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
ROADS = ("HIGHWAY_", "ROUTE_ROAD", "RAILWAY_", "LEISURE_PA")
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
groups = {"terrain": [], "bld": [], "road": [], "tram": []}
contours = {"COURBES_NIVEAU_10m": [], "COURBES_NIVEAU_50m": []}
campus = None
for o in m.Objects:
    g = o.Geometry
    name = m.Layers[o.Attributes.LayerIndex].Name
    if name in contours and isinstance(g, r3.PolylineCurve):
        Q = np.array([[g.Point(k).X, g.Point(k).Y, g.Point(k).Z] for k in range(g.PointCount)])
        ok = (Q[:, 0] > X0) & (Q[:, 0] < X1) & (Q[:, 1] > Y0) & (Q[:, 1] < Y1)
        seg = ok[:-1] & ok[1:]
        if seg.any():
            contours[name].append(np.stack([Q[:-1][seg], Q[1:][seg]], 1).reshape(-1, 3))
        continue
    if not isinstance(g, r3.Mesh):
        continue
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
    elif name == "ROUTE_TRAM":
        key = "tram"
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

for k, L in contours.items():
    P = np.vstack(L); P[:, 2] += 1.5
    to3(P).tofile(OUT / ("cont10_pos.bin" if k.endswith("10m") else "cont50_pos.bin")); print(k, len(P) // 2, "segments")

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


# One continuous low flight. Columns: x, y, camera height above ground, label.
#  a) along the tram street south of the campus (outside, campus on the left)
#  b) short pass inside the campus (between the faculty buildings)
#  c) out by the roundabout, then along the tram through the USTO quarter
WP = [(3750, 330, 40, "a"), (4300, 340, 40, "a"), (4750, 360, 40, "a"), (4900, 420, 55, "b"),
      (4990, 640, 60, "b"), (5120, 850, 60, "b"), (5380, 960, 60, "b"), (5600, 880, 40, "c"),
      (5800, 640, 40, "c"), (5930, 330, 40, "c"), (6010, 80, 40, "c"), (5850, -170, 40, "c"),
      (5620, -400, 40, "c"), (5520, -560, 40, "c")]
LAB = {"a": "Rue du tram, le long de l'université (vue extérieure)",
       "b": "Petit passage à l'intérieur du campus USTO-MB",
       "c": "Le tram à travers le quartier USTO, en face de l'université"}
W = np.array([w[:3] for w in WP], float)
s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(W[:, :2], axis=0), axis=1))]
NLOW, LEAD = 27 * FPS, 45
t = np.linspace(0, s[-1], NLOW + LEAD)
path = gaussian_filter1d(np.c_[np.interp(t, s, W[:, 0]), np.interp(t, s, W[:, 1])], 10, axis=0, mode="nearest")
h = gaussian_filter1d(np.interp(t, s, W[:, 2]), 10, mode="nearest")
zc = G(path[:, 0], path[:, 1])
low_cam = np.c_[path[:NLOW], zc[:NLOW] + h[:NLOW]]
low_tgt = np.c_[path[LEAD:], zc[LEAD:] + 12]
seg = [WP[min(np.searchsorted(s, x, "right") - 1, len(WP) - 1)][3] for x in t[:NLOW]]
# live HUD: ground altitude and slope along the direction of travel (+ = climbing)
d = np.gradient(path[:NLOW], axis=0); d /= np.linalg.norm(d, axis=1, keepdims=True)
fw, bw = path[:NLOW] + 40 * d, path[:NLOW] - 40 * d
slope = 100 * (G(fw[:, 0], fw[:, 1]) - G(bw[:, 0], bw[:, 1])) / 80
slope = gaussian_filter1d(slope, 6)
hud_low = [f"Altitude du sol {z:.0f} m   ·   pente {p:+.1f} %" for z, p in zip(zc[:NLOW], slope)]

shots = []
# 1. approach from the north-west towards the tram street (5 s)
n = 5 * FPS
cam = lerp(np.r_[3000, 2600, 700], low_cam[0] + [-250, 120, 160], n)
tgt = lerp(np.r_[U0, 120], low_tgt[0], n)
shots.append((cam, tgt, ["USTO — arrivée par l'ouest, la rue du tram"] * n, [""] * n))
# 2. the low flight a-b-c (27 s)
shots.append((low_cam, low_tgt, [LAB[k] for k in seg], hud_low))
# 3. slope: side view, low over the quarter, looking north across the contour lines (7 s)
n = 7 * FPS
cam = lerp(low_cam[-1], np.r_[6700, -1500, 260], n)
tgt = lerp(low_tgt[-1], np.r_[5600, 900, 130], n)
shots.append((cam, tgt, ["La pente : courbes de niveau du fichier (10 m / 50 m)"] * n, [""] * n))
# 4. rise and look back at the university and the quarter (5 s)
n = 5 * FPS
cam = lerp(cam[-1], np.r_[C0[0] + 300, C0[1] - 2200, 1250], n)
tgt = lerp(tgt[-1], np.r_[C0 + [0, 250], 100], n)
shots.append((cam, tgt, ["USTO — université, tram et quartier — maquette blanche, relief Copernicus"] * n, [""] * n))
CAM = gaussian_filter1d(np.vstack([s_[0] for s_ in shots]), 5, axis=0, mode="nearest")
TGT = gaussian_filter1d(np.vstack([s_[1] for s_ in shots]), 5, axis=0, mode="nearest")
names = sum([s_[2] for s_ in shots], []); hud = sum([s_[3] for s_ in shots], [])
labels, t0 = [], 0
for k in range(1, len(names) + 1):
    if k == len(names) or names[k] != names[t0]:
        labels.append([t0, k, names[t0]]); t0 = k
json.dump({"fps": FPS, "cam": to3(CAM).tolist(), "tgt": to3(TGT).tolist(), "labels": labels, "hud": hud},
          open(OUT / "scene.json", "w"))
print("frames", len(CAM), "low flight", round(s[-1]), "m; ground", round(zc.min()), "-", round(zc.max()),
      "m; slope", round(slope.min(), 1), "..", round(slope.max(), 1), "%")
for a, b, nme in labels:
    print(a, b, nme)
