"""Second USTO video: a 30 s walk-through inside the USTO-MB campus, from the
white maquette Rhino file. Reuses the scene buffers written by
scripts/12c_video_usto.py (/tmp/claude-0/vusto/*.bin) and writes
scene_campus.json, rendered with scripts/video/index_usto.html?scene=scene_campus.json
The route is computed on the ground: shortest path on a 4 m grid that prefers
the roads of the file and never enters a building footprint (+14 m margin),
from the south entrance (tram side) past the west faculty buildings, then up
the main alley along the central faculty complex to the north-east; the camera flies at roof level, 30-34 m above the ground."""
import json
from pathlib import Path
import numpy as np
import rhino3dm as r3
from scipy.ndimage import gaussian_filter1d
from scipy.interpolate import LinearNDInterpolator
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely import contains_xy
from skimage.graph import route_through_array

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "results/Oran_maquette_blanche_complete_Rhino8.3dm"
OUT = Path("/tmp/claude-0/vusto")
CX, CY = 5600.0, 400.0
FPS, SECONDS, LEAD = 24, 30, 36
XA, YA, XB, YB, S = 4380, 450, 5650, 1450, 4.0


def to3(P):
    return np.c_[P[:, 0] - CX, P[:, 2], -(P[:, 1] - CY)].astype(np.float32)


def tris(g):
    V = np.array([[p.X, p.Y, p.Z] for p in g.Vertices])
    F = np.array([tuple(g.Faces[k]) for k in range(g.Faces.Count)]).reshape(-1, 4)
    q = F[:, 2] != F[:, 3]
    T = V[np.r_[F[:, [0, 1, 2]], F[q][:, [0, 2, 3]]]]
    c = T[:, :, :2].mean(1)
    return T[(c[:, 0] > XA - 50) & (c[:, 0] < XB + 50) & (c[:, 1] > YA - 50) & (c[:, 1] < YB + 50)]


def footprint(T):
    return unary_union([p for p in (Polygon(t[:, :2]) for t in T) if p.area > 0.1])


m = r3.File3dm.Read(str(SRC))
bld, road, terr, uni = [], [], [], []
for o in m.Objects:
    g = o.Geometry
    if not isinstance(g, r3.Mesh):
        continue
    n = m.Layers[o.Attributes.LayerIndex].Name
    if n.startswith(("BUILDING", "HEIGHT_", "EXTRA_BUILD")):
        bld.append(tris(g))
    elif n.startswith("HIGHWAY_"):
        road.append(tris(g))
    elif n == "TERRAIN_ET_SOCLE":
        terr.append(tris(g))
    elif n == "AMENITY_UN":
        uni.append(tris(g))
U = footprint(np.vstack(uni)).buffer(1).buffer(-1)
campus = max(getattr(U, "geoms", [U]), key=lambda p: p.area)
TP = np.vstack(terr).reshape(-1, 3); TP = np.unique(TP[TP[:, 2] > -50].round(2), axis=0)
G = LinearNDInterpolator(TP[:, :2], TP[:, 2], fill_value=150.0)

nx, ny = int((XB - XA) / S), int((YB - YA) / S)
gx, gy = np.meshgrid(XA + S * (np.arange(nx) + .5), YA + S * (np.arange(ny) + .5))
blocked = contains_xy(footprint(np.vstack(bld)).buffer(14), gx, gy)
onroad = contains_xy(footprint(np.vstack(road)).buffer(2), gx, gy)
cost = np.where(onroad, 1.0, 6.0); cost[~contains_xy(campus.buffer(-6), gx, gy)] = 1e6; cost[blocked] = 1e6
ij = lambda x, y: (int((y - YA) / S), int((x - XA) / S))
legs = [(5180, 665), (4800, 840), (5040, 1040), (5320, 1180)]   # south entrance -> west faculties -> main alley -> north-east
path = []
for a, b in zip(legs[:-1], legs[1:]):
    p, _ = route_through_array(cost, ij(*a), ij(*b), fully_connected=True, geometric=True)
    path += [(XA + S * (j + .5), YA + S * (i + .5)) for i, j in p]
P = gaussian_filter1d(np.array(path), 6, axis=0, mode="nearest")
s = np.r_[0, np.cumsum(np.hypot(*np.diff(P, axis=0).T))]
k_cross = np.argmin(np.hypot(P[:, 0] - 5040, P[:, 1] - 1040))
k_west = np.argmin(np.hypot(P[:, 0] - 4800, P[:, 1] - 840))

# time law: constant speed to the crossing (24 s), then slow down along the main alley (6 s)
N = SECONDS * FPS; n1 = 24 * FPS
u = np.r_[np.linspace(0, s[k_cross], n1, endpoint=False),
          s[k_cross] + (s[-1] - s[k_cross]) * np.sin(np.linspace(0, np.pi / 2, N - n1 + LEAD))]
xy = np.c_[np.interp(u, s, P[:, 0]), np.interp(u, s, P[:, 1])]
z = G(xy[:, 0], xy[:, 1])
h = np.interp(u, [0, 120, s[k_cross], s[-1]], [30, 34, 34, 30])
cam = np.c_[xy[:N], z[:N] + h[:N]]
tgt = np.c_[xy[LEAD:N + LEAD], z[LEAD:N + LEAD] + 9]
# around the central complex, turn the head towards it
CC = np.r_[5160, 860]
a = np.interp(u[:N], [0, s[k_west], s[k_west] + 150, s[-1]], [0, 0, 0.3, 0.45])[:, None]
tgt[:, :2] = tgt[:, :2] * (1 - a) + CC * a
tgt[:, 2] = tgt[:, 2] * (1 - a[:, 0]) + (G(*CC) + 14) * a[:, 0]
end_tgt = np.r_[5180, 900, G(5180, 900) + 18]                 # end: look back at the central complex
w = np.clip((np.arange(N) - (N - 3 * FPS)) / (3 * FPS), 0, 1)[:, None]
tgt = tgt * (1 - w) + end_tgt * w
CAM = gaussian_filter1d(cam, 4, axis=0, mode="nearest"); TGT = gaussian_filter1d(tgt, 6, axis=0, mode="nearest")


def lab(d):
    if d < 160: return "Entrée de l'université, côté tram"
    if d < s[k_west]: return "Les bâtiments d'enseignement, côté ouest"
    if d < s[k_cross]: return "Vers l'allée principale du campus"
    return "Allée principale — le grand complexe central des facultés"


names = ["Promenade dans l'université USTO-MB"] * (2 * FPS) + [lab(d) for d in u[2 * FPS:N]]
labels, t0 = [], 0
for k in range(1, N + 1):
    if k == N or names[k] != names[t0]:
        labels.append([t0, k, names[t0]]); t0 = k
hud = [f"Altitude du sol {zz:.0f} m" for zz in z[:N]]
json.dump({"fps": FPS, "cam": to3(CAM).tolist(), "tgt": to3(TGT).tolist(), "labels": labels, "hud": hud},
          open(OUT / "scene_campus.json", "w"))
print("frames", N, "route", round(s[-1]), "m (to crossing", round(s[k_cross]), "m)")
for a, b, n in labels:
    print(a, b, n)
