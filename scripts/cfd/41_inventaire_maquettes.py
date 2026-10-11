"""Step 41 - inventory of the two Rhino 8 models (FILE inspection with rhino3dm, outside
Rhino): detailed textured urban maquette + thermal maquette.

For each file: units, tolerance, Earth anchor, layer tree (path, colour, material,
object count per geometry type, bounding box), materials and texture files,
block definitions/instances, user strings, invalid meshes, open (non closed)
meshes, exact duplicates (same geometry hash), thermal layers (colour classes,
point-cloud values). Also the spatial correspondence between the two files
(common origin, bbox overlap, same building layers).

Usage : python 41_inventaire_maquettes.py <urban.3dm> <thermal.3dm>
Output: results/cfd/inventaire/inventaire_<name>.json + inventaire_calques_<name>.csv
"""
import csv
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import rhino3dm as r3

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results/cfd/inventaire"


def mesh_hash(g):
    V = np.array([[p.X, p.Y, p.Z] for p in g.Vertices], np.float32)
    return hashlib.sha1(np.round(V, 3).tobytes()).hexdigest(), V


def inventory(path):
    m = r3.File3dm.Read(str(path))
    S = m.Settings
    ea = S.EarthAnchorPoint
    lay = {}
    for i in range(len(m.Layers)):
        L = m.Layers[i]
        lay[i] = {"index": i, "path": L.FullPath, "color": list(L.Color[:3]), "material": L.RenderMaterialIndex,
                  "visible": L.Visible, "counts": defaultdict(int), "bbox": [np.inf, np.inf, np.inf, -np.inf, -np.inf, -np.inf],
                  "faces": 0, "vertices": 0, "open_meshes": 0, "invalid": 0, "user_strings": 0, "pc_points": 0,
                  "pc_values": False}
    hashes = defaultdict(list)
    for o in m.Objects:
        g = o.Geometry; a = o.Attributes; L = lay[a.LayerIndex]
        t = type(g).__name__; L["counts"][t] += 1
        if a.UserStringCount:
            L["user_strings"] += 1
        bb = g.GetBoundingBox()
        if bb.IsValid:
            L["bbox"] = [min(L["bbox"][0], bb.Min.X), min(L["bbox"][1], bb.Min.Y), min(L["bbox"][2], bb.Min.Z),
                         max(L["bbox"][3], bb.Max.X), max(L["bbox"][4], bb.Max.Y), max(L["bbox"][5], bb.Max.Z)]
        if isinstance(g, r3.Mesh):
            L["faces"] += g.Faces.Count; L["vertices"] += len(g.Vertices)
            if not g.IsValid:
                L["invalid"] += 1
            if not g.IsClosed:
                L["open_meshes"] += 1
            h, _ = mesh_hash(g); hashes[h].append(L["path"])
        elif isinstance(g, r3.PointCloud):
            L["pc_points"] += g.Count; L["pc_values"] = L["pc_values"] or g.ContainsValues
    dup = {h: v for h, v in hashes.items() if len(v) > 1}
    mats = [{"index": i, "name": m.Materials[i].Name, "diffuse": list(m.Materials[i].DiffuseColor[:3]),
             "texture": (m.Materials[i].GetBitmapTexture().FileName if m.Materials[i].GetBitmapTexture() else None)}
            for i in range(len(m.Materials))]
    layers = []
    for L in lay.values():
        L["counts"] = dict(L["counts"])
        L["bbox"] = [round(float(x), 2) for x in L["bbox"]] if np.isfinite(L["bbox"][0]) else None
        layers.append(L)
    tot = defaultdict(int)
    for L in layers:
        for k, v in L["counts"].items():
            tot[k] += v
    rep = {"file": path.name, "MB": round(path.stat().st_size / 1e6, 1),
           "unit": str(S.ModelUnitSystem), "abs_tolerance": S.ModelAbsoluteTolerance,
           "earth_anchor_lat_lon": [ea.EarthBasepointLatitude, ea.EarthBasepointLongitude],
           "n_layers": len(layers), "n_objects": len(m.Objects), "objects_by_type": dict(tot),
           "n_materials": len(mats), "materials": mats,
           "n_instance_definitions": len(m.InstanceDefinitions),
           "invalid_meshes": int(sum(L["invalid"] for L in layers)),
           "open_meshes": int(sum(L["open_meshes"] for L in layers)),
           "duplicate_mesh_groups": len(dup), "duplicates_examples": list(dup.values())[:10],
           "layers": layers}
    return rep


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    reps = []
    for p in sys.argv[1:]:
        p = Path(p)
        rep = inventory(p)
        reps.append(rep)
        name = p.stem
        (OUT / f"inventaire_{name}.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False, default=str))
        with open(OUT / f"inventaire_calques_{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["calque", "objets", "types", "faces", "sommets", "maillages_ouverts", "invalides",
                        "points_nuage", "valeurs_nuage", "materiau", "xmin", "ymin", "zmin", "xmax", "ymax", "zmax"])
            for L in rep["layers"]:
                b = L["bbox"] or [""] * 6
                w.writerow([L["path"], sum(L["counts"].values()), ";".join(f"{k}:{v}" for k, v in L["counts"].items()),
                            L["faces"], L["vertices"], L["open_meshes"], L["invalid"], L["pc_points"], L["pc_values"],
                            L["material"], *b])
        print(json.dumps({k: rep[k] for k in rep if k not in ("layers", "materials")}, indent=1, default=str))
    if len(reps) == 2:
        a, b = reps
        pa = {L["path"] for L in a["layers"]}; pb = {L["path"] for L in b["layers"]}
        def bb(rep):
            B = np.array([L["bbox"] for L in rep["layers"] if L["bbox"]])
            return [float(B[:, 0].min()), float(B[:, 1].min()), float(B[:, 3].max()), float(B[:, 4].max())]
        corr = {"same_unit": a["unit"] == b["unit"], "same_anchor": a["earth_anchor_lat_lon"] == b["earth_anchor_lat_lon"],
                "bbox_xy_a": bb(a), "bbox_xy_b": bb(b), "common_layers": len(pa & pb),
                "only_in_a": sorted(pa - pb)[:60], "only_in_b": sorted(pb - pa)[:60]}
        (OUT / "correspondance_maquettes.json").write_text(json.dumps(corr, indent=1, ensure_ascii=False))
        print(json.dumps(corr, indent=1, ensure_ascii=False)[:3000])


if __name__ == "__main__":
    main()
