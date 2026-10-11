"""Step 48 - realistic textured scene of the detailed Rhino maquette for the video, per sector.

Reads Oran_maquette_urbaine_detaillee_Rhino8.3dm (read only) and exports, for each CFD sector
(box + margin), the geometry grouped by Rhino render material (asphalts, paving, concrete
sidewalks, granite curbs, crossings and markings, islands, vegetation, terrain), the CFD
reference buildings (the meshed ones), and the street furniture blocks (street lamps, alignment
and park trees, bollards, signs, bus shelters) as instanced meshes with their transforms.
Textures are the maquette's own files (textures/*.png, planar projection, 4 m repeat as in Rhino).

  python 48_scene_realiste.py <detailed.3dm> <textures_dir> <out_dir>
Output: <out_dir>/<SECTOR>_mat<i>.bin (float32 x,y,z,u,v per vertex, non-indexed triangles),
        <SECTOR>_bld.bin, <SECTOR>_terrain.bin, blk_<def>.bin (+ material), <SECTOR>_inst_<def>.bin
        (float32 4x4 column-major matrices, three.js axes), materials.json, textures/
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import rhino3dm as r3

ROOT = Path(__file__).resolve().parents[2]
SECT = json.loads((ROOT / "results/cfd/secteurs.json").read_text())
GEO = ROOT / "results/cfd/geometrie"
MARGIN = 250.0
LAYER_PREFIX = ("ROAD_", "SIDEWALK", "CURB", "CROSSWALK", "ROAD_MARKING", "ROAD_ISLAND", "GREEN_SPACE", "PEDESTRIAN_ZONE")
LIFT = {7: 0.06, 8: 0.06}            # markings / crossings slightly above the asphalt (avoid z-fighting)


def tris(g):
    V = np.array([[p.X, p.Y, p.Z] for p in g.Vertices], np.float64)
    F = np.array([tuple(g.Faces[k]) for k in range(g.Faces.Count)], np.int64).reshape(-1, 4)
    q = F[:, 2] != F[:, 3]
    return V[np.r_[F[:, [0, 1, 2]], F[q][:, [0, 2, 3]]]]


def to3(P):
    return np.stack([P[..., 0], P[..., 2], -P[..., 1]], -1)


def xform(t):
    M = np.array([[t.M00, t.M01, t.M02, t.M03], [t.M10, t.M11, t.M12, t.M13],
                  [t.M20, t.M21, t.M22, t.M23], [t.M30, t.M31, t.M32, t.M33]])
    A = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], float)   # model -> three axes
    return A @ M @ np.linalg.inv(A)


def main():
    src, tex, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]); out.mkdir(parents=True, exist_ok=True)
    m = r3.File3dm.Read(src)
    mats = [{"index": i, "name": m.Materials[i].Name, "diffuse": list(m.Materials[i].DiffuseColor[:3]),
             "texture": (Path(m.Materials[i].GetBitmapTexture().FileName.replace("\\", "/")).name
                         if m.Materials[i].GetBitmapTexture() else None)} for i in range(len(m.Materials))]
    (out / "textures").mkdir(exist_ok=True)
    for f in tex.glob("*.png"):
        shutil.copy(f, out / "textures" / f.name)
    boxes = {k: [s["model_box_m"][0] - MARGIN, s["model_box_m"][1] - MARGIN, s["model_box_m"][2] + MARGIN,
                 s["model_box_m"][3] + MARGIN] for k, s in SECT.items()}
    groups = {k: {} for k in SECT}
    insts = {k: {} for k in SECT}
    idef_layer = {}
    for o in m.Objects:
        a = o.Attributes; L = m.Layers[a.LayerIndex]; g = o.Geometry
        if isinstance(g, r3.InstanceReference):
            if not L.FullPath.startswith("URBAN_FURNITURE"):
                continue
            t = g.Xform; x, y = t.M03, t.M13
            for k, b in boxes.items():
                if b[0] < x < b[2] and b[1] < y < b[3]:
                    insts[k].setdefault(str(g.ParentIdefId), []).append(xform(t).T.ravel())
            idef_layer[str(g.ParentIdefId)] = L.FullPath
            continue
        if not isinstance(g, r3.Mesh) or not L.FullPath.startswith(LAYER_PREFIX):
            continue
        bb = g.GetBoundingBox()
        mat = L.RenderMaterialIndex if a.MaterialSource == r3.ObjectMaterialSource.MaterialFromLayer else a.MaterialIndex
        hit = [k for k, b in boxes.items() if bb.Min.X < b[2] and bb.Max.X > b[0] and bb.Min.Y < b[3] and bb.Max.Y > b[1]]
        if not hit:
            continue
        P = tris(g); c = P.mean(1)
        for k in hit:
            b = boxes[k]
            sel = (c[:, 0] > b[0]) & (c[:, 0] < b[2]) & (c[:, 1] > b[1]) & (c[:, 1] < b[3])
            if sel.any():
                Q = P[sel].copy(); Q[..., 2] += LIFT.get(mat, 0.0)
                groups[k].setdefault(mat, []).append(Q)
    rep = {"materials": mats, "sectors": {}}
    for k in SECT:
        rep["sectors"][k] = {"materials": {}, "instances": {}}
        for mat, lst in groups[k].items():
            P = np.concatenate(lst)
            uv = P[..., :2] / 4.0                                      # planar mapping, 4 m repeat (as in Rhino)
            np.concatenate([to3(P), uv], -1).astype(np.float32).tofile(out / f"{k}_mat{mat}.bin")
            rep["sectors"][k]["materials"][str(mat)] = int(len(P))
        txt = (GEO / f"buildings_{k}.stl").read_text().split("vertex")[1:]
        B = np.array([list(map(float, t.split()[:3])) for t in txt]).reshape(-1, 3, 3)
        to3(B).astype(np.float32).tofile(out / f"{k}_bld.bin")
        g = np.load(GEO / "terrain_grid.npz"); xs, ys, Z = g["xs"], g["ys"], g["Z"]; b = boxes[k]
        ix = (xs > b[0] - 300) & (xs < b[2] + 300); iy = (ys > b[1] - 300) & (ys < b[3] + 300)
        X, Y = np.meshgrid(xs[ix], ys[iy]); Zc = Z[np.ix_(iy, ix)] - 0.05
        n2 = ix.sum(); i = (np.arange(iy.sum() - 1)[:, None] * n2 + np.arange(n2 - 1)[None, :]).ravel()
        T = np.r_[np.c_[i, i + n2, i + 1], np.c_[i + 1, i + n2, i + n2 + 1]]
        Pt = np.c_[X.ravel(), Y.ravel(), Zc.ravel()][T]
        np.concatenate([to3(Pt), Pt[..., :2] / 4.0], -1).astype(np.float32).tofile(out / f"{k}_terrain.bin")
        for d, lst in insts[k].items():
            np.array(lst, np.float32).tofile(out / f"{k}_inst_{d}.bin")
            rep["sectors"][k]["instances"][d] = len(lst)
    # block definitions geometry (each object of the definition with its material)
    rep["blocks"] = {}
    for i in range(len(m.InstanceDefinitions)):
        idf = m.InstanceDefinitions[i]; did = str(idf.Id)
        if did not in idef_layer:
            continue
        parts = []
        for oid in idf.GetObjectIds():
            o = m.Objects.FindId(oid)
            if o is None or not isinstance(o.Geometry, r3.Mesh):
                continue
            a = o.Attributes
            mat = a.MaterialIndex if a.MaterialSource == r3.ObjectMaterialSource.MaterialFromObject else m.Layers[a.LayerIndex].RenderMaterialIndex
            P = to3(tris(o.Geometry)).astype(np.float32)
            P.tofile(out / f"blk_{did}_{len(parts)}.bin"); parts.append({"file": f"blk_{did}_{len(parts)}.bin", "mat": int(mat), "tris": len(P)})
        rep["blocks"][did] = {"name": idf.Name, "layer": idef_layer[did], "parts": parts}
    (out / "materials.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    print(json.dumps({k: {"mats": v["materials"], "inst": {rep["blocks"].get(d, {}).get("name", d): n for d, n in v["instances"].items()}}
                      for k, v in rep["sectors"].items()}, indent=1, ensure_ascii=False))
    print("blocks", {v["name"]: [p["tris"] for p in v["parts"]] for v in rep["blocks"].values()})


if __name__ == "__main__":
    main()
