"""Figures for the PDF report, Tschumi palette (black / greys / red)."""
import sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LightSource, LinearSegmentedColormap
from matplotlib.collections import PolyCollection

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "documentation/figures"
RED, BLACK = "#E30613", "#111111"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": BLACK,
                     "axes.linewidth": 0.8, "axes.spines.top": False, "axes.spines.right": False})
GREYS = LinearSegmentedColormap.from_list("g", ["#1a1a1a", "#bdbdbd", "#f2f2f2"])
d = np.load(ROOT / "results/cache/relief_parts.npz")
tx, ty, tz = d["tx"], d["ty"], d["tz"]


def tris(F):
    q = F[:, 2] != F[:, 3]
    return np.r_[F[:, [0, 1, 2]], F[q][:, [0, 2, 3]]]


def fig_map():
    ls = LightSource(azdeg=315, altdeg=35)
    rgb = ls.shade(tz[::-1], cmap=GREYS, vert_exag=3, dx=25, dy=25, blend_mode="soft", vmin=-50, vmax=650)
    fig, ax = plt.subplots(figsize=(10, 7.6))
    ax.imshow(rgb, extent=[tx[0] / 1e3, tx[-1] / 1e3, ty[0] / 1e3, ty[-1] / 1e3])
    for i in range(len(d["names"])):
        V = d[f"s{i}_V"]
        ax.plot(V[::5, 0] / 1e3, V[::5, 1] / 1e3, ",", color=RED, alpha=0.55)
    ax.set_xlabel("km (Transverse Mercator local, origine 35,699 N / 0,635 W)"); ax.set_ylabel("km")
    fig.savefig(OUT / "f_map.png", dpi=170, bbox_inches="tight")


def fig_3d():
    x0, x1, y0, y1 = -2200, 700, -700, 1700
    i0, i1 = np.searchsorted(tx, x0), np.searchsorted(tx, x1) + 1
    j0, j1 = np.searchsorted(ty, y0), np.searchsorted(ty, y1) + 1
    X, Y = np.meshgrid(tx[i0:i1], ty[j0:j1]); Z = tz[j0:j1, i0:i1]
    ny, nx = X.shape
    TV = np.c_[X.ravel(), Y.ravel(), Z.ravel()]
    i = (np.arange(ny - 1)[:, None] * nx + np.arange(nx - 1)[None, :]).ravel()
    P = [TV[np.vstack([np.c_[i, i + 1, i + nx], np.c_[i + 1, i + nx + 1, i + nx]])]]
    kind = [np.zeros(len(P[0]))]
    for k in range(len(d["names"])):
        V, F = d[f"s{k}_V"], d[f"s{k}_F"]; Q = V[tris(F)]
        c = Q[:, :, :2].mean(1); m = (c[:, 0] > x0) & (c[:, 0] < x1) & (c[:, 1] > y0) & (c[:, 1] < y1)
        P.append(Q[m]); kind.append(np.ones(m.sum()))
    P = np.concatenate(P); kind = np.concatenate(kind)
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]); n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    a, e = np.radians(-30), np.radians(30)
    view = np.array([np.sin(a) * np.cos(e), -np.cos(a) * np.cos(e), np.sin(e)])
    n = np.where((n @ view)[:, None] < 0, -n, n)
    light = np.array([-0.4, -0.5, 0.77]); light /= np.linalg.norm(light)
    sh = 0.3 + 0.7 * np.clip(n @ light, 0, 1)
    right = np.array([np.cos(a), np.sin(a), 0.0]); up = np.cross(right, view)
    C = P - P.reshape(-1, 3).mean(0); u, v, dep = C @ right, C @ up, C @ view
    o = np.argsort(dep.mean(1))
    col = np.where(kind[:, None] == 1, np.array([0.89, 0.02, 0.07]) * 0.55 + 0.45 * sh[:, None],
                   np.repeat(sh[:, None], 3, 1) * 0.92)
    col = np.where(kind[:, None] == 1, np.array([0.89, 0.02, 0.07]) * (0.5 + 0.5 * sh[:, None]), col)
    fig, ax = plt.subplots(figsize=(12, 6.5), facecolor="white")
    ax.add_collection(PolyCollection(np.stack([u, v], -1)[o], facecolors=np.clip(col[o], 0, 1),
                                     edgecolors="none", antialiased=False))
    ax.autoscale(); ax.set_aspect("equal"); ax.axis("off")
    fig.savefig(OUT / "f_3d.png", dpi=170, bbox_inches="tight")


def fig_mercator():
    phi = np.linspace(0, 60, 300)
    fig, ax = plt.subplots(figsize=(6, 3.4))
    ax.plot(phi, 1 / np.cos(np.radians(phi)), color=BLACK, lw=1.4)
    p = 35.699; k = 1 / np.cos(np.radians(p))
    ax.axvline(p, color=RED, lw=0.8, ls="--"); ax.axhline(k, color=RED, lw=0.8, ls="--")
    ax.plot([p], [k], "o", color=RED)
    ax.text(p + 1.5, k - 0.12, f"Oran  phi = {p} deg\nk = sec(phi) = {k:.4f}", color=RED)
    ax.set_xlabel("latitude phi (deg)"); ax.set_ylabel("facteur d'échelle Mercator k")
    fig.savefig(OUT / "f_mercator.png", dpi=200, bbox_inches="tight")


def fig_coast():
    S = Path("/tmp/claude-0/-home-user-Oran-3D-Terrain-Reconstruction/31713da4-23bc-5877-a4c6-11f70042439a/scratchpad")
    sys.path.insert(0, str(ROOT / "scripts"))
    from oran_georef import model_to_local
    M = np.load(S / "bay_pts.npy")
    lx, ly = model_to_local(M[:, 0], M[:, 1])
    fig, ax = plt.subplots(figsize=(10, 5.2))
    sea = tz <= 0.01
    ax.contour(tx / 1e3, ty / 1e3, sea.astype(float), levels=[0.5], colors=BLACK, linewidths=1.2)
    ax.plot(lx / 1e3, ly / 1e3, ",", color=RED)
    ax.plot([], [], color=BLACK, label="trait de côte Copernicus (z = 0)")
    ax.plot([], [], color=RED, label="trait de côte du modèle Rhino (calque BAY), après calage")
    ax.legend(frameon=False, loc="lower right"); ax.set_aspect("equal")
    ax.set_xlim(tx[0] / 1e3, tx[-1] / 1e3); ax.set_ylim(-2, ty[-1] / 1e3)
    ax.set_xlabel("km"); ax.set_ylabel("km")
    fig.savefig(OUT / "f_coast.png", dpi=180, bbox_inches="tight")


def fig_drape():
    x = np.linspace(0, 100, 401)
    nodes = np.arange(0, 101, 25); zn = np.array([10, 18, 14, 26, 20])
    t = np.interp(x, nodes, zn)
    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.plot(x, t, color=BLACK, lw=1.6, label="terrain T(x) (linéaire par morceaux)")
    for nd in nodes:
        ax.axvline(nd, color="#bbbbbb", lw=0.6)
    ax.plot([0, 100], [10, 20], color=RED, lw=1.4, label="arête non raffinée")
    k = np.argmax(np.abs(np.interp(nodes, [0, 100], [10, 20]) - zn))
    ax.annotate("", xy=(nodes[k], zn[k]), xytext=(nodes[k], np.interp(nodes[k], [0, 100], [10, 20])),
                arrowprops=dict(arrowstyle="<->", color=RED))
    ax.text(nodes[k] + 2, 15.5, "écart max.\n(sur une ligne\nde la grille)", color=RED)
    xs = np.r_[0, 25, 50, 75, 100]; ax.plot(xs, np.interp(xs, nodes, zn) + 0.6, "--", color="#555555",
                                             lw=1, label="arête raffinée + décalage")
    ax.legend(frameon=False, fontsize=8, loc="upper left"); ax.set_xlabel("m"); ax.set_ylabel("z (m)")
    fig.savefig(OUT / "f_drape.png", dpi=200, bbox_inches="tight")


def fig_hist():
    import json
    r = json.loads((ROOT / "export_3d_print/print_report.json").read_text())
    fig, axs = plt.subplots(1, 2, figsize=(10, 3.2))
    # building footprint min dim at print scale
    dims = []
    for i in range(len(d["names"])):
        V, F, P = d[f"s{i}_V"], d[f"s{i}_F"], d[f"s{i}_P"]
        lo = np.full(P.max() + 1, np.inf); hi = np.full(P.max() + 1, -np.inf)
        for ax_ in (0, 1):
            l2 = np.full(P.max() + 1, np.inf); h2 = np.full(P.max() + 1, -np.inf)
            np.minimum.at(l2, P, V[:, ax_]); np.maximum.at(h2, P, V[:, ax_])
            dims.append(h2 - l2)
    dd = np.minimum(dims[0::2][0] if False else np.concatenate(dims[0::2]), np.concatenate(dims[1::2]))
    dd = dd[np.isfinite(dd)] * r["mm_per_m"]
    axs[0].hist(np.clip(dd, 0, 1.2), bins=60, color=BLACK)
    axs[0].axvline(0.4, color=RED, lw=1.2); axs[0].text(0.42, axs[0].get_ylim()[1] * 0.8, "buse 0,4 mm", color=RED)
    axs[0].set_xlabel("plus petite dimension d'un bâtiment imprimé (mm)"); axs[0].set_ylabel("bâtiments")
    import json as _j
    rr = _j.loads((ROOT / "results/relief_report.json").read_text())
    tol = [v["tol_m"] for v in rr["drape_tolerance_m"].values()]
    dev = [v["max_dev_m"] for v in rr["drape_tolerance_m"].values()]
    axs[1].scatter(tol, dev, s=10, color=BLACK)
    axs[1].plot([0, 0.55], [0, 0.55], color=RED, lw=1)
    axs[1].set_xlabel("tolérance du calque (m)"); axs[1].set_ylabel("écart max. mesuré (m)")
    fig.savefig(OUT / "f_hist.png", dpi=200, bbox_inches="tight")


if __name__ == "__main__":
    for f in (fig_map, fig_mercator, fig_coast, fig_drape, fig_hist, fig_3d):
        f(); print(f.__name__, "ok", flush=True)
