"""Print source for the detailed urban maquette: terrain re-read at the corrected
georeferencing + rebuilt buildings (script 14), in the npz format read by
scripts/06_print_export.py (same print pipeline as the previous delivery).

Output: results/cache/urbaine_print_parts.npz
Then:   python scripts/06_print_export.py --cache results/cache/urbaine_print_parts.npz \
            --name Oran_maquette_urbaine
"""
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import urban_lib as u  # noqa: E402


def main():
    B = pickle.load(open(u.CACHE / "bati_14.pkl", "rb"))
    d = {"tx": B["terrain_x"], "ty": B["terrain_y"], "tz": B["Z_new"]}
    names = []
    for i, (ln, (V, F)) in enumerate(sorted(B["buildings"].items())):
        n, lab = u.mesh_parts(V, u.tris(F))           # one closed prism per building
        d[f"s{i}_V"], d[f"s{i}_F"], d[f"s{i}_P"] = V, F, lab
        names.append(ln)
        print(ln, n)
    d["names"] = np.array(names)
    np.savez_compressed(u.CACHE / "urbaine_print_parts.npz", **d)


if __name__ == "__main__":
    main()
