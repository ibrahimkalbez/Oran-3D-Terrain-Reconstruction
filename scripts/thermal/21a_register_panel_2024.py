"""Step 21a - automatic registration of the 10/07/2024 panel on the Rhino model.

No coordinates are typed by hand. The panel is matched to geometry that already
exists in the white maquette (local Transverse Mercator metres):

  * COASTLINE: the outline drawn on the panel where it follows the sea, against
    the 0.5 m contour of the maquette terrain (Copernicus GLO-30, sea = 0 m).
    Each dark outline pixel is matched to the nearest coastline point
    (robust chamfer matching, Huber loss).
  * LAKE: centroid of the lake on the panel (cool blob) against the centroid of
    the matching NATURAL_WA polygon of the maquette.

Initial guess: scale of the panel's scale bar (0-15 km bar measured in pixels),
north up, lake on lake. Then a similarity (scale, rotation, shift) and a full
affine are fitted; the affine is kept only if it improves the coast fit
significantly.

Independent check (not used in the fit): the airport runways (AEROWAY_RU) are
transformed onto the panel and the distance to the warm linear runway pixels
is reported, as well as the port position.

Inputs : results/thermal/area_mask_px.npy, values_px.npy, panel_2024.png,
         white maquette .3dm (argument)
Output : results/thermal/registration_2024.json  (used by step 21)
Usage  : python scripts/thermal/21a_register_panel_2024.py <white maquette .3dm>
"""
import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rhino3dm as r3  # noqa: E402
from PIL import Image  # noqa: E402
from scipy import ndimage  # noqa: E402
from scipy.optimize import least_squares  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

from thermal_common import DATA, WORK, load_config  # noqa: E402


def mesh_tris(g):
    V = np.array([[p.X, p.Y] for p in g.Vertices])
    F = np.array([tuple(g.Faces[k]) for k in range(g.Faces.Count)]).reshape(-1, 4)
    q = F[:, 2] != F[:, 3]
    return V, np.r_[F[:, [0, 1, 2]], F[q][:, [0, 2, 3]]]


def model_features(path):
    m = r3.File3dm.Read(str(path))
    f = {}
    for o in m.Objects:
        n = m.Layers[o.Attributes.LayerIndex].Name
        g = o.Geometry
        if n == "TERRAIN_ET_SOCLE":
            V = np.array([[p.X, p.Y, p.Z] for p in g.Vertices])
            top = V[V[:, 2] > V[:, 2].min() + 1e-6]
            xs, ys = np.unique(top[:, 0]), np.unique(top[:, 1])
            Z = np.zeros((len(ys), len(xs)))
            Z[np.searchsorted(ys, top[:, 1]), np.searchsorted(xs, top[:, 0])] = top[:, 2]
            f["grid"] = (xs, ys, Z)
        elif n in ("NATURAL_WA", "AEROWAY_RU", "MAN_MADE_PIER", "MAN_MADE_BREAKWATER"):
            f[n] = mesh_tris(g)
    return f


def coastline(xs, ys, Z, level=0.5):
    fig = plt.figure()
    cs = fig.add_subplot().contour(xs, ys, Z, levels=[level])
    plt.close(fig)
    segs = [s for s in cs.allsegs[0] if len(s) > 20]
    pts = []
    for s in segs:                                   # densify to <= 5 m spacing
        d = np.linalg.norm(np.diff(s, axis=0), axis=1)
        for a, b, L in zip(s[:-1], s[1:], d):
            k = max(1, int(np.ceil(L / 5)))
            pts.append(a + (b - a) * np.arange(k)[:, None] / k)
    return np.vstack(pts)


def tri_area_centroid(V, T, box):
    P = V[T]; c = P.mean(1)
    sel = (c[:, 0] > box[0]) & (c[:, 0] < box[2]) & (c[:, 1] > box[1]) & (c[:, 1] < box[3])
    u, w = P[sel, 1] - P[sel, 0], P[sel, 2] - P[sel, 0]
    A = 0.5 * np.abs(u[:, 0] * w[:, 1] - u[:, 1] * w[:, 0])
    return (c[sel] * A[:, None]).sum(0) / A.sum(), A.sum()


def apply(p, px, kind):
    if kind == "similarity":
        s, th, tx, ty = p
        c, si = s * np.cos(th), s * np.sin(th)
        M = np.array([[c, si], [si, -c]])            # row axis points south
        return px @ M.T + [tx, ty]
    a = p.reshape(2, 3)
    return px @ a[:, 1:].T + a[:, 0]


def to_affine(p, kind):
    if kind == "affine":
        return p.reshape(2, 3)
    s, th, tx, ty = p
    c, si = s * np.cos(th), s * np.sin(th)
    return np.array([[tx, c, si], [ty, si, -c]])


def main():
    cfg = load_config()
    reg = cfg["registration"]
    f = model_features(sys.argv[1])
    xs, ys, Z = f["grid"]
    coast = coastline(xs, ys, Z)
    tree = cKDTree(coast)
    # ---- panel features (pixel centres)
    panel = np.asarray(Image.open(WORK / "panel_2024.png").convert("RGB"), float)
    area = np.load(WORK / "area_mask_px.npy")
    val = np.load(WORK / "values_px.npy")
    dark = panel.mean(2) < reg.get("outline_gray_max", 150)
    near = ndimage.binary_dilation(area, iterations=3) & ~ndimage.binary_erosion(area, iterations=1)
    outline = dark & near
    r, c = np.nonzero(outline)
    opx = np.c_[c + 0.5, r + 0.5]
    lake = np.isfinite(val) & (val < reg["lake_max_value"])
    lab, n = ndimage.label(lake)
    seed = reg["lake_seed_px"]
    lake = lab == lab[seed[1], seed[0]]
    lr, lc = np.nonzero(lake)
    lake_px = np.array([lc.mean() + 0.5, lr.mean() + 0.5])
    lake_xy, lake_area = tri_area_centroid(*f["NATURAL_WA"], reg["model_lake_box"])
    # ---- initial guess: scale bar, north up, lake on lake
    s0 = reg["scalebar_km"] * 1000.0 / (reg["scalebar_px"][1] - reg["scalebar_px"][0])
    p0 = np.array([s0, 0.0, lake_xy[0] - s0 * lake_px[0], lake_xy[1] + s0 * lake_px[1]])
    # coast pixels = outline pixels within 600 m of the real coastline after the initial guess
    d0, _ = tree.query(apply(p0, opx, "similarity"))
    cmask = d0 < reg.get("coast_select_m", 600)
    cpx = opx[cmask]
    w_lake = np.sqrt(reg.get("lake_weight_points", 200))

    def resid(p, kind):
        P = apply(p, cpx, kind)
        d, i = tree.query(P)
        rc = (P - coast[i])                                   # vector residuals
        rl = (apply(p, lake_px[None], kind)[0] - lake_xy) * w_lake
        return np.r_[rc.ravel(), rl]

    out = {}
    sol = least_squares(resid, p0, args=("similarity",), loss="huber", f_scale=60.0)
    for _ in range(5):                                       # ICP style re-association
        sol = least_squares(resid, sol.x, args=("similarity",), loss="huber", f_scale=60.0)
    ps = sol.x
    A_sim = to_affine(ps, "similarity")
    a0 = A_sim.ravel()
    sol_a = least_squares(resid, a0, args=("affine",), loss="huber", f_scale=60.0)
    for _ in range(5):
        sol_a = least_squares(resid, sol_a.x, args=("affine",), loss="huber", f_scale=60.0)

    # variant: rotation fixed to the UTM 30N meridian convergence (Landsat products for
    # Oran are delivered in UTM 30N) -> only scale and shift are fitted
    fixed = None
    if reg.get("utm_rotation_deg") is not None:
        th = np.radians(reg["utm_rotation_deg"])

        def resid_fixed(q):
            return resid(np.r_[q[0], th, q[1:]], "similarity")
        sf = least_squares(resid_fixed, np.r_[ps[0], ps[2:]], loss="huber", f_scale=60.0)
        for _ in range(5):
            sf = least_squares(resid_fixed, sf.x, loss="huber", f_scale=60.0)
        fixed = np.r_[sf.x[0], th, sf.x[1:]]

    def stats(p, kind):
        d, _ = tree.query(apply(p, cpx, kind))
        lk = float(np.linalg.norm(apply(p, lake_px[None], kind)[0] - lake_xy))
        return {"coast_points": int(len(d)), "coast_median_m": float(np.median(d)),
                "coast_rmse_m": float(np.sqrt((d ** 2).mean())), "coast_p90_m": float(np.percentile(d, 90)),
                "lake_centroid_error_m": lk}
    st_s, st_a = stats(ps, "similarity"), stats(sol_a.x, "affine")
    use_aff = st_a["coast_rmse_m"] < 0.85 * st_s["coast_rmse_m"]
    A = to_affine(sol_a.x, "affine") if use_aff else A_sim
    kind = "affine" if use_aff else "similarity"
    st_f = stats(fixed, "similarity") if fixed is not None else None
    if st_f and not use_aff and st_f["coast_median_m"] <= st_s["coast_median_m"] + 10 \
            and st_f["lake_centroid_error_m"] <= st_s["lake_centroid_error_m"] + 50:
        ps, A, kind = fixed, to_affine(fixed, "similarity"), "similarity_utm_rotation"
        st_s_chosen = st_f
    else:
        st_s_chosen = st_a if use_aff else st_s
    J = A[:, 1:]
    sx, sy = np.linalg.norm(J[:, 0]), np.linalg.norm(J[:, 1])
    rot = np.degrees(np.arctan2(J[1, 0], J[0, 0]))
    # ---- independent check: runways and port
    Minv = np.linalg.inv(J)
    to_px = lambda P: (P - A[:, 0]) @ Minv.T                 # noqa: E731
    Vr, Tr = f["AEROWAY_RU"]
    rw = to_px(Vr[Tr].mean(1))                                # runway triangle centroids -> panel px
    warm = np.isfinite(val) & (val >= reg.get("runway_min_value", 37.0))
    wr, wc = np.nonzero(warm)
    wtree = cKDTree(np.c_[wc + 0.5, wr + 0.5])
    inside = (rw[:, 0] > 0) & (rw[:, 0] < val.shape[1]) & (rw[:, 1] > 0) & (rw[:, 1] < val.shape[0])
    drw, _ = wtree.query(rw[inside])
    out.update({
        "transform_kind": kind,
        "px_to_model": A.tolist(),
        "pixel_convention": "panel pixel centre = (col + 0.5, row + 0.5)",
        "scale_m_per_px": [float(sx), float(sy)], "rotation_deg": float(rot),
        "scalebar_m_per_px": float(s0),
        "fit_similarity": {"params": ps.tolist(), **st_s},
        "fit_affine": st_a,
        "fit_similarity_utm_rotation": ({"params": fixed.tolist(), **st_f} if st_f else None),
        "chosen_fit": st_s_chosen,
        "lake": {"panel_px": lake_px.tolist(), "model_xy": lake_xy.tolist(),
                 "panel_area_km2": float(lake.sum() * sx * sy / 1e6), "model_area_km2": float(lake_area / 1e6)},
        "check_runways_to_warm_pixels_m": {"n": int(len(drw)), "median_m": float(np.median(drw) * sx) if len(drw) else None,
                                           "p90_m": float(np.percentile(drw, 90) * sx) if len(drw) else None},
    })
    # sensitivity: displacement between the chosen fit and the alternative similarity fits
    h, w = val.shape
    grid = np.c_[np.meshgrid(np.arange(0, w, 10) + .5, np.arange(0, h, 10) + .5)[0].ravel(),
                 np.meshgrid(np.arange(0, w, 10) + .5, np.arange(0, h, 10) + .5)[1].ravel()]
    grid = grid[area[grid[:, 1].astype(int), grid[:, 0].astype(int)]]
    base = grid @ A[:, 1:].T + A[:, 0]
    alt = {"similarity_libre": sol.x, "similarity_rotation_utm": fixed}
    out["sensitivity_m"] = {k: {"max": float(np.linalg.norm(apply(p, grid, "similarity") - base, axis=1).max()),
                                "mean": float(np.linalg.norm(apply(p, grid, "similarity") - base, axis=1).mean())}
                            for k, p in alt.items() if p is not None}
    P = apply(sol.x if kind == "similarity" else ps, cpx, "similarity") if kind != "affine" else apply(sol_a.x, cpx, "affine")
    d, i = tree.query(P)
    np.savez(WORK / "control_points_2024.npz", px=cpx, model_xy=P, matched_coast_xy=coast[i], residual_m=d,
             lake_px=lake_px, lake_model_xy=lake_xy)
    (WORK / "registration_2024.json").write_text(json.dumps(out, indent=1))
    # ---- overlay figure
    fig, ax = plt.subplots(figsize=(13, 10.5), dpi=120)
    ax.imshow(Image.open(WORK / "panel_2024.png"), extent=(0, val.shape[1], val.shape[0], 0))
    cp = to_px(coast); ax.plot(cp[:, 0], cp[:, 1], ",", color="blue", ms=1)
    for k, col in (("NATURAL_WA", "m"), ("AEROWAY_RU", "k"), ("MAN_MADE_PIER", "k"), ("MAN_MADE_BREAKWATER", "k")):
        V, T = f[k]
        for t in to_px(V[T].reshape(-1, 2)).reshape(-1, 3, 2):
            ax.fill(t[:, 0], t[:, 1], color=col, lw=0, alpha=0.6)
    ax.scatter(cpx[:, 0], cpx[:, 1], s=0.3, c="cyan")
    ax.set_xlim(0, val.shape[1]); ax.set_ylim(val.shape[0], 0)
    ax.set_title(f"Calage {kind}: côte RMSE {out['chosen_fit']['coast_rmse_m']:.0f} m, "
                 f"médiane {out['chosen_fit']['coast_median_m']:.0f} m — bleu: côte maquette, noir: port et pistes, magenta: eau")
    fig.tight_layout(); fig.savefig(WORK / "controle_calage_2024.png"); plt.close(fig)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
