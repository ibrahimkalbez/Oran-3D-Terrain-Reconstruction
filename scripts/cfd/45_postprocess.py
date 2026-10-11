"""Step 45 - post-processing of one solved OpenFOAM case WITHOUT OpenFOAM function objects
(they abort in this v1912 Debian build). Everything is read from the written ascii fields.

  python 45_postprocess.py <case_dir> <out_dir>

* fields U, p (T) at the latest time + cell centres (mean of the cell's face centres)
* interpolation: inverse-distance weighting of the 8 nearest cell centres (fluid cells only);
  points inside building footprints or below the snapped ground are discarded
* pedestrian level z = ground + 2 m on a 5 m grid over each sector; vertical sections along /
  across the wind through each sector centre; streamlines (RK2, 4 m steps) seeded upstream of
  each sector at 10 m and 30 m above ground
* residuals parsed from log.solver; mass balance from the boundary face fluxes phi
* exchange files for Rhino (R03): champ_pieton_<sid>.csv, lignes_courant_<sid>.csv,
  coupes_<sid>.csv, meta_<sid>.json ; figures for the report and the video
"""
import json
import math
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.collections import PolyCollection  # noqa: E402
from scipy.interpolate import RegularGridInterpolator  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GEO = ROOT / "results/cfd/geometrie"
SECT = json.loads((ROOT / "results/cfd/secteurs.json").read_text())


def body(path):
    t = Path(path).read_text(errors="ignore")
    h = t.index("}") if "FoamFile" in t else 0
    return t, h


def read_list(path, start_token="internalField"):
    t, h = body(path)
    i = t.index(start_token, h)
    j = t.index("(", t.index("List<", i))
    n = int(t[t.index(">", i) + 1:j].split()[-1])
    k = t.index("\n)", j)
    arr = np.fromstring(t[j + 1:k].replace("(", " ").replace(")", " "), sep=" ")
    return arr.reshape(n, -1) if arr.size != n else arr


def patch_values(path, patch):
    t, h = body(path)
    i = t.index(patch, t.index("boundaryField"))
    blk = t[i:t.index("}", i)]
    if "nonuniform" not in blk:
        m = re.search(r"value\s+uniform\s+([-\d.eE+]+)", blk)
        return np.array([float(m.group(1))]) if m else np.array([0.0])
    j = blk.index("(", blk.index("List<"))
    return np.fromstring(blk[j + 1:blk.rindex(")")].replace("(", " ").replace(")", " "), sep=" ")


def mesh_centres(case):
    pm = Path(case) / "constant/polyMesh"
    P = np.fromstring(body(pm / "points")[0][body(pm / "points")[1]:].split("(", 1)[1].rsplit(")", 1)[0]
                      .replace("(", " ").replace(")", " "), sep=" ").reshape(-1, 3)
    t, h = body(pm / "faces")
    ints = np.fromstring(t[t.index("(", h) + 1:t.rindex(")")].replace("(", " ").replace(")", " "), sep=" ", dtype=np.int64)
    starts, lens = [], []; pos = 0
    while pos < len(ints):
        n = int(ints[pos]); starts.append(pos + 1); lens.append(n); pos += n + 1
    starts = np.array(starts); lens = np.array(lens)
    fc = np.zeros((len(starts), 3))
    for m in np.unique(lens):
        k = lens == m
        fc[k] = P[ints[starts[k][:, None] + np.arange(m)[None, :]]].mean(1)
    def ilist(name):
        t, h = body(pm / name)
        return np.fromstring(t[t.index("(", h) + 1:t.rindex(")")], sep=" ", dtype=np.int64)
    own, nei = ilist("owner"), ilist("neighbour")
    nc = own.max() + 1
    C = np.zeros((nc, 3)); cnt = np.zeros(nc)
    np.add.at(C, own, fc[:len(own)]); np.add.at(cnt, own, 1)
    np.add.at(C, nei, fc[:len(nei)]); np.add.at(cnt, nei, 1)
    return C / cnt[:, None]


def footprints(tri_dir):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    from shapely.prepared import prep
    polys, raw = [], {}
    for k in SECT:
        txt = (tri_dir / f"buildings_{k}.stl").read_text().split("vertex")[1:]
        V = np.array([list(map(float, t.split()[:3])) for t in txt]).reshape(-1, 3, 3)
        nz = np.cross(V[:, 1] - V[:, 0], V[:, 2] - V[:, 0])
        roof = V[nz[:, 2] > 0.9 * np.linalg.norm(nz, axis=1)][:, :, :2]
        raw[k] = roof
        polys += [Polygon(t) for t in roof if Polygon(t).area > 1e-3]
    return prep(unary_union(polys).buffer(0.5)), raw


def main():
    case, out = Path(sys.argv[1]), Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
    meta = json.loads((case / "scenario.json").read_text())
    sc = meta["scenario"]; th = meta["thermal"]; sid = sc["id"] + ("_thermique" if th else "")
    flow = np.array(meta["flow_to"]); uref = sc["U10_ref_ms"]
    times = sorted(float(p.name) for p in case.iterdir() if re.fullmatch(r"[0-9.]+", p.name) and p.name != "0")
    tdir = case / f"{times[-1]:g}"
    C = mesh_centres(case)
    U = read_list(tdir / "U"); p = read_list(tdir / ("p_rgh" if th and (tdir / "p_rgh").exists() else "p"))
    T = read_list(tdir / "T") - 273.15 if th else None
    tree = cKDTree(C)
    g = np.load(GEO / "terrain_grid.npz"); zg = RegularGridInterpolator((g["ys"], g["xs"]), g["Z"], bounds_error=False, fill_value=0)
    tri_dir = case / "constant/triSurface"          # the buildings actually meshed in THIS case
    inside, roofs = footprints(tri_dir)
    from shapely.geometry import Point

    def interp(Q, keep_mask=None):
        d, i = tree.query(Q, k=8)
        w = 1.0 / np.maximum(d, 0.1) ** 2; w /= w.sum(1, keepdims=True)
        out_ = {"U": (U[i] * w[..., None]).sum(1), "p": (p[i] * w).sum(1), "dmin": d[:, 0]}
        if T is not None:
            out_["T"] = (T[i] * w).sum(1)
        return out_

    def fluid(Q):
        ok = np.array([not inside.contains(Point(x, y)) for x, y in Q[:, :2]])
        return ok

    rows, ind = [], {}
    for k, s in SECT.items():
        b = s["model_box_m"]
        X, Y = np.meshgrid(np.arange(b[0], b[2], 5.0), np.arange(b[1], b[3], 5.0))
        Q = np.c_[X.ravel(), Y.ravel(), zg(np.c_[Y.ravel(), X.ravel()]) + 2.0]
        Q = Q[fluid(Q)]
        r = interp(Q)
        Um = np.linalg.norm(r["U"], axis=1)
        along = r["U"][:, :2] @ flow[:2]
        for j in range(len(Q)):
            rows.append([k, *Q[j], Um[j], *r["U"][j], r["p"][j], (r["T"][j] if T is not None else np.nan)])
        ind[k] = {"points_fluide_5m": int(len(Q)), "surface_fluide_km2": len(Q) * 25 / 1e6, "U_moy": float(Um.mean()),
                  "U_p90": float(np.percentile(Um, 90)), "U_max": float(Um.max()), "K_moy_U_sur_Uref": float(Um.mean() / uref),
                  "pct_acceleration_U_sup_1.2Uref": float(100 * (Um > 1.2 * uref).mean()),
                  "pct_stagnation_U_inf_0.5": float(100 * (Um < 0.5).mean()),
                  "pct_flux_inverse": float(100 * (along < 0).mean())}
        if T is not None:
            ind[k].update({"T_air_2m_moy_C": float(r["T"].mean()), "T_air_2m_p90_C": float(np.percentile(r["T"], 90)),
                           "T_air_2m_max_C": float(r["T"].max())})
    R = np.array([r_[1:] for r_ in rows], float)
    with open(out / f"champ_pieton_{sid}.csv", "w") as f:
        f.write("x,y,z,U,Ux,Uy,Uz,p" + (",T" if th else "") + ",secteur\n")
        for r_ in rows:
            f.write(",".join(f"{v:.3f}" for v in r_[1:9]) + (f",{r_[9]:.2f}" if th else "") + f",{r_[0]}\n")
    # vertical sections
    sec_rows = []
    for k, s in SECT.items():
        b = s["model_box_m"]; cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2; L = max(b[2] - b[0], b[3] - b[1]) / 2
        for name, d in (("long", flow[:2]), ("trav", np.array([-flow[1], flow[0]]))):
            tt = np.arange(-L, L, 5.0); hz = np.arange(1.0, 150, 3.0)
            TT, HH = np.meshgrid(tt, hz)
            x = cx + TT.ravel() * d[0]; y = cy + TT.ravel() * d[1]; z = zg(np.c_[y, x]) + HH.ravel()
            Q = np.c_[x, y, z]; ok = fluid(Q) | (HH.ravel() > 130)
            r = interp(Q)
            for j in np.flatnonzero(ok):
                sec_rows.append((f"{name}_{k}", TT.ravel()[j], HH.ravel()[j], *Q[j], np.linalg.norm(r["U"][j]), *r["U"][j],
                                 r["p"][j], r["T"][j] if T is not None else np.nan))
    with open(out / f"coupes_{sid}.csv", "w") as f:
        f.write("coupe,s,h,x,y,z,U,Ux,Uy,Uz,p,T\n")
        for r_ in sec_rows:
            f.write(r_[0] + "," + ",".join(f"{v:.3f}" for v in r_[1:]) + "\n")
    # streamlines (RK2)
    lines = []
    for k, s in SECT.items():
        b = s["model_box_m"]; cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2; L = max(b[2] - b[0], b[3] - b[1]) / 2
        perp = np.array([-flow[1], flow[0]])
        for hgt in (10.0, 30.0):
            for t in np.linspace(-0.85 * L, 0.85 * L, 24):
                x0, y0 = cx - flow[0] * (L + 150) + t * perp[0], cy - flow[1] * (L + 150) + t * perp[1]
                P0 = np.array([x0, y0, float(zg([[y0, x0]])[0]) + hgt]); pts = [P0]
                for _ in range(900):
                    u0 = interp(pts[-1][None])["U"][0]; n0 = np.linalg.norm(u0)
                    if n0 < 0.05:
                        break
                    mid = pts[-1] + 2.0 * u0 / n0
                    u1 = interp(mid[None])["U"][0]; n1 = np.linalg.norm(u1)
                    if n1 < 0.05:
                        break
                    nxt = pts[-1] + 4.0 * u1 / n1
                    if nxt[2] < float(zg([[nxt[1], nxt[0]]])[0]) or not (b[0] - 600 < nxt[0] < b[2] + 600 and b[1] - 600 < nxt[1] < b[3] + 600):
                        break
                    pts.append(nxt)
                if len(pts) > 5:
                    P_ = np.array(pts); lines.append((k, P_, np.linalg.norm(interp(P_)["U"], axis=1)))
    with open(out / f"lignes_courant_{sid}.csv", "w") as f:
        f.write("id,secteur,x,y,z,U\n")
        for li, (k, P_, u) in enumerate(lines):
            for q in range(len(P_)):
                f.write(f"{li},{k},{P_[q, 0]:.2f},{P_[q, 1]:.2f},{P_[q, 2]:.2f},{u[q]:.3f}\n")
    # residuals + mass balance
    res = {"iter": []}
    cur = None
    for line in (case / "log.solver").read_text(errors="ignore").splitlines():
        if line.startswith("Time = "):
            cur = float(line.split("=")[1]); res["iter"].append(cur)
        m = re.search(r"Solving for (\w+), Initial residual = ([-\d.eE+]+)", line)
        if m and cur is not None:
            res.setdefault(m.group(1), {})
            res[m.group(1)].setdefault(cur, float(m.group(2)))
    res = {k: (v if k == "iter" else [v.get(i, np.nan) for i in res["iter"]]) for k, v in res.items()}
    roles = meta["roles"]; mass = {"in": 0.0, "out": 0.0}
    for pt, rl in list(roles.items()) + [("top", "top")]:
        try:
            ph = patch_values(tdir / "phi", pt).sum()
        except Exception:
            ph = 0.0
        if rl == "inlet":
            mass["in"] += ph
        elif rl in ("outlet", "top"):
            mass["out"] += ph
    imb = abs(mass["in"] + mass["out"]) / max(abs(mass["in"]), 1e-12)
    m = {"scenario": sid, "solver": "OpenFOAM v1912 " + ("buoyantBoussinesqSimpleFoam" if th else "simpleFoam") + ", RANS realizable k-epsilon",
         "mesh_cells": int(len(C)), "geo_dir": str(tri_dir), "bati": meta.get("bati", "?"), "dir_from_deg": sc["dir_from_deg"], "U10_ref_ms": uref, "U_scale_max": round(2.0 * uref, 1),
         "units": {"U": "m/s", "p": "m2/s2 (pression cinématique)", "T": "degC"}, "iterations": int(times[-1]),
         "final_residuals": {k: v[-1] for k, v in res.items() if k != "iter"}, "mass_flux_in_m3s": mass["in"],
         "mass_flux_out_m3s": mass["out"], "mass_imbalance_rel": imb, "indicateurs_secteurs": ind, "streamlines": len(lines)}
    (out / f"meta_{sid}.json").write_text(json.dumps(m, indent=1, ensure_ascii=False))
    (out / f"residus_{sid}.json").write_text(json.dumps(res))
    # ---- figures
    fig, axs = plt.subplots(1, 3, figsize=(21, 7.6), dpi=120)
    for ax, k in zip(axs, SECT):
        b = SECT[k]["model_box_m"]; sel = np.array([r_[0] == k for r_ in rows])
        im = ax.scatter(R[sel, 0], R[sel, 1], c=R[sel, 3] / uref, s=1.3, marker="s", cmap="jet", vmin=0, vmax=1.6, linewidths=0)
        ax.add_collection(PolyCollection(roofs[k], facecolors="0.2", edgecolors="none"))
        for kk, P_, u in lines:
            if kk == k:
                ax.plot(P_[:, 0], P_[:, 1], "w-", lw=0.4, alpha=0.7)
        ax.set_xlim(b[0], b[2]); ax.set_ylim(b[1], b[3]); ax.set_aspect("equal")
        i = ind[k]
        ax.set_title(f"{k.replace('_', ' ')}\nU/Uref moy {i['K_moy_U_sur_Uref']:.2f} · >1,2 Uref : {i['pct_acceleration_U_sup_1.2Uref']:.1f} % · "
                     f"<0,5 m/s : {i['pct_stagnation_U_inf_0.5']:.1f} %", fontsize=9)
        a = math.radians(sc["dir_from_deg"]); x0, y0 = b[0] + 150, b[3] - 150
        ax.arrow(x0 + 70 * math.sin(a), y0 + 70 * math.cos(a), -110 * math.sin(a), -110 * math.cos(a), width=10, color="k")
    fig.colorbar(im, ax=axs, shrink=0.8, label=f"U/Uref à 2 m du sol (Uref = U10 Es Sénia = {uref} m/s)")
    fig.suptitle(f"{sid} — vent venant du {sc['dir_from_deg']:.1f}° — OpenFOAM RANS, {len(C):,} mailles".replace(",", " "), fontsize=12)
    fig.savefig(out / f"carte_pieton_{sid}.png"); plt.close(fig)
    if T is not None:
        fig, axs = plt.subplots(1, 3, figsize=(21, 7.6), dpi=120)
        for ax, k in zip(axs, SECT):
            b = SECT[k]["model_box_m"]; sel = np.array([r_[0] == k for r_ in rows])
            im = ax.scatter(R[sel, 0], R[sel, 1], c=R[sel, 8], s=1.3, marker="s", cmap="inferno", linewidths=0,
                            vmin=np.percentile(R[:, 8], 2), vmax=np.percentile(R[:, 8], 98))
            ax.add_collection(PolyCollection(roofs[k], facecolors="0.6", edgecolors="none"))
            ax.set_xlim(b[0], b[2]); ax.set_ylim(b[1], b[3]); ax.set_aspect("equal")
            ax.set_title(f"{k.replace('_', ' ')} — T air 2 m moy {ind[k]['T_air_2m_moy_C']:.2f} °C", fontsize=9)
        fig.colorbar(im, ax=axs, shrink=0.8, label="Température de l'AIR calculée à 2 m (°C)")
        fig.suptitle(f"{sid} — température de l'air calculée (Boussinesq), surfaces imposées = LST 10/07/2024", fontsize=12)
        fig.savefig(out / f"carte_Tair_{sid}.png"); plt.close(fig)
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=120)
    for k, v in res.items():
        if k != "iter":
            ax.semilogy(res["iter"], v, label=k, lw=1)
    ax.set_xlabel("itération"); ax.set_ylabel("résidu initial"); ax.grid(True, which="both", lw=0.3); ax.legend(fontsize=8)
    ax.set_title(f"Convergence — {sid} — déséquilibre de masse {100 * imb:.3f} %"); fig.tight_layout()
    fig.savefig(out / f"residus_{sid}.png"); plt.close(fig)
    S = [r_ for r_ in sec_rows if r_[0].startswith("long_")]
    fig, axs = plt.subplots(3, 1, figsize=(14, 9), dpi=120)
    for ax, k in zip(axs, SECT):
        A = np.array([r_[1:] for r_ in S if r_[0] == f"long_{k}"], float)
        im = ax.scatter(A[:, 0], A[:, 1], c=A[:, 5] / uref, s=4, marker="s", cmap="jet", vmin=0, vmax=1.6, linewidths=0)
        ax.set_ylim(0, 150); ax.set_xlim(A[:, 0].min(), A[:, 0].max())
        ax.set_ylabel("h au-dessus du sol (m)"); ax.set_title(f"Coupe verticale dans l'axe du vent — {k.replace('_', ' ')}", fontsize=9)
    axs[-1].set_xlabel("distance le long du vent (m)"); fig.colorbar(im, ax=axs, shrink=0.8, label="U/Uref")
    fig.savefig(out / f"coupes_{sid}.png"); plt.close(fig)
    print(json.dumps({k: m[k] for k in ("mesh_cells", "iterations", "final_residuals", "mass_imbalance_rel", "indicateurs_secteurs")},
                     indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
