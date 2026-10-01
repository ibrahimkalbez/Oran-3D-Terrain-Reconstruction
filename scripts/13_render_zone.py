"""White-maquette preview of a zone (terrain + whole buildings + socle), painter's algorithm.
Usage: python scripts/13_render_zone.py x0 x1 y0 y1 out.png "title" [az el zexag step]"""
import sys
import numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from scipy.interpolate import RegularGridInterpolator
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def tris(F):
    q = F[:, 2] != F[:, 3]
    return np.r_[F[:, [0, 1, 2]], F[q][:, [0, 2, 3]]]


def main(x0, x1, y0, y1, out, title, az=-150, el=38, zex=1.0, step=10.0):
    d = np.load(ROOT / "results/cache/relief_parts.npz"); tx, ty, tz = d["tx"], d["ty"], d["tz"]
    Tg = RegularGridInterpolator((ty, tx), tz)
    xs = np.arange(x0, x1 + 1, step); ys = np.arange(y0, y1 + 1, step); X, Y = np.meshgrid(xs, ys)
    Z = Tg(np.c_[Y.ravel(), X.ravel()]).reshape(X.shape)
    ny, nx = X.shape; TV = np.c_[X.ravel(), Y.ravel(), Z.ravel()]
    i = (np.arange(ny - 1)[:, None] * nx + np.arange(nx - 1)[None, :]).ravel()
    P = [TV[np.vstack([np.c_[i, i + 1, i + nx], np.c_[i + 1, i + nx + 1, i + nx]])]]; K = [np.zeros(len(P[0]))]
    base = Z.min() - 0.08 * (x1 - x0) / zex; idx = np.arange(nx * ny).reshape(ny, nx)
    for ring in (idx[0, :], idx[:, 0], idx[-1, :], idx[:, -1]):
        a = TV[ring[:-1]]; b = TV[ring[1:]]; a2 = a.copy(); a2[:, 2] = base; b2 = b.copy(); b2[:, 2] = base
        P += [np.stack([a, b, b2], 1), np.stack([a, b2, a2], 1)]; K += [np.full(len(a), 2)] * 2
    for k in range(len(d["names"])):
        V, F, Pp = d[f"s{k}_V"], d[f"s{k}_F"], d[f"s{k}_P"]
        T = tris(F); tp = Pp[T[:, 0]]
        cx = np.bincount(Pp, V[:, 0]) / np.maximum(np.bincount(Pp), 1)
        cy = np.bincount(Pp, V[:, 1]) / np.maximum(np.bincount(Pp), 1)
        inside = (cx > x0) & (cx < x1) & (cy > y0) & (cy < y1)
        Q = V[T[inside[tp]]]
        # keep whole buildings, but clamp to the plate so nothing hangs outside
        if len(Q):
            ok = (Q[:, :, 0].min(1) >= x0) & (Q[:, :, 0].max(1) <= x1) & (Q[:, :, 1].min(1) >= y0) & (Q[:, :, 1].max(1) <= y1)
            Q = Q[ok]
        P.append(Q); K.append(np.ones(len(Q)))
    P = np.concatenate(P); K = np.concatenate(K)
    P[:, :, 2] *= zex
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]); n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    a, e = np.radians(az), np.radians(el)
    view = np.array([np.sin(a) * np.cos(e), -np.cos(a) * np.cos(e), np.sin(e)]); n = np.where((n @ view)[:, None] < 0, -n, n)
    L = np.array([-0.3, 0.6, 0.75]); L /= np.linalg.norm(L); sh = 0.35 + 0.65 * np.clip(n @ L, 0, 1)
    right = np.array([np.cos(a), np.sin(a), 0.]); up = np.cross(right, view)
    C = P - P.reshape(-1, 3).mean(0); u, v, dep = C @ right, C @ up, C @ view; o = np.argsort(dep.mean(1))
    bc = np.where(K[:, None] == 1, np.array([.98, .98, .98]), np.where(K[:, None] == 2, np.array([.5, .5, .5]), np.array([.80, .80, .78])))
    fig, ax = plt.subplots(figsize=(16, 10), facecolor="#2b2b2b")
    ax.add_collection(PolyCollection(np.stack([u, v], -1)[o], facecolors=np.clip(bc * sh[:, None], 0, 1)[o],
                                     edgecolors="none", antialiased=False))
    ax.autoscale(); ax.set_aspect("equal"); ax.axis("off")
    ax.set_title(title, color="white", fontsize=13)
    fig.savefig(out, dpi=105, bbox_inches="tight", facecolor=fig.get_facecolor())
    print("z range", round(float(Z.min()), 1), round(float(Z.max()), 1))


if __name__ == "__main__":
    a = sys.argv[1:]
    main(*map(float, a[:4]), a[4], a[5], *map(float, a[6:]))
