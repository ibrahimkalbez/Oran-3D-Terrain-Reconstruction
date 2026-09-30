"""Step 1 - Analyse the Rhino model of Oran (no Rhino needed).

Reports units, extent, georeferencing, layers, object types, invalid objects
and how each solid can be meshed. Writes results/rhino_analysis.json and
results/rhino_analysis.md.

Usage : python scripts/03_analyze_rhino.py data/rhino/brahim_ib9a.3dm
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import rhino3dm as r3

sys.path.insert(0, str(Path(__file__).parent))
from rhino_utils import geometry_to_mesh, iter_objects, unit_scale  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main(path):
    model = r3.File3dm.Read(str(path))
    if model is None:
        sys.exit(f"Cannot read {path} (not a valid .3dm?)")
    unit, to_m = unit_scale(model)
    layers = {i: model.Layers[i].FullPath for i in range(len(model.Layers))}

    types, per_layer, methods = Counter(), defaultdict(Counter), Counter()
    invalid, closed_meshes, open_meshes = [], 0, 0
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for g, attr, xf in iter_objects(model):
        t = type(g).__name__
        types[t] += 1
        per_layer[layers.get(attr.LayerIndex, "?")][t] += 1
        bb = g.GetBoundingBox()
        if bb.IsValid:
            lo = np.minimum(lo, [bb.Min.X, bb.Min.Y, bb.Min.Z])
            hi = np.maximum(hi, [bb.Max.X, bb.Max.Y, bb.Max.Z])
        if not g.IsValid:
            invalid.append({"id": str(attr.Id), "type": t, "layer": layers.get(attr.LayerIndex)})
        res = geometry_to_mesh(g)
        if res is not None:
            methods[res[2]] += 1
            if isinstance(g, r3.Mesh):
                closed_meshes += g.IsClosed
                open_meshes += not g.IsClosed

    ea = model.Settings.EarthAnchorPoint
    ext = (hi - lo) * to_m
    looks_utm30 = 1e5 < lo[0] * to_m < 9e5 and 3.8e6 < lo[1] * to_m < 4.1e6
    report = {
        "file": str(path),
        "created_by": model.CreatedBy, "last_edited": str(model.LastEdited),
        "app": model.ApplicationName,
        "units": unit, "unit_to_m": to_m,
        "bbox_min_model": lo.tolist(), "bbox_max_model": hi.tolist(),
        "extent_m": ext.round(2).tolist(),
        "earth_anchor_set": bool(ea.EarthLocationIsSet()),
        "earth_anchor_lat_lon": [ea.EarthBasepointLatitude, ea.EarthBasepointLongitude],
        "coords_look_like_utm30n": bool(looks_utm30),
        "object_types": dict(types),
        "objects_per_layer": {k: dict(v) for k, v in sorted(per_layer.items())},
        "solid_mesh_methods": dict(methods),
        "meshes_closed_open": [closed_meshes, open_meshes],
        "invalid_objects": invalid[:200], "invalid_count": len(invalid),
    }
    out = ROOT / "results"
    (out / "rhino_analysis.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))

    md = [f"# Analyse du modèle Rhino — `{Path(path).name}`", "",
          f"- Unités : **{unit}** (1 unité = {to_m} m)",
          f"- Emprise : **{ext[0]:.1f} × {ext[1]:.1f} m**, hauteur {ext[2]:.1f} m",
          f"- Coin min (modèle) : {lo.round(2).tolist()}",
          f"- Coordonnées de type UTM 30N : **{'oui' if looks_utm30 else 'non'}**",
          f"- Point d'ancrage terrestre défini : **{'oui' if report['earth_anchor_set'] else 'non'}**",
          f"- Objets invalides : **{len(invalid)}**", "", "## Types d'objets", ""]
    md += [f"- {k} : {v}" for k, v in types.most_common()]
    md += ["", "## Maillage des solides", ""] + [f"- {k} : {v}" for k, v in methods.items()]
    md += ["", "## Objets par calque", ""]
    md += [f"- **{k}** : " + ", ".join(f"{t} {n}" for t, n in v.items())
           for k, v in sorted(per_layer.items())]
    (out / "rhino_analysis.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ROOT / "data/rhino/brahim_ib9a.3dm")
