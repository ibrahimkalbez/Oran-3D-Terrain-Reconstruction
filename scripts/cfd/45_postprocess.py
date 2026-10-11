"""Step 45 - post-processing of one solved OpenFOAM case (same exchange format as Fluent, see R03).

  python 45_postprocess.py <case_dir> <out_dir>

1. sampling (OpenFOAM postProcess, cellPoint interpolation) at the latest time:
   - pedestrian level: z = ground + 2.0 m on a 5 m grid over each sector (points inside buildings
     are outside the fluid mesh and are dropped by OpenFOAM);
   - vertical sections along and across the wind through the centre of each sector (5 m);
   - streamlines seeded upstream of each sector at 10 m and 30 m above ground;
2. exports  champ_pieton_<sid>.csv, lignes_courant_<sid>.csv, coupes_<sid>.csv, meta_<sid>.json
3. indicators per sector: U/Uref statistics, area with U > 1.2 Uref (acceleration), U < 0.5 m/s
   (stagnation / recirculation), reversed-flow fraction, + residual history and mass balance
4. figures (maps, sections, residuals) for the report and the video.
"""
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy.interpolate import RegularGridInterpolator  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GEO = ROOT / "results/cfd/geometrie"
SECT = json.loads((ROOT / "results/cfd/secteurs.json").read_text())
FOAM = ". /usr/share/openfoam/etc/bashrc 2>/dev/null; "


def sh(cmd, case):
    return subprocess.run(["bash", "-c", FOAM + cmd], cwd=case, capture_output=True, text=True)


def latest(case):
    ts = [float(p.name) for p in Path(case).iterdir() if re.fullmatch(r"[0-9.]+", p.name) and p.name != "0"]
    return max(ts) if ts else None


def main():
    case, out = Path(sys.argv[1]), Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
    meta = json.loads((case / "scenario.json").read_text())
    sc = meta["scenario"]; sid = sc["id"] + ("_thermique" if meta["thermal"] else "")
    flow = np.array(meta["flow_to"])
    g = np.load(GEO / "terrain_grid.npz"); zg = RegularGridInterpolator((g["ys"], g["xs"]), g["Z"], bounds_error=False, fill_value=0)
    fields = "(U p T)" if meta["thermal"] else "(U p)"
    sets, ped_pts = [], {}
    for k, s in SECT.items():
        b = s["model_box_m"]
        X, Y = np.meshgrid(np.arange(b[0], b[2], 5.0), np.arange(b[1], b[3], 5.0))
        P = np.c_[X.ravel(), Y.ravel(), zg(np.c_[Y.ravel(), X.ravel()]) + 2.0]
        ped_pts[k] = P
        sets.append(f"ped_{k} {{ type cloud; axis xyz; points ({' '.join(f'({x:.2f} {y:.2f} {z:.2f})' for x, y, z in P)}); }}")
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        L = max(b[2] - b[0], b[3] - b[1]) / 2
        for name, d in (("long", flow[:2]), ("trav", np.array([-flow[1], flow[0]]))):
            t = np.arange(-L, L, 5.0); hz = np.arange(0, 120, 4.0)
            pts = [(cx + ti * d[0], cy + ti * d[1], float(zg([[cy + ti * d[1], cx + ti * d[0]]])[0]) + h) for ti in t for h in hz]
            sets.append(f"coupe_{name}_{k} {{ type cloud; axis xyz; points ({' '.join(f'({x:.2f} {y:.2f} {z:.2f})' for x, y, z in pts)}); }}")
    seeds = []
    for k, s in SECT.items():
        b = s["model_box_m"]; cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2; L = max(b[2] - b[0], b[3] - b[1]) / 2
        perp = np.array([-flow[1], flow[0]])
        for h in (10.0, 30.0):
            for t in np.linspace(-0.9 * L, 0.9 * L, 40):
                x, y = cx - flow[0] * (L + 100) + t * perp[0], cy - flow[1] * (L + 100) + t * perp[1]
                seeds.append((x, y, float(zg([[y, x]])[0]) + h))
    (case / "system/postDict").write_text(f"""FoamFile {{ version 2.0; format ascii; class dictionary; object postDict; }}
functions
{{
  samp {{ type sets; libs ("libsampling.so"); interpolationScheme cellPoint; setFormat raw; fields {fields};
    sets ( {' '.join(sets)} ); }}
  streams {{ type streamLine; libs ("libfieldFunctionObjects.so"); setFormat vtk; U U; trackForward true;
    fields (U); lifeTime 4000; nSubCycle 4; cloud particleTracks;
    seedSampleSet {{ type cloud; axis xyz; points ({' '.join(f'({x:.2f} {y:.2f} {z:.2f})' for x, y, z in seeds)}); }} }}
}}
""")
    r = sh("postProcess -dict system/postDict -latestTime > log.postProcess 2>&1", case)
    t = latest(case)
    sdir = case / "postProcessing/samp" / (f"{t:g}")
    uref = sc["U10_ref_ms"]
    rows_all, ind = [], {}
    for k in SECT:
        f = sdir / f"ped_{k}_{'U_p_T' if meta['thermal'] else 'U_p'}.xy"
        if not f.exists():
            cand = list(sdir.glob(f"ped_{k}*"))
            f = cand[0] if cand else None
        D = np.loadtxt(f) if f else np.zeros((0, 7))
        # raw cloud format: x y z Ux Uy Uz p [T]
        x, y, z, ux, uy, uz, p = D[:, :7].T
        U = np.sqrt(ux ** 2 + uy ** 2 + uz ** 2)
        Tt = D[:, 7] - 273.15 if meta["thermal"] and D.shape[1] > 7 else None
        for i in range(len(x)):
            rows_all.append((k, x[i], y[i], z[i], U[i], ux[i], uy[i], uz[i], p[i], Tt[i] if Tt is not None else np.nan))
        along = (ux * flow[0] + uy * flow[1])
        A = len(U) * 25.0
        ind[k] = {"points_fluide": int(len(U)), "surface_fluide_km2": A / 1e6, "U_moy": float(U.mean()), "U_p90": float(np.percentile(U, 90)),
                  "K_moy_U_sur_Uref": float(U.mean() / uref), "pct_acceleration_U_sup_1.2Uref": float(100 * (U > 1.2 * uref).mean()),
                  "pct_stagnation_U_inf_0.5": float(100 * (U < 0.5).mean()), "pct_flux_inverse": float(100 * (along < 0).mean()),
                  **({"T_air_2m_moy_C": float(np.nanmean(Tt)), "T_air_2m_p90_C": float(np.nanpercentile(Tt, 90))} if Tt is not None else {})}
    hdr = "secteur,x,y,z,U,Ux,Uy,Uz,p" + (",T" if meta["thermal"] else "")
    with open(out / f"champ_pieton_{sid}.csv", "w") as fo:
        fo.write("x,y,z,U,Ux,Uy,Uz,p" + (",T" if meta["thermal"] else "") + ",secteur\n")
        for r_ in rows_all:
            fo.write(",".join(f"{v:.3f}" for v in r_[1:9]) + (f",{r_[9]:.2f}" if meta["thermal"] else "") + f",{r_[0]}\n")
    # sections
    with open(out / f"coupes_{sid}.csv", "w") as fo:
        fo.write("coupe,x,y,z,U,Ux,Uy,Uz,p" + (",T" if meta["thermal"] else "") + "\n")
        for f in sorted(sdir.glob("coupe_*")):
            D = np.loadtxt(f)
            name = f.name.split("_U")[0]
            for row in D:
                fo.write(name + "," + ",".join(f"{v:.3f}" for v in row) + "\n")
    # streamlines (legacy vtk polydata)
    sl = []
    vt = list((case / "postProcessing/streams").rglob("*.vtk"))
    if vt:
        txt = vt[0].read_text().split()
        i = txt.index("POINTS"); n = int(txt[i + 1]); P = np.array(txt[i + 3:i + 3 + 3 * n], float).reshape(-1, 3)
        j = txt.index("LINES"); nl = int(txt[j + 1]); pos = j + 3; L_ = []
        for _ in range(nl):
            m = int(txt[pos]); L_.append(np.array(txt[pos + 1:pos + 1 + m], int)); pos += m + 1
        Uv = None
        if "U" in txt:
            try:
                k = txt.index("U", pos); Uv = np.array(txt[k + 3:k + 3 + 3 * n], float).reshape(-1, 3)
            except Exception:
                Uv = None
        with open(out / f"lignes_courant_{sid}.csv", "w") as fo:
            fo.write("id,x,y,z,U\n")
            for li, idx in enumerate(L_):
                for q in idx[::2]:
                    u = np.linalg.norm(Uv[q]) if Uv is not None else 0.0
                    fo.write(f"{li},{P[q, 0]:.2f},{P[q, 1]:.2f},{P[q, 2]:.2f},{u:.3f}\n")
        sl = L_
    # residuals and mass balance
    res = {}
    for f in (case / "postProcessing/residuals").rglob("*.dat"):
        D = np.genfromtxt(f, comments="#", invalid_raise=False)
        hdr_ = [l for l in f.read_text().splitlines() if l.startswith("#")][-1].split()[1:]
        for ci, h in enumerate(hdr_):
            if h.endswith("_initial") and ci < D.shape[1]:
                res[h.replace("_initial", "")] = D[:, ci].tolist()
        res["iter"] = D[:, 0].tolist()
    mass = {}
    for nm in ("massIn", "massOut"):
        fs = list((case / f"postProcessing/{nm}").rglob("*.dat"))
        if fs:
            D = np.genfromtxt(fs[0], comments="#"); mass[nm] = D[-1, 1]
    imb = abs(mass.get("massIn", 0) + mass.get("massOut", 0)) / max(abs(mass.get("massIn", 1)), 1e-12) if mass else None
    nit = int(res["iter"][-1]) if res.get("iter") else None
    m = {"scenario": sid, "solver": "OpenFOAM v1912 (simpleFoam / buoyantBoussinesqSimpleFoam), RANS realizable k-epsilon",
         "dir_from_deg": sc["dir_from_deg"], "U10_ref_ms": uref, "U_scale_max": round(2.0 * uref, 1), "units": {"U": "m/s", "p": "m2/s2 (kinematic)", "T": "degC"},
         "iterations": nit, "final_residuals": {k: v[-1] for k, v in res.items() if k != "iter"},
         "mass_in": mass.get("massIn"), "mass_out": mass.get("massOut"), "mass_imbalance_rel": imb,
         "indicateurs_secteurs": ind, "streamlines": len(sl), "time": t}
    (out / f"meta_{sid}.json").write_text(json.dumps(m, indent=1, ensure_ascii=False))
    (out / f"residus_{sid}.json").write_text(json.dumps(res))
    figures(out, sid, rows_all, ind, uref, sc, res, meta)
    print(json.dumps({k: m[k] for k in ("iterations", "final_residuals", "mass_imbalance_rel", "indicateurs_secteurs")}, indent=1))


def figures(out, sid, rows, ind, uref, sc, res, meta):
    bld = {}
    for k in SECT:
        txt = (GEO / f"buildings_{k}.stl").read_text().split("vertex")[1:]
        V = np.array([list(map(float, t.split()[:3])) for t in txt]).reshape(-1, 3, 3)
        nz = np.cross(V[:, 1] - V[:, 0], V[:, 2] - V[:, 0])
        bld[k] = V[nz[:, 2] > 0.9 * np.linalg.norm(nz, axis=1)][:, :, :2]
    from matplotlib.collections import PolyCollection
    fig, axs = plt.subplots(1, 3, figsize=(21, 7.5), dpi=120)
    for ax, k in zip(axs, SECT):
        R = np.array([r_[1:5] for r_ in rows if r_[0] == k])
        b = SECT[k]["model_box_m"]
        if len(R):
            sc_ = ax.scatter(R[:, 0], R[:, 1], c=R[:, 3] / uref, s=1.2, marker="s", cmap="jet", vmin=0, vmax=1.6, linewidths=0)
        ax.add_collection(PolyCollection(bld[k], facecolors="0.25", edgecolors="none"))
        ax.set_xlim(b[0], b[2]); ax.set_ylim(b[1], b[3]); ax.set_aspect("equal")
        i = ind[k]
        ax.set_title(f"{k.replace('_', ' ')} — U/Uref moy {i['K_moy_U_sur_Uref']:.2f}, >1,2 : {i['pct_acceleration_U_sup_1.2Uref']:.1f} %, "
                     f"<0,5 m/s : {i['pct_stagnation_U_inf_0.5']:.1f} %", fontsize=9)
        th = math.radians(sc["dir_from_deg"]); cx, cy = b[0] + 120, b[3] - 120
        ax.arrow(cx + 80 * math.sin(th), cy + 80 * math.cos(th), -120 * math.sin(th), -120 * math.cos(th), width=8, color="k")
    fig.colorbar(sc_, ax=axs, shrink=0.8, label=f"U / Uref à 2 m (Uref = U10 Es Sénia = {uref} m/s)")
    fig.suptitle(f"{sid} — vent de {sc['dir_from_deg']:.1f}° — vitesse piétonne (OpenFOAM, RANS)", fontsize=12)
    fig.savefig(out / f"carte_pieton_{sid}.png"); plt.close(fig)
    if res.get("iter"):
        fig, ax = plt.subplots(figsize=(8, 4.5), dpi=120)
        for k, v in res.items():
            if k != "iter":
                ax.semilogy(res["iter"], v, label=k)
        ax.set_xlabel("itération"); ax.set_ylabel("résidu initial"); ax.legend(); ax.grid(True, which="both", lw=0.3)
        ax.set_title(f"Convergence — {sid}"); fig.tight_layout(); fig.savefig(out / f"residus_{sid}.png"); plt.close(fig)


if __name__ == "__main__":
    main()
