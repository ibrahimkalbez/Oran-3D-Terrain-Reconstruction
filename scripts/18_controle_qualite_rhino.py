"""Quality control of the delivered Rhino file (re-read from disk).

For every mesh object: Rhino validity (IsValidWithLog), open edges, non-manifold
edges, degenerate faces, normal orientation (closed parts: outward = positive
volume; open sheets: facing up), per layer.  Also: block instances, named
views, empty layers, materials and texture files.
Output: results/controle_qualite_rhino.json
Usage : python scripts/18_controle_qualite_rhino.py [file.3dm]
"""
import json
import sys
from pathlib import Path

import numpy as np
import rhino3dm as r3

sys.path.insert(0, str(Path(__file__).parent))
import urban_lib as u  # noqa: E402

ROOT = u.ROOT
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "results/Oran_maquette_urbaine_detaillee_Rhino8.3dm"


def check(V, F):
    T = u.tris(F)
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    N = np.cross(b - a, c - a)
    area = 0.5 * np.linalg.norm(N, axis=1)
    E = np.r_[T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]
    _, inv, cnt = np.unique(np.sort(E, 1), axis=0, return_inverse=True, return_counts=True)
    n, lab = u.mesh_parts(V, T)
    tl = lab[T[:, 0]]
    open_part = np.zeros(n, bool)
    open_part[np.r_[tl, tl, tl][cnt[inv.ravel()] == 1]] = True
    vol = np.bincount(tl, np.einsum("ij,ij->i", a, np.cross(b, c)), minlength=n)
    up = np.bincount(tl, N[:, 2], minlength=n)
    used = np.unique(T)
    return {"faces": int(len(T)), "parts": int(len(np.unique(tl))),
            "closed_parts": int((~open_part[np.unique(tl)]).sum()),
            "open_edges": int((cnt == 1).sum()), "nonmanifold_edges": int((cnt > 2).sum()),
            "degenerate_faces": int((area < 1e-9).sum()),
            "inward_closed_parts": int(((vol < 0) & ~open_part).sum()),
            "downward_open_parts": int(((up < 0) & open_part).sum()),
            "unused_vertices": int(len(V) - len(used))}


def main():
    f = r3.File3dm.Read(str(SRC))
    paths = {i: f.Layers[i].FullPath for i in range(len(f.Layers))}
    per_layer, invalid, n_inst, n_curves = {}, [], 0, 0
    used_layers = set()
    for o in f.Objects:
        g = o.Geometry
        li = o.Attributes.LayerIndex
        used_layers.add(li)
        if isinstance(g, r3.InstanceReference):
            n_inst += 1
            continue
        if not isinstance(g, r3.Mesh):
            n_curves += 1
            continue
        ok, logtxt = g.IsValidWithLog
        if not ok:
            invalid.append({"layer": paths[li], "name": o.Attributes.Name, "log": logtxt[:300]})
        V = np.array([[p.X, p.Y, p.Z] for p in g.Vertices], np.float64)
        F = np.array([tuple(g.Faces[k]) for k in range(g.Faces.Count)], np.int64).reshape(-1, 4)
        r = check(V, F)
        L = per_layer.setdefault(paths[li], {k: 0 for k in r} | {"objects": 0})
        for k, v in r.items():
            L[k] += v
        L["objects"] += 1
    # parents are allowed to be empty (they hold sub-layers)
    parents = {p.rsplit("::", 1)[0] for p in paths.values() if "::" in p}
    empty = [p for i, p in paths.items() if i not in used_layers and p not in parents]
    tot = {k: sum(v[k] for v in per_layer.values()) for k in next(iter(per_layer.values()))}
    tex = []
    for i in range(len(f.Materials)):
        t = f.Materials[i].GetBitmapTexture()
        if t and t.FileName:
            tex.append({"material": f.Materials[i].Name, "file": t.FileName,
                        "exists_next_to_3dm": (SRC.parent / t.FileName).exists()})
    rep = {"file": SRC.name, "file_MB": round(SRC.stat().st_size / 1e6, 1),
           "units": str(f.Settings.ModelUnitSystem), "layers": len(f.Layers), "empty_layers": empty,
           "mesh_objects": tot["objects"], "invalid_meshes": invalid, "totals": tot,
           "block_definitions": len(f.InstanceDefinitions), "block_instances": n_inst,
           "curves": n_curves, "named_views": [f.NamedViews[i].Name for i in range(len(f.NamedViews))],
           "materials": len(f.Materials), "textures": tex,
           "earth_anchor_lonlat": [f.Settings.EarthAnchorPoint.EarthBasepointLongitude,
                                   f.Settings.EarthAnchorPoint.EarthBasepointLatitude],
           "per_layer": per_layer}
    (ROOT / "results/controle_qualite_rhino.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in rep.items() if k != "per_layer"}, indent=1, ensure_ascii=False))
    bad = {k: v for k, v in per_layer.items() if v["open_edges"] or v["nonmanifold_edges"] or v["degenerate_faces"]
           or v["inward_closed_parts"] or v["downward_open_parts"]}
    print("layers with remarks:", json.dumps(bad, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
