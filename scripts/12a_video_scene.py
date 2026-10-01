"""Scene + camera path for the fly-through video (white maquette).
Writes /tmp/claude-0/video/{terrain,buildings}.bin + scene.json"""
import json
from pathlib import Path
import numpy as np
from scipy.ndimage import gaussian_filter1d

ROOT = Path(__file__).resolve().parents[1]
OUT = Path("/tmp/claude-0/video")
X0, X1, Y0, Y1 = -1600, 2900, -1100, 2800
d = np.load(ROOT / "results/cache/relief_parts.npz"); tx, ty, tz = d["tx"], d["ty"], d["tz"]
CX, CY = 600.0, 900.0                                   # scene centre (local m)


def tris(F):
    q = F[:, 2] != F[:, 3]
    return np.r_[F[:, [0, 1, 2]], F[q][:, [0, 2, 3]]]


def to3(P):                                             # local (x,y,z) -> three.js (x, z, -y)
    return np.c_[P[:, 0] - CX, P[:, 2], -(P[:, 1] - CY)].astype(np.float32)


i0, i1 = np.searchsorted(tx, X0), np.searchsorted(tx, X1) + 1
j0, j1 = np.searchsorted(ty, Y0), np.searchsorted(ty, Y1) + 1
X, Y = np.meshgrid(tx[i0:i1], ty[j0:j1]); Z = tz[j0:j1, i0:i1]; ny, nx = X.shape
TV = np.c_[X.ravel(), Y.ravel(), Z.ravel()]
i = (np.arange(ny - 1)[:, None] * nx + np.arange(nx - 1)[None, :]).ravel()
TT = np.vstack([np.c_[i, i + 1, i + nx], np.c_[i + 1, i + nx + 1, i + nx]])
# socle walls
base = -60.0; idx = np.arange(nx * ny).reshape(ny, nx)
ring = np.r_[idx[0, :], idx[1:, -1], idx[-1, -2::-1], idx[-2:0:-1, 0]]
n = len(TV); bot = TV[ring].copy(); bot[:, 2] = base
a, b = ring, np.roll(ring, -1); ba = n + np.arange(len(ring)); bb = np.roll(ba, -1)
TV = np.vstack([TV, bot]); TT = np.vstack([TT, np.c_[a, ba, b], np.c_[b, ba, bb]])
(to3(TV)).tofile(OUT / "terrain_pos.bin"); TT.astype(np.uint32).tofile(OUT / "terrain_idx.bin")

Vs, Ts, off = [], [], 0
for k in range(len(d["names"])):
    V, F = d[f"s{k}_V"], d[f"s{k}_F"]; T = tris(F); c = V[T].mean(1)
    m = (c[:, 0] > X0) & (c[:, 0] < X1) & (c[:, 1] > Y0) & (c[:, 1] < Y1)
    T = T[m]
    if not len(T):
        continue
    u, inv = np.unique(T.ravel(), return_inverse=True)
    Vs.append(V[u]); Ts.append(inv.reshape(-1, 3) + off); off += len(u)
BV = np.vstack(Vs); BT = np.vstack(Ts)
# flat shading needs unshared vertices: expand
P = BV[BT].reshape(-1, 3)
to3(P).tofile(OUT / "bld_pos.bin")
print("terrain tris", len(TT), "building tris", len(BT))

# ---------------------------------------------------------------- paths
def ground(x, y):
    from scipy.interpolate import RegularGridInterpolator
    return RegularGridInterpolator((ty, tx), tz, bounds_error=False, fill_value=0)(np.c_[y, x])

xs = np.arange(-600, 2401, 50.0)
edge = []
for x in xs:
    col = tz[:, np.searchsorted(tx, x)]; ok = np.nonzero((col >= 40) & (ty < 3000) & (ty > -500))[0]
    edge.append(ty[ok.max()])
ey = gaussian_filter1d(np.array(edge, float), 3)
front = np.c_[xs, ey]                                    # Front de mer (cliff top)
tang = np.gradient(front, axis=0); tang /= np.linalg.norm(tang, axis=1, keepdims=True)
nrm = np.c_[-tang[:, 1], tang[:, 0]]                     # left normal = towards the sea (north-west)
gz_front = ground(front[:, 0], front[:, 1])
# Larbi Ben M'hidi (approximate): street parallel to the front de mer, ~300 m inland
lbm = (front - 300 * nrm)[(front[:, 0] >= 0) & (front[:, 0] <= 1500)]
gz_lbm = ground(lbm[:, 0], lbm[:, 1])


def resample(P, n):
    s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(P[:, :2], axis=0), axis=1))]
    t = np.linspace(0, s[-1], n)
    return np.stack([np.interp(t, s, P[:, k]) for k in range(P.shape[1])], 1)


FPS = 24
shots = []
# 1. establishing shot from the sea (3 s): orbit-ish dolly
n = 3 * FPS
cam = resample(np.array([[300, 3600, 700], [700, 3100, 520]]), n)
tgt = np.repeat([[700, 1300, 40]], n, 0); shots.append((cam, tgt))
# 2. above the Front de mer (9 s): 90 m above the cliff top, slightly seaward, looking ahead
F3 = np.c_[front, gz_front]
n = 9 * FPS
path = resample(F3, n + 40)
cam = path[:n].copy(); cam[:, :2] += 60 * resample(np.c_[nrm], n + 40)[:n] * 1; cam[:, 2] += 90
tgt = path[40:n + 40].copy(); tgt[:, 2] += 10
# blend from shot 1
shots.append((cam, tgt))
# 3. below: port level, 350 m seaward, flying back west, looking at the cliff (9 s)
n = 9 * FPS
P = np.c_[front + 380 * nrm, np.full(len(front), 22.0)][::-1]
cam = resample(P, n)
T_ = np.c_[front, gz_front + 10][::-1]; tgt = resample(T_, n)
shots.append((cam, tgt))
# 4. rise & go inland to Larbi Ben M'hidi (3 s transition) then along the street (8 s)
L3 = np.c_[lbm, gz_lbm]
n = 3 * FPS
start = shots[-1][0][-1]; end = L3[0] + np.r_[0, 0, 45]
cam = np.array([start + (end - start) * t for t in np.linspace(0, 1, n)]); cam[:, 2] += 120 * np.sin(np.linspace(0, np.pi, n))
tgt = np.array([shots[-1][1][-1] + (L3[3] - shots[-1][1][-1]) * t for t in np.linspace(0, 1, n)])
shots.append((cam, tgt))
n = 8 * FPS
path = resample(L3, n + 30)
cam = path[:n].copy(); cam[:, 2] += 45
tgt = path[30:n + 30].copy(); tgt[:, 2] += 15
shots.append((cam, tgt))
# 5. final rise (3 s)
n = 3 * FPS
start = shots[-1][0][-1]
cam = np.array([start + (np.r_[900, -400, 900] - start) * t ** 0.8 for t in np.linspace(0, 1, n)])
tgt = np.array([shots[-1][1][-1] + (np.r_[700, 1100, 30] - shots[-1][1][-1]) * t for t in np.linspace(0, 1, n)])
shots.append((cam, tgt))
CAM = np.vstack([s[0] for s in shots]); TGT = np.vstack([s[1] for s in shots])
# smooth everything (no jerks at shot boundaries)
CAM = gaussian_filter1d(CAM, 6, axis=0); TGT = gaussian_filter1d(TGT, 6, axis=0)
labels = []
t = 0
for name, s in zip(["Oran — vue depuis la mer", "Front de mer — par le dessus", "Le port — par le dessous",
                    "Vers la rue Larbi Ben M'hidi", "Rue Larbi Ben M'hidi (tracé approximatif)", "Oran — maquette blanche"],
                   shots):
    labels.append([t, t + len(s[0]), name]); t += len(s[0])
json.dump({"fps": FPS, "cam": to3(CAM).tolist(), "tgt": to3(TGT).tolist(), "labels": labels,
           "n_bld_vertices": int(len(P))}, open(OUT / "scene.json", "w"))
print("frames", len(CAM))
