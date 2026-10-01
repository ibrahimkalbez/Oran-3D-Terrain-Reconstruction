"""Oblique 3D preview of the Oran relief model (terrain + buildings) rendered
with a painter's algorithm (no GPU needed). Same geometry as the Rhino file.
Usage: python scripts/08_render_3d.py x0 x1 y0 y1 out.png [azimuth_deg elevation_deg]"""
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def tris(F):
    q = F[:, 2] != F[:, 3]
    return np.r_[F[:, [0, 1, 2]], F[q][:, [0, 2, 3]]]


def main(x0, x1, y0, y1, out, az=-35.0, el=32.0):
    d = np.load(ROOT / "results/cache/relief_parts.npz")
    tx, ty, tz = d["tx"], d["ty"], d["tz"]
    i0, i1 = np.searchsorted(tx, x0), np.searchsorted(tx, x1) + 1
    j0, j1 = np.searchsorted(ty, y0), np.searchsorted(ty, y1) + 1
    X, Y = np.meshgrid(tx[i0:i1], ty[j0:j1]); Z = tz[j0:j1, i0:i1]
    nx = X.shape[1]; ny = X.shape[0]
    TV = np.c_[X.ravel(), Y.ravel(), Z.ravel()]
    i = (np.arange(ny - 1)[:, None] * nx + np.arange(nx - 1)[None, :]).ravel()
    TT = np.vstack([np.c_[i, i + 1, i + nx], np.c_[i + 1, i + nx + 1, i + nx]])
    P = [TV[TT]]; kind = [np.zeros(len(TT))]
    for k in range(len(d["names"])):
        V, F = d[f"s{k}_V"], d[f"s{k}_F"]
        T = tris(F); Q = V[T]
        c = Q[:, :, :2].mean(1)
        m = (c[:, 0] > x0) & (c[:, 0] < x1) & (c[:, 1] > y0) & (c[:, 1] < y1)
        P.append(Q[m]); kind.append(np.ones(m.sum()))
    P = np.concatenate(P); kind = np.concatenate(kind)
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    a, e = np.radians(az), np.radians(el)
    view = np.array([np.sin(a) * np.cos(e), -np.cos(a) * np.cos(e), np.sin(e)])  # towards camera
    n = np.where((n @ view)[:, None] < 0, -n, n)             # two-sided lighting
    light = np.array([-0.4, -0.5, 0.77]); light /= np.linalg.norm(light)
    sh = 0.35 + 0.65 * np.clip(n @ light, 0, 1)
    # camera axes
    right = np.array([np.cos(a), np.sin(a), 0.0])
    up = np.cross(view, right) * -1
    up = np.cross(right, view)
    C = P - P.reshape(-1, 3).mean(0)
    u, v, depth = C @ right, C @ up, C @ view
    order = np.argsort(depth.mean(1))                          # far first
    base_t = plt.cm.gist_earth(np.clip((P[:, :, 2].mean(1) + 80) / 730, 0, 1))[:, :3]
    base_b = np.array([0.93, 0.89, 0.82])
    col = np.where(kind[:, None] == 1, base_b, base_t) * sh[:, None]
    fig, ax = plt.subplots(figsize=(16, 10), facecolor="#dfe7ee")
    pc = PolyCollection(np.stack([u, v], -1)[order], facecolors=np.clip(col[order], 0, 1),
                        edgecolors="none", antialiased=False)
    ax.add_collection(pc); ax.autoscale(); ax.set_aspect("equal"); ax.axis("off")
    ax.set_title("Oran — modèle Rhino posé sur le relief Copernicus (vue 3D, échelle réelle)", fontsize=14)
    fig.savefig(out, dpi=110, bbox_inches="tight", facecolor=fig.get_facecolor())


if __name__ == "__main__":
    a = sys.argv[1:]
    main(*map(float, a[:4]), a[4], *(map(float, a[5:7]) if len(a) > 5 else []))
