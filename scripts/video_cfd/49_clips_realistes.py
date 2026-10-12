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
# street axes: read on the asphalt of the detailed maquette (the OSM centroid axis of Ben M'hidi lies 30-40 m south of it)
STREETS = {"CENTRE_VILLE": ("Boulevard Larbi Ben M'hidi", [(-1030, 291), (-900, 322), (-750, 358), (-600, 394), (-470, 425)]),
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
            a = np.fromfile(f, np.float32).reshape(-1, 3, 5)[:, :, :3]
            asph.append(np.stack([a[:, :, 0], -a[:, :, 2]], -1))
    T = np.concatenate(asph)
    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union
    txt = (GEO / f"buildings_{k}.stl").read_text().split("vertex")[1:]
    V = np.array([list(map(float, t.split()[:3])) for t in txt]).reshape(-1, 3, 3)
    nz = np.cross(V[:, 1] - V[:, 0], V[:, 2] - V[:, 0])
    roofs = unary_union([Polygon(t[:, :2]) for t in V[nz[:, 2] > 0.9 * np.linalg.norm(nz, axis=1)] if Polygon(t[:, :2]).area > 1e-3])
    # route along the maquette roadway: shortest path on a 2 m grid covering the asphalt triangles of the maquette
    # minus a 3 m band around the facades, inside a 60 m corridor around the street axis (the OSM axis itself can
    # cross building footprints); ends = the reachable free points closest to the requested start / end
    import shapely
    from shapely.geometry import LineString
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra, connected_components
    axis = LineString(pts); corr = axis.buffer(60)
    tris = [Polygon(t) for t in T if corr.intersects(Point(t.mean(0)))]
    road = unary_union(tris).buffer(0.6).buffer(-0.5).intersection(corr).difference(roofs.buffer(2.5))   # close gaps between triangles
    x0, y0, x1, y1 = road.bounds
    gx, gy = np.meshgrid(np.arange(x0, x1, 2.0), np.arange(y0, y1, 2.0))
    Q = np.c_[gx.ravel(), gy.ravel()]; Q = Q[shapely.contains_xy(road, Q[:, 0], Q[:, 1])]
    dax = shapely.distance(axis, shapely.points(Q))
    pr = cKDTree(Q).query_pairs(2.9, output_type="ndarray")
    L = np.linalg.norm(Q[pr[:, 0]] - Q[pr[:, 1]], axis=1) * (1 + (dax[pr[:, 0]] + dax[pr[:, 1]]) / 40)
    G = coo_matrix((np.r_[L, L], (np.r_[pr[:, 0], pr[:, 1]], np.r_[pr[:, 1], pr[:, 0]])), shape=(len(Q), len(Q))).tocsr()
    _, lab = connected_components(G, directed=False)
    a0 = int(np.argmin(np.linalg.norm(Q - np.array(pts[0]), axis=1)))
    same = np.flatnonzero(lab == lab[a0])
    a1 = int(same[np.argmin(np.linalg.norm(Q[same] - np.array(pts[-1]), axis=1))])
    free = Q
    _, pred = dijkstra(G, indices=a0, return_predecessors=True)
    route = [a1]
    while route[-1] != a0 and pred[route[-1]] >= 0:
        route.append(pred[route[-1]])
    R = free[route[::-1]]
    print(k, "route", len(R), "pts from", R[0].round(), "to", R[-1].round(), "(asked", pts[0], pts[-1], ")")
    cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(R, axis=0), axis=1))]
    st = np.arange(0, cum[-1], step)
    S = np.c_[np.interp(st, cum, R[:, 0]), np.interp(st, cum, R[:, 1])]
    S = gaussian_filter1d(S, 3, axis=0, mode="nearest")
    S = S[[roofs.distance(Point(p)) >= 2.0 for p in S]]
    return S, np.array([float(zg([[y, x]])[0]) for x, y in S]), roofs


def surface_z(scene, k):
    """Height of the rendered maquette surface (roads, sidewalks, islands, terrain) at (x, y): nearest triangle centroid."""
    P = []
    for f in sorted(Path(scene).glob(f"{k}_mat*.bin")) + [Path(scene) / f"{k}_terrain.bin"]:
        if f.exists():
            a = np.fromfile(f, np.float32).reshape(-1, 3, 5)[:, :, :3].mean(1)
            P.append(np.c_[a[:, 0], -a[:, 2], a[:, 1]])
    P = np.vstack(P); t = cKDTree(P[:, :2])
    return lambda xy: P[t.query(xy)[1], 2]


def street_particles(post, sid, k, S, roofs, zs, uref, dt=0.5, T=40.0):
    """Pedestrian-level particle traces along the street: seeds every 8 m on the camera path with lateral offsets,
    advected (RK2) by the computed horizontal velocity at 2 m (IDW of the 5 m pedestrian grid), drawn 1.5 m above the
    maquette surface. Samples are equally spaced in time (dt), so particles move at the computed speed."""
    from shapely.geometry import Point
    import pandas as pd
    d = pd.read_csv(Path(post) / f"champ_pieton_{sid}.csv"); d = d[d.secteur == k]
    XY = d[["x", "y"]].values; V = d[["Ux", "Uy"]].values; t = cKDTree(XY)
    def vel(p):
        dd, ii = t.query(p, k=4); w = 1 / np.maximum(dd, 0.5) ** 2
        v = (V[ii] * w[:, :, None]).sum(1) / w.sum(1)[:, None]
        v[dd[:, 0] > 4.0] = 0.0          # outside the fluid grid (inside a building / out of the sector)
        return v
    tan = np.gradient(S, axis=0); tan /= np.linalg.norm(tan, axis=1)[:, None] + 1e-9; nrm = np.c_[-tan[:, 1], tan[:, 0]]
    seeds = np.vstack([S[i] + o * nrm[i] for i in range(0, len(S)) for o in (-12, -8, -4, 0, 4, 8, 12)])
    seeds = seeds[(t.query(seeds)[0] < 3.0) & np.array([roofs.distance(Point(q)) >= 1.0 for q in seeds])]
    lines = []
    for sgn in (-1.0, 1.0):                    # backward then forward from each seed
        P = seeds.copy(); tr = [P.copy()]
        for _ in range(int(T / dt)):
            k1 = vel(P); k2 = vel(P + sgn * 0.5 * dt * k1); P = P + sgn * dt * k2; tr.append(P.copy())
        tr = np.stack(tr, 1)
        lines.append(tr if sgn > 0 else tr[:, ::-1])
    L = np.concatenate([lines[0][:, :-1], lines[1]], 1)           # (nseed, nt, 2), time increasing
    blocks = []
    for j, l in enumerate(L):
        if np.linalg.norm(np.diff(l, axis=0), axis=1).sum() < 5.0:      # (almost) still air: nothing to draw
            continue
        U = np.r_[np.linalg.norm(vel(l), axis=1)] / uref
        z = zs(l) + (1.0 if j % 2 else 2.2)                                 # two heights around the 2 m field
        blocks.append(np.vstack([[len(l), 0, 0, 0], np.c_[l, z, U]]))
    return np.concatenate(blocks).astype(np.float32) if blocks else np.zeros((0, 4), np.float32)


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
            nm, pts = STREETS[k]; S, Z, roofs = street_path(scene, k, pts, zg)
            zs = surface_z(scene, k); Zs = zs(S)
            street_particles(post, sid, k, S, roofs, zs, u).tofile(Path(post) / "vues" / "web" / f"{k}_slrue.bin")
            from shapely.geometry import LineString
            n = 80; idx = np.linspace(0, len(S) - 12, n).astype(int)
            for j, i in enumerate(idx):
                # look ahead as far as the line of sight stays >= 1 m from the facades (bends)
                m = next((m for m in (10, 7, 5, 3) if roofs.distance(LineString([S[i], S[i + m]])) >= 1.0), 3)
                F.append({"clip": f"wind_rue_{k}", "sector": k, "cam": t3(S[i, 0], S[i, 1], Zs[i] + 1.7),
                          "tgt": t3(S[i + m, 0], S[i + m, 1], Zs[i + m] + 1.5), "fov": 68, "uref": u,
                          "streetParticles": True, "t": j * 0.25, "title": f"{nm} — à hauteur de piéton",
                          "subtitle": f"Vent calculé dans la rue ({sid}), œil à 1,7 m · particules portées par le vent à 2 m", "tech": tech,
                          "tag": "Caméra posée sur la chaussée de la maquette ; particules = vent calculé à 2 m, couleur = U/Uref"})
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
