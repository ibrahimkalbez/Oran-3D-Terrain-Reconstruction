"""Step 52 - 15 s overview video of the computed wind flow over the city centre and Gambetta (one map).

Plan view at pedestrian height (2 m above the ground) of the OpenFOAM solution of one scenario, in the style of
a CFD report: speed in colour (jet), white streaks advected by the computed horizontal wind, buildings in grey,
annotations appearing one after the other. The annotated zones are detected in the field itself
(stagnation U < 0.6 m/s, acceleration U > 1.2 Uref, reversed flow), never placed by hand.

  python 52_carte_flux_global.py <case_dir> <out_mp4> [duration_s]
"""
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import shapely  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402
from scipy import ndimage  # noqa: E402
from scipy.interpolate import RegularGridInterpolator  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GEO = ROOT / "results/cfd/geometrie"
SECT = json.loads((ROOT / "results/cfd/secteurs.json").read_text())
KEYS = ("CENTRE_VILLE", "GAMBETTA")
NAMES = {"CENTRE_VILLE": "CENTRE-VILLE", "GAMBETTA": "GAMBETTA"}
BUFFER = 300.0          # buildings are modelled in the sector boxes + 300 m (43_geometrie_cfd.py)
STEP = 5.0
FPS = 25
SPEEDUP = 30.0          # streak motion accelerated x30 (stated on screen)
DIRS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]


def load_pp():
    sp = importlib.util.spec_from_file_location("pp", ROOT / "scripts/cfd/45_postprocess.py")
    pp = importlib.util.module_from_spec(sp); sp.loader.exec_module(pp)
    return pp


def field(case):
    """Speed and horizontal velocity on a 5 m grid at 2 m above the terrain, buildings masked."""
    pp = load_pp()
    meta = json.loads((case / "scenario.json").read_text()); sc = meta["scenario"]
    times = sorted(float(p.name) for p in case.iterdir() if p.name.replace(".", "").isdigit() and p.name != "0")
    C = pp.mesh_centres(case); U = pp.read_list(case / f"{times[-1]:g}" / "U")
    b = np.array([SECT[k]["model_box_m"] for k in KEYS])
    x0, y0 = b[:, 0].min() - 60, b[:, 1].min() - 60; x1, y1 = b[:, 2].max() + 60, b[:, 3].max() + 60
    xs, ys = np.arange(x0, x1, STEP), np.arange(y0, y1, STEP)
    X, Y = np.meshgrid(xs, ys)
    g = np.load(GEO / "terrain_grid.npz")
    zg = RegularGridInterpolator((g["ys"], g["xs"]), g["Z"], bounds_error=False, fill_value=0)
    Q = np.c_[X.ravel(), Y.ravel(), zg(np.c_[Y.ravel(), X.ravel()]) + 2.0]
    d, i = cKDTree(C).query(Q, k=8)
    w = 1.0 / np.maximum(d, 0.1) ** 2; w /= w.sum(1, keepdims=True)
    V = (U[i] * w[..., None]).sum(1)
    polys = []
    for k in SECT:
        txt = (case / "constant/triSurface" / f"buildings_{k}.stl").read_text().split("vertex")[1:]
        T = np.array([list(map(float, t.split()[:3])) for t in txt]).reshape(-1, 3, 3)
        nz = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
        polys += [Polygon(t[:, :2]) for t in T[nz[:, 2] > 0.9 * np.linalg.norm(nz, axis=1)] if Polygon(t[:, :2]).area > 1e-3]
    bld = shapely.contains_xy(unary_union(polys), Q[:, 0], Q[:, 1]).reshape(X.shape)
    S = np.linalg.norm(V, axis=1).reshape(X.shape)
    uref = sc["U10_ref_ms"]
    art = S > 3 * uref                      # isolated numerical artefacts (reported by 45_postprocess)
    S[bld | art] = np.nan
    Vx = np.where(bld, 0, V[:, 0].reshape(X.shape)); Vy = np.where(bld, 0, V[:, 1].reshape(X.shape))
    modelled = np.zeros_like(bld)
    for k in SECT:
        bb = SECT[k]["model_box_m"]
        modelled |= (X > bb[0] - BUFFER) & (X < bb[2] + BUFFER) & (Y > bb[1] - BUFFER) & (Y < bb[3] + BUFFER)
    sea = Q[:, 2].reshape(X.shape) < 2.6
    return dict(xs=xs, ys=ys, S=S, Vx=Vx, Vy=Vy, bld=bld, modelled=modelled, sea=sea, sc=sc, uref=uref,
                solver=json.loads(next((ROOT / "results/cfd/post" / case.name).glob("meta_*.json")).read_text())["solver"],
                ncells=len(C), art=int(art.sum()))


def blobs(mask, F, n, min_px):
    """Largest connected regions of a mask inside the two sectors: (x, y, area m2) of their centroids."""
    inside = np.zeros_like(mask)
    for k in KEYS:
        b = SECT[k]["model_box_m"]
        inside[np.ix_((F["ys"] > b[1]) & (F["ys"] < b[3]), (F["xs"] > b[0]) & (F["xs"] < b[2]))] = True
    lab, m = ndimage.label(mask & inside)
    if m == 0:
        return [], lab, []
    sz = ndimage.sum(np.ones_like(lab), lab, range(1, m + 1))
    order = [j + 1 for j in np.argsort(sz)[::-1] if sz[j] >= min_px][:n]
    cen = ndimage.center_of_mass(np.ones_like(lab), lab, order)
    out = [(F["xs"][0] + c[1] * STEP, F["ys"][0] + c[0] * STEP, sz[j - 1] * STEP ** 2) for c, j in zip(cen, order)]
    return out, lab, order


def main():
    case, out = Path(sys.argv[1]), Path(sys.argv[2]); dur = float(sys.argv[3]) if len(sys.argv) > 3 else 15.0
    F = field(case); xs, ys, S = F["xs"], F["ys"], F["S"]; uref = F["uref"]; sc = F["sc"]
    d = sc["dir_from_deg"]; flow = np.array([-math.sin(math.radians(d)), -math.cos(math.radians(d))])
    # zones detected in the field
    stag, lab_s, ord_s = blobs(np.nan_to_num(S, nan=9) < 0.6, F, 3, 400)
    acc, lab_a, ord_a = blobs(np.nan_to_num(S) > 1.2 * uref, F, 3, 60)
    along = F["Vx"] * flow[0] + F["Vy"] * flow[1]
    rev, lab_r, ord_r = blobs((along < -0.3) & ~np.isnan(S), F, 2, 300)
    # particles
    rng = np.random.default_rng(3)
    vx = RegularGridInterpolator((ys, xs), F["Vx"], bounds_error=False, fill_value=0)
    vy = RegularGridInterpolator((ys, xs), F["Vy"], bounds_error=False, fill_value=0)
    fl = np.argwhere(~np.isnan(S))
    N = 5500
    def spawn(n):
        j = fl[rng.integers(0, len(fl), n)]
        return np.c_[xs[j[:, 1]] + rng.uniform(-2.5, 2.5, n), ys[j[:, 0]] + rng.uniform(-2.5, 2.5, n)]
    P = spawn(N); age = rng.uniform(0, 3, N); life = rng.uniform(2.0, 4.0, N)
    dt = 1.0 / FPS
    # figure
    W, H = 1920, 1080
    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100); fig.patch.set_facecolor("#101317")
    ax = fig.add_axes([0.045, 0.13, 0.86, 0.79]); ax.set_facecolor("#101317")
    cax = fig.add_axes([0.925, 0.2, 0.012, 0.65])
    vmax = round(1.6 * uref)
    im = ax.imshow(S, origin="lower", extent=(xs[0], xs[-1] + STEP, ys[0], ys[-1] + STEP), cmap="jet", vmin=0, vmax=vmax,
                   interpolation="bilinear")
    rgba = np.zeros(S.shape + (4,)); rgba[F["bld"]] = (0.42, 0.42, 0.44, 1.0)
    ax.imshow(rgba, origin="lower", extent=im.get_extent(), interpolation="nearest")
    dim = np.zeros(S.shape + (4,)); dim[~F["modelled"] & ~F["sea"]] = (0.06, 0.07, 0.09, 0.72)   # land without modelled buildings
    ax.imshow(dim, origin="lower", extent=im.get_extent(), interpolation="nearest")
    cb = fig.colorbar(im, cax=cax); cb.set_label("Vitesse du vent à 2 m (m/s)", color="w", fontsize=13); cb.ax.tick_params(colors="w", labelsize=11)
    ax.set_xlim(xs[0], xs[-1]); ax.set_ylim(ys[0], ys[-1]); ax.set_aspect("equal")
    ax.tick_params(colors="#b0bec5", labelsize=10)
    ax.set_xlabel("x (m) — Est", color="#b0bec5", fontsize=11); ax.set_ylabel("y (m) — Nord", color="#b0bec5", fontsize=11)
    fig.text(0.045, 0.955, f"Centre-ville et Gambetta (Oran)  —  vent calculé à 2 m du sol  —  scénario {sc['id']}",
             color="w", fontsize=21, weight="bold")
    fig.text(0.045, 0.928, f"{F['solver']} · {F['ncells']:,} mailles · vent du {DIRS[round(d / 22.5) % 16]} ({d}°), {uref} m/s à 10 m (Es Sénia)"
             f" · traînées blanches : vent calculé, mouvement accéléré ×{SPEEDUP:.0f}".replace(",", " "), color="#cfd8dc", fontsize=12.5)
    placed = []                                      # label anchors already used (no overlaps)
    for k in KEYS:
        b = SECT[k]["model_box_m"]
        ax.text(b[0] + 40, b[1] + 40, NAMES[k], color="w", fontsize=16, weight="bold", ha="left", va="bottom",
                bbox=dict(boxstyle="round,pad=0.3", fc=(0, 0, 0, 0.6), ec="none"))
        placed.append((b[0] + 200, b[1] + 80))
    gx = (SECT["CENTRE_VILLE"]["model_box_m"][2] + SECT["GAMBETTA"]["model_box_m"][0]) / 2
    ax.text(gx, ys[0] + 300, "bâti hors\nzone modélisée", color="#90a4ae", fontsize=10, ha="center", style="italic")
    # wind arrow
    ax0 = xs[0] + 200; ay0 = ys[-1] - 330
    ax.annotate("", xy=(ax0 + 170 * flow[0], ay0 + 170 * flow[1]), xytext=(ax0 - 170 * flow[0], ay0 - 170 * flow[1]),
                arrowprops=dict(arrowstyle="-|>,head_width=0.6,head_length=1.0", color="w", lw=3.5))
    ax.text(ax0 + 120, ay0 + 60, f"VENT DU {DIRS[round(d / 22.5) % 16]}\n{uref} m/s à 10 m".replace(".", ","), color="w",
            fontsize=14, weight="bold", ha="left", va="center", bbox=dict(boxstyle="round,pad=0.3", fc=(0, 0, 0, 0.5), ec="none"))
    placed.append((ax0 + 250, ay0 + 60))
    lc = LineCollection([], colors="w", linewidths=0.9, alpha=0.75); ax.add_collection(lc)
    cap = fig.text(0.045, 0.045, "", color="w", fontsize=16, wrap=True)
    # timed annotation layers (artists created hidden)
    def contour_of(lab, ids, color, ls):
        m = np.isin(lab, ids).astype(float)
        return ax.contour(xs + STEP / 2, ys + STEP / 2, m, levels=[0.5], colors=[color], linewidths=1.8, linestyles=ls)
    def label(x, y, txt, fc):
        if any(math.hypot(x - a, y - b) < 260 for a, b in placed):
            return None
        placed.append((x, y))
        return ax.text(x, y, txt, color="w", fontsize=11.5, weight="bold", ha="center", va="center",
                       bbox=dict(boxstyle="round,pad=0.3", fc=fc, ec="none", alpha=0.85))
    layers = []
    if ord_s:
        layers.append((dur * 0.22, [contour_of(lab_s, ord_s, "#00e5ff", "--")] + [label(x, y, "Zone de stagnation\n(U < 0,6 m/s)", "#00363d") for x, y, _ in stag],
                       "En cyan : zones de stagnation. Dans le tissu dense, l'air ne circule presque plus au niveau du piéton."))
    if ord_a:
        layers.append((dur * 0.45, [contour_of(lab_a, ord_a, "#ff2bd6", "-")] + [label(x, y, f"Accélération\n(U > {1.2 * uref:.1f} m/s)".replace(".", ","), "#4a0040") for x, y, _ in acc],
                       "En magenta : accélérations. Le vent de mer franchit le rebord de l'escarpement et s'accélère au sommet."))
    if ord_r:
        layers.append((dur * 0.68, [contour_of(lab_r, ord_r, "#ffd54f", ":")] + [label(x, y, "Recirculation\n(flux inversé)", "#4a3b00") for x, y, _ in rev],
                       "En jaune : recirculations. Derrière les grands îlots, l'air tourne sur place et revient contre le vent."))
    layers = [(t0, [a for a in arts if a is not None], txt) for t0, arts, txt in layers]
    for _, arts, _ in layers:
        for a in arts:
            for c in (a.collections if hasattr(a, "collections") else [a]):
                c.set_visible(False)
    captions = [(0.0, f"Vent du nord-nord-ouest (brise de mer d'été). Vitesse calculée à 2 m du sol sur l'ensemble centre-ville + Gambetta.")] + \
               [(t0, txt) for t0, _, txt in layers] + \
               [(dur * 0.86, "Gambetta : couloir de vent rapide sur l'escarpement. Centre-ville : ventilation faible, à préserver le long des axes NNO–SSE.")]
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                           "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "18", "-preset", "slow", "-movflags", "+faststart", str(out)],
                          stdin=subprocess.PIPE)
    nfr = int(dur * FPS)
    for f in range(nfr):
        t = f / FPS
        # advect (RK2) with the computed horizontal wind, accelerated for readability
        k1 = np.c_[vx(P[:, ::-1]), vy(P[:, ::-1])]
        k2 = np.c_[vx((P + 0.5 * dt * SPEEDUP * k1)[:, ::-1]), vy((P + 0.5 * dt * SPEEDUP * k1)[:, ::-1])]
        Pn = P + dt * SPEEDUP * k2; age += dt
        dead = (age > life) | (np.hypot(*k2.T) < 0.05) & (rng.random(N) < 0.02) | \
               (Pn[:, 0] < xs[0]) | (Pn[:, 0] > xs[-1]) | (Pn[:, 1] < ys[0]) | (Pn[:, 1] > ys[-1])
        tail = Pn - 5 * dt * SPEEDUP * k2
        lc.set_segments(np.stack([tail, Pn], 1)[~dead])
        P = Pn; P[dead] = spawn(int(dead.sum())); age[dead] = 0
        for t0, arts, _ in layers:
            for a in arts:
                for c in (a.collections if hasattr(a, "collections") else [a]):
                    c.set_visible(t >= t0)
        cap.set_text(next(txt for t0, txt in reversed(captions) if t >= t0))
        fig.canvas.draw()
        ff.stdin.write(np.asarray(fig.canvas.buffer_rgba()).tobytes())
    ff.stdin.close(); ff.wait()
    print(json.dumps({"out": str(out), "frames": nfr, "stagnation_zones_m2": [round(a) for *_, a in stag],
                      "acceleration_zones_m2": [round(a) for *_, a in acc], "recirculation_zones_m2": [round(a) for *_, a in rev],
                      "artefacts_masked": F["art"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
