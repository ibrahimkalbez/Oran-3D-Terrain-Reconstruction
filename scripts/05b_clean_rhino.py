"""Clean an existing Rhino file in place: every mesh goes through
clean_for_rhino() (weld identical vertices, cull degenerate and duplicate
faces, drop unused vertices, recompute normals) and is checked with Rhino's
own validity test. Layers, attributes, units and Earth anchor are kept.
Usage: python scripts/05b_clean_rhino.py results/Oran_relief_Copernicus_Rhino8.3dm"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import rhino3dm as r3

spec = importlib.util.spec_from_file_location("b", Path(__file__).parent / "05_build_oran_relief.py")
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)


def main(path):
    src = r3.File3dm.Read(path)
    out = r3.File3dm()
    out.Settings.ModelUnitSystem = src.Settings.ModelUnitSystem
    out.Settings.ModelAbsoluteTolerance = src.Settings.ModelAbsoluteTolerance
    out.Settings.EarthAnchorPoint = src.Settings.EarthAnchorPoint
    for i in range(len(src.Layers)):
        L = src.Layers[i]; n = r3.Layer()
        n.Name, n.Color, n.Visible = L.Name, L.Color, L.Visible
        out.Layers.Add(n)
    bad, nf0, nf1 = [], 0, 0
    for o in src.Objects:
        g = o.Geometry
        V = np.array([[p.X, p.Y, p.Z] for p in g.Vertices], np.float64)
        F = np.array([tuple(g.Faces[k]) for k in range(g.Faces.Count)], np.int64).reshape(-1, 4)
        nf0 += len(F)
        b.add_mesh(out, V, F, o.Attributes)
    for o in out.Objects:
        nf1 += o.Geometry.Faces.Count
        ok, log = o.Geometry.IsValidWithLog
        if not ok:
            bad.append((out.Layers[o.Attributes.LayerIndex].Name, log.strip()[:120]))
    out.Write(path, 8)
    print(f"faces {nf0} -> {nf1}; invalid meshes after cleaning: {len(bad)}")
    for x in bad[:20]:
        print("  ", x)


if __name__ == "__main__":
    main(sys.argv[1])
