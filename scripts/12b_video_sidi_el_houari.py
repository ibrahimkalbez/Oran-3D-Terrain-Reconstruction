"""Fly-through of Sidi El Houari built from the user's Rhino model:
white maquette file (terrain + buildings + draped streets) + the named areas
of the model (district polygon NAME_____________, NAME_PLACE_1ER_NOVEMBRE) in red.
Writes /tmp/claude-0/vseh/*.bin + scene.json for scripts/video/render.mjs"""
import json
from pathlib import Path
import numpy as np
import rhino3dm as r3
from scipy.ndimage import gaussian_filter1d
from scipy.interpolate import RegularGridInterpolator

ROOT = Path(__file__).resolve().parents[1]
OUT = Path("/tmp/claude-0/vseh"); OUT.mkdir(exist_ok=True)
X0, X1, Y0, Y1 = -4200, 300, -900, 2200
CX, CY = -2000.0, 600.0
SRC = ROOT / "results/Oran_maquette_blanche_complete_Rhino8.3dm"
ROADS = ("HIGHWAY_", "ROUTE_ROAD", "RAILWAY_", "ROUTE_TRAM", "LEISURE_PA", "MAN_MADE_PIER", "MAN_MADE_BREAKWATER")
NAMED = {"NAME_____________": "Sidi El Houari", "NAME_PLACE_1ER_NOVEMBRE": "Place du 1er Novembre"}


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


m = r3.File3dm.Read(str(SRC))
groups = {"terrain": [], "bld": [], "road": []}
outlines = {}
for o in m.Objects:
    g = o.Geometry
    if not isinstance(g, r3.Mesh):
        continue
    name = m.Layers[o.Attributes.LayerIndex].Name
    if name == "TERRAIN_ET_SOCLE":
        key = "terrain"
    elif name in NAMED:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        V, T = mesh_np(g)
        poly = unary_union([Polygon(V[t, :2]) for t in T if Polygon(V[t, :2]).area > 1e-6]).buffer(0.5).buffer(-0.5)
        segs = []
        for gg in getattr(poly, "geoms", [poly]):
            ring = np.array(gg.exterior.coords)
            k_ = np.linspace(0, 1, max(2, int(gg.exterior.length / 5)))
            pts = np.array([gg.exterior.interpolate(t, normalized=True).coords[0] for t in k_])
            segs.append(np.repeat(pts, 2, 0)[1:-1])
        P2 = np.vstack(segs)
        outlines[NAMED[name]] = P2
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
for k, L in groups.items():
    P = np.vstack(L); to3(P).tofile(OUT / f"{k}_pos.bin"); print(k, len(P) // 3, "tris")
dG = np.load(ROOT / "results/cache/relief_parts.npz")
G0 = RegularGridInterpolator((dG["ty"], dG["tx"]), dG["tz"], bounds_error=False, fill_value=0)
lines = np.vstack([v for v in outlines.values()])
lines = np.c_[lines, G0(np.c_[lines[:, 1], lines[:, 0]]) + 3.0]
to3(lines).tofile(OUT / "lines_pos.bin")
print("outlines", {k: len(v) // 2 for k, v in outlines.items()})

# ---------------------------------------------------------------- camera
d = np.load(ROOT / "results/cache/relief_parts.npz"); tx, ty, tz = d["tx"], d["ty"], d["tz"]
G = RegularGridInterpolator((ty, tx), tz, bounds_error=False, fill_value=0)
gz = lambda x, y: float(G([[y, x]])[0])
seh = outlines["Sidi El Houari"]; C0 = seh[:, :2].mean(0)                 # district centre
place = outlines["Place du 1er Novembre"][:, :2].mean(0)
FPS = 24


def lerp(a, b, n, ease=True):
    t = np.linspace(0, 1, n); t = t * t * (3 - 2 * t) if ease else t
    return a[None] + (b - a)[None] * t[:, None]


shots, labels = [], []
# 1. approach from the sea (5 s)
n = 5 * FPS
cam = lerp(np.r_[C0[0] + 600, C0[1] + 3200, 650], np.r_[C0[0] + 300, C0[1] + 1700, 420], n)
tgt = np.repeat([np.r_[C0, 70]], n, 0)
shots.append((cam, tgt, "Sidi El Houari — arrivée depuis la mer"))
# 2. orbit around the district (12 s)
n = 12 * FPS
ang = np.linspace(np.radians(80), np.radians(80 + 280), n)
R = 1350
cam = np.c_[C0[0] + R * np.cos(ang), C0[1] + R * np.sin(ang), np.full(n, 380)]
tgt = np.repeat([np.r_[C0, 70]], n, 0)
shots.append((cam, tgt, "Sidi El Houari — tour du quartier (contour rouge : polygone du fichier)"))
# 3. low flight: Place du 1er Novembre -> west through the district (12 s)
n = 12 * FPS
wp = np.array([place + [250, -150], place, [C0[0] + 300, C0[1] + 60], C0, [C0[0] - 600, C0[1] + 80]])
s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(wp, axis=0), axis=1))]
t = np.linspace(0, s[-1], n + 50)
path = np.c_[np.interp(t, s, wp[:, 0]), np.interp(t, s, wp[:, 1])]
path = gaussian_filter1d(path, 8, axis=0)
zc = np.array([gz(*p) for p in path])
cam = np.c_[path[:n], zc[:n] + 55]
tgt = np.c_[path[50:n + 50], zc[50:n + 50] + 20]
shots.append((cam, tgt, "Place du 1er Novembre → cœur de Sidi El Houari (vol à 55 m)"))
# 4. rise towards Santa Cruz and look back over the district (6 s)
n = 6 * FPS
cam = lerp(cam[-1], np.r_[C0[0] - 1500, C0[1] - 300, 420], n)
tgt = lerp(tgt[-1], np.r_[C0 + [300, 100], 60], n)
shots.append((cam, tgt, "Vers Santa Cruz — regard sur Sidi El Houari et le port"))
# 5. final (4 s)
n = 4 * FPS
cam = lerp(cam[-1], np.r_[C0[0] + 200, C0[1] - 1900, 1300], n)
tgt = lerp(tgt[-1], np.r_[C0 + [200, 300], 40], n)
shots.append((cam, tgt, "Sidi El Houari — maquette blanche, relief Copernicus"))
CAM = gaussian_filter1d(np.vstack([s_[0] for s_ in shots]), 5, axis=0)
TGT = gaussian_filter1d(np.vstack([s_[1] for s_ in shots]), 5, axis=0)
t0 = 0
for c, _, name in shots:
    labels.append([t0, t0 + len(c), name]); t0 += len(c)
json.dump({"fps": FPS, "cam": to3(CAM).tolist(), "tgt": to3(TGT).tolist(), "labels": labels},
          open(OUT / "scene.json", "w"))
print("frames", len(CAM), "district centre", C0.round(0), "place", place.round(0))
