"""Third USTO video: the large social housing towers next to USTO, reached
from the university's second (west) entrance. From the white maquette Rhino
file; reuses the scene buffers written by scripts/12c_video_usto.py
(/tmp/claude-0/vusto/*.bin) and writes /tmp/claude-0/vusto/tours/*.
The tower ensemble is the group of the file's tallest buildings next to the
campus (35-40 m towers + 25-30 m slabs, south-west of the campus, between the
campus west road and the southern bypass); the file has no name for it, so
its outline is traced on the file's roads."""
import json
from pathlib import Path
import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.interpolate import LinearNDInterpolator
from shapely.geometry import Polygon
from shapely import contains_xy

SRC = Path("/tmp/claude-0/vusto"); OUT = SRC / "tours"; OUT.mkdir(exist_ok=True)
CX, CY = 5600.0, 400.0
FPS = 24
TOWERS = Polygon([(4130, 20), (4470, 20), (4470, 95), (4735, 95), (4735, -140), (4470, -140),
                  (4470, -170), (4130, -170)])


def from3(P):
    return np.c_[P[:, 0] + CX, -P[:, 2] + CY, P[:, 1]]


def to3(P):
    return np.c_[P[:, 0] - CX, P[:, 2], -(P[:, 1] - CY)].astype(np.float32)


def rd(name):
    return from3(np.fromfile(SRC / name, np.float32).reshape(-1, 3).astype(float))


# buildings: towers in bright red, university light red, everything else white
B = np.vstack([rd("bld_pos.bin"), rd("bldq_pos.bin")]); U = rd("bldu_pos.bin")
c = B.reshape(-1, 3, 3).mean(1)
it = np.repeat(contains_xy(TOWERS, c[:, 0], c[:, 1]), 3)
to3(B[~it]).tofile(OUT / "bld_pos.bin"); to3(B[it]).tofile(OUT / "bldt_pos.bin"); to3(U).tofile(OUT / "bldu_pos.bin")
for f in ("terrain_pos.bin", "road_pos.bin", "tram_pos.bin", "cont10_pos.bin", "cont50_pos.bin", "lines_campus_pos.bin"):
    (OUT / f).unlink(missing_ok=True); (OUT / f).symlink_to(SRC / f)
TB = B[it]; hz = TB[:, 2]
print("tower ensemble:", len(TB) // 3, "triangles")

TP = rd("terrain_pos.bin"); TP = TP[(TP[:, 2] > -50) & (np.hypot(TP[:, 0] - 4700, TP[:, 1] - 400) < 2500)]
TP = np.unique(TP.round(2), axis=0)
G = LinearNDInterpolator(TP[:, :2], TP[:, 2], fill_value=150.0)
k_ = np.linspace(0, 1, int(TOWERS.exterior.length / 5))
ring = np.array([TOWERS.exterior.interpolate(t, normalized=True).coords[0] for t in k_])
seg = np.repeat(ring, 2, 0)[1:-1]
to3(np.c_[seg, G(seg[:, 0], seg[:, 1]) + 3]).tofile(OUT / "lines_tours_pos.bin")
T0 = np.array(TOWERS.centroid.coords[0])
top = np.percentile(hz, 99.5); print("ensemble centre", T0.round(0), "highest roof", round(top), "m a.s.l.,",
                                       round(top - G(*T0)), "m above ground")

# route: inside the campus -> second (west) entrance -> campus west road -> into the tower ensemble
RW = np.array([(4620, 800), (4560, 775), (4520, 745), (4463, 715), (4462, 500), (4462, 250),
               (4462, 80), (4440, 28), (4330, 22), (4200, 20), (4080, 20)])
s = np.r_[0, np.cumsum(np.hypot(*np.diff(RW, axis=0).T))]
t = np.linspace(0, s[-1], 600)
route = gaussian_filter1d(np.c_[np.interp(t, s, RW[:, 0]), np.interp(t, s, RW[:, 1])], 6, axis=0, mode="nearest")
rs = np.repeat(route, 2, 0)[1:-1]
to3(np.c_[rs, G(rs[:, 0], rs[:, 1]) + 2.5]).tofile(OUT / "lines_route_pos.bin")


def lerp(a, b, n):
    u = np.linspace(0, 1, n); u = u * u * (3 - 2 * u)
    return a[None] + (b - a)[None] * u[:, None]


shots = []
# 1. establishing: from the south-west, the towers in front, the campus behind (5 s)
n = 5 * FPS
cam = lerp(np.r_[3500, -1500, 520], np.r_[3900, -800, 300], n)
tgt = lerp(np.r_[4700, 500, 150], np.r_[4500, 150, 170], n)
shots.append((cam, tgt, ["Les grandes tours à côté de l'USTO — vue d'ensemble"] * n, [""] * n))
# 2-3. low flight along the route (19 s): exit of the university, road, entrance of the towers
n, lead = 19 * FPS, 40
u = np.linspace(0, len(route) - 1, n + lead)
xy = np.c_[np.interp(u, np.arange(len(route)), route[:, 0]), np.interp(u, np.arange(len(route)), route[:, 1])]
z = G(xy[:, 0], xy[:, 1]); sl = np.interp(u, np.arange(len(route)), t)
h = np.interp(sl, [0, s[3], s[6], s[-1]], [32, 30, 40, 52])
cam = np.c_[xy[:n], z[:n] + h[:n]]
tgt = np.c_[xy[lead:], z[lead:] + 14]
# inside the ensemble, look sideways at the towers (to the south)
a = np.interp(sl[:n], [0, s[6], s[7], s[-1]], [0, 0, 0.45, 0.45])[:, None]
tgt[:, :2] = tgt[:, :2] * (1 - a) + (xy[:n] + [0, -120]) * a
names = []
for d in sl[:n]:
    names.append("Sortie de l'université par la seconde entrée (côté ouest)" if d < s[3] + 40 else
                 "La route vers la cité des grandes tours" if d < s[6] else
                 "Entrée de la cité — les grandes tours sociales")
hud = [f"Altitude du sol {zz:.0f} m" for zz in z[:n]]
shots.append((cam, tgt, names, hud))
# 4. orbit around the tower ensemble (10 s)
n = 10 * FPS
p0 = cam[-1]; a0 = np.arctan2(p0[1] - T0[1], p0[0] - T0[0])
ang = a0 + np.radians(np.linspace(0, 300, n)); R = np.linspace(480, 560, n)
cam = np.c_[T0[0] + R * np.cos(ang), T0[1] + R * np.sin(ang), np.linspace(p0[2] + 60, G(*T0) + 170, n)]
tgt = np.repeat([np.r_[T0, G(*T0) + 20]], n, 0)
shots.append((cam, tgt, ["Les grandes tours et barres — tour complet"] * n, [""] * n))
# 5. final: rise, the towers and the university together (5 s)
n = 5 * FPS
cam = lerp(cam[-1], np.r_[3900, -1300, 700], n)
tgt = lerp(tgt[-1], np.r_[4750, 450, 150], n)
shots.append((cam, tgt, ["Les tours, la route et l'université USTO-MB — maquette blanche"] * n, [""] * n))

CAM = gaussian_filter1d(np.vstack([x[0] for x in shots]), 5, axis=0, mode="nearest")
TGT = gaussian_filter1d(np.vstack([x[1] for x in shots]), 6, axis=0, mode="nearest")
names = sum([x[2] for x in shots], []); hud = sum([x[3] for x in shots], [])
labels, t0 = [], 0
for k in range(1, len(names) + 1):
    if k == len(names) or names[k] != names[t0]:
        labels.append([t0, k, names[t0]]); t0 = k
json.dump({"fps": FPS, "cam": to3(CAM).tolist(), "tgt": to3(TGT).tolist(), "labels": labels, "hud": hud},
          open(OUT / "scene.json", "w"))
print("frames", len(CAM), "route", round(s[-1]), "m")
for a_, b_, n_ in labels:
    print(a_, b_, n_)
