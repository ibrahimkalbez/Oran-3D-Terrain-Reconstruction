"""Step 49 - realistic 3D clips of the video (detailed textured Rhino maquette + CFD results).

  python 49_clips_realistes.py <scene_dir> <scenario_post_dir_or_none> <clip_set> <out_frames_root> [fps]

clip_set:
  intro      orbit over each sector, maquette only (topography, black roads, vegetation, furniture)
  wind       per sector: aerial dolly from upwind with the ground wind field + flowing particles,
             then a walk at eye height (1.7 m) along a real street with particles near the ground
Street cameras are snapped on the asphalt of the maquette (materials 0-3) and kept >= 3 m from
the CFD building footprints. Frames are rendered by realiste/render.mjs (10 fps by default,
motion-interpolated to 25 fps at editing time).
"""
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import gaussian_filter1d
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[2]
SECT = json.loads((ROOT / "results/cfd/secteurs.json").read_text())
GEO = ROOT / "results/cfd/geometrie"
STREETS = {"CENTRE_VILLE": ("Boulevard Larbi Ben M'hidi", [(-1060, 250), (-962, 277), (-854, 301), (-659, 346), (-560, 370)]),
           "GAMBETTA": ("Rue en bord d'escarpement, Gambetta", [(700, 1380), (1000, 1480), (1300, 1560), (1600, 1650)]),
           "BD_DES_LIONS": ("Boulevard des Lions", [(5760, 1428), (6100, 1260), (6500, 1050), (6900, 840), (7300, 640)])}
DIRS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]


def zgrid():
    g = np.load(GEO / "terrain_grid.npz")
    return RegularGridInterpolator((g["ys"], g["xs"]), g["Z"], bounds_error=False, fill_value=0.0)


def t3(x, y, z):
    return [float(x), float(z), float(-y)]


def street_path(scene, k, pts, zg, step=4.0):
    asph = []
    for m in (0, 1, 2, 3):
        f = Path(scene) / f"{k}_mat{m}.bin"
        if f.exists():
            a = np.fromfile(f, np.float32).reshape(-1, 3, 5)[:, :, :3].mean(1)
            asph.append(np.c_[a[:, 0], -a[:, 2]])
    A = np.vstack(asph); ta = cKDTree(A)
    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union
    txt = (GEO / f"buildings_{k}.stl").read_text().split("vertex")[1:]
    V = np.array([list(map(float, t.split()[:3])) for t in txt]).reshape(-1, 3, 3)
    nz = np.cross(V[:, 1] - V[:, 0], V[:, 2] - V[:, 0])
    roofs = unary_union([Polygon(t[:, :2]) for t in V[nz[:, 2] > 0.9 * np.linalg.norm(nz, axis=1)] if Polygon(t[:, :2]).area > 1e-3])
    P = np.array(pts, float); seg = []
    for a, b in zip(P[:-1], P[1:]):
        n = max(2, int(np.linalg.norm(b - a) / step))
        seg.append(a + (b - a) * np.linspace(0, 1, n, endpoint=False)[:, None])
    Q = np.vstack(seg + [P[-1:]])
    _, i = ta.query(Q, k=12)
    out = []
    for q, ii in zip(Q, i):
        cand = sorted(A[ii], key=lambda c: np.linalg.norm(c - q))
        for c in cand:
            if roofs.distance(Point(c)) >= 3.0:
                out.append(c); break
    S = gaussian_filter1d(np.array(out), 6, axis=0, mode="nearest")
    return S, np.array([float(zg([[y, x]])[0]) for x, y in S])


def specs_for(set_, scene, post, zg):
    meta = json.loads(next(Path(post).glob("meta_*.json")).read_text()) if post else None
    F = []
    for k, s in SECT.items():
        b = s["model_box_m"]; cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2; cz = float(zg([[cy, cx]])[0])
        name = k.replace("_", " ").title().replace("Bd Des", "Boulevard des")
        if set_ == "intro":
            n = 60
            for j in range(n):
                a = math.radians(200 + 70 * j / (n - 1)); R = 1250; h = 430 - 60 * j / (n - 1)
                F.append({"clip": f"intro_{k}", "sector": k, "cam": t3(cx + R * math.cos(a), cy + R * math.sin(a), cz + h),
                          "tgt": t3(cx, cy, cz), "fov": 50, "uref": 1, "title": name,
                          "subtitle": "Maquette Rhino 8 détaillée : topographie, voiries, trottoirs, végétation, mobilier",
                          "tag": "Rendu du fichier Oran_maquette_urbaine_detaillee_Rhino8.3dm (matériaux et textures d'origine)"})
        elif set_ == "wind":
            u = meta["U10_ref_ms"]; d = meta["dir_from_deg"]; th = math.radians(d); fl = np.array([-math.sin(th), -math.cos(th)])
            sid = meta["scenario"]; ind = meta["indicateurs_secteurs"][k]
            tech = (f"{meta['solver']}\n{meta['mesh_cells']:,} mailles · Uref {u} m/s à 10 m (Es Sénia) · vent du "
                    f"{DIRS[round(d / 22.5) % 16]} ({d}°)\nU/Uref moyen à 2 m : {ind['K_moy_U_sur_Uref']:.2f} · stagnation "
                    f"(U<0,5 m/s) : {ind['pct_stagnation_U_inf_0.5']:.1f} %").replace(",", " ")
            n = 80
            for j in range(n):
                w = j / (n - 1); R = 1500 - 650 * w; h = 650 - 330 * w
                cam = (cx - fl[0] * R + 300 * (w - 0.5) * fl[1], cy - fl[1] * R - 300 * (w - 0.5) * fl[0], cz + h)
                F.append({"clip": f"wind_aerien_{k}", "sector": k, "cam": t3(*cam), "tgt": t3(cx, cy, cz), "fov": 50, "uref": u,
                          "ground": 0.5, "particles": True, "t": j * 0.25, "title": f"{name} — écoulement calculé ({sid})",
                          "subtitle": "Sol coloré : vitesse à 2 m · particules : lignes de courant à 10 et 30 m", "tech": tech,
                          "tag": "Simulation OpenFOAM (RANS) sur la maquette Rhino — valeurs calculées, pas mesurées"})
            nm, pts = STREETS[k]; S, Z = street_path(scene, k, pts, zg)
            n = 80; idx = np.linspace(0, len(S) - 12, n).astype(int)
            for j, i in enumerate(idx):
                F.append({"clip": f"wind_rue_{k}", "sector": k, "cam": t3(S[i, 0], S[i, 1], Z[i] + 1.7),
                          "tgt": t3(S[i + 10, 0], S[i + 10, 1], Z[i + 10] + 1.5), "fov": 68, "uref": u, "ground": 0.35,
                          "particles": True, "lowOnly": True, "t": j * 0.25, "title": f"{nm} — à hauteur de piéton",
                          "subtitle": f"Vent calculé dans la rue ({sid}), œil à 1,7 m", "tech": tech,
                          "tag": "Caméra posée sur la chaussée de la maquette ; sol coloré = U/Uref à 2 m"})
    return F


def main():
    scene, post, set_, root = Path(sys.argv[1]), (None if sys.argv[2] == "none" else Path(sys.argv[2])), sys.argv[3], Path(sys.argv[4])
    zg = zgrid()
    srv_dir = root / f"srv_{set_}_{post.name if post else 'none'}"; srv_dir.mkdir(parents=True, exist_ok=True)
    for f in scene.iterdir():
        if not (srv_dir / f.name).exists():
            (srv_dir / f.name).symlink_to(f)
    for f in ("index.html", "render.mjs"):
        shutil.copy(ROOT / "scripts/cfd/realiste" / f, srv_dir / f)
    if post:
        cfd = srv_dir / "cfd"
        if cfd.exists() or cfd.is_symlink():
            cfd.unlink()
        cfd.symlink_to(post / "vues/web")
    (srv_dir / "config.json").write_text(json.dumps({"sectors": list(SECT)}))
    F = specs_for(set_, scene, post, zg)
    clips = {}
    for f in F:
        clips.setdefault(f["clip"], []).append(f)
    port = 8780 + abs(hash(str(srv_dir))) % 100
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=srv_dir,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for c, fs in clips.items():
            out = root / f"{c}{'_' + post.name if post else ''}"
            if out.exists() and len(list(out.glob('*.png'))) == len(fs):
                continue
            for i, f in enumerate(fs):
                f["name"] = f"{i:04d}"
            (srv_dir / f"frames_{c}.json").write_text(json.dumps(fs))
            r = subprocess.run(["node", "render.mjs", str(port), f"frames_{c}.json", str(out)], cwd=srv_dir, capture_output=True, text=True)
            print(c, len(fs), "frames", r.stdout[-200:], r.stderr[-300:])
    finally:
        srv.terminate()


if __name__ == "__main__":
    main()
