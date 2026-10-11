"""Step 46 - ANSYS Fluent package (to run on the workstation where ANSYS is installed).

  python 46_package_ansys.py <mesh_case> <out_pkg> <scenario_case> [<scenario_case> ...]

Content of <out_pkg>/ (identical mesh and boundary data as the OpenFOAM runs):
  mesh/oran_<level>.msh            foamMeshToFluent export (zones = OpenFOAM patches)
  geometry/*.stl                   CFD geometry (buildings per sector + ground) for SpaceClaim
  scenarios/<id>/scenario.json     roles of the boundaries, inflow parameters, thermal settings
  scenarios/<id>/*.prof            Fluent profiles (inlet U,V,W,k,epsilon; T of ground and roofs)
  scripts/A02_pyfluent_solve.py, A03_lancer_file_attente.py
  batiments_cfd.csv                building ids <-> Rhino objects
  README_ANSYS.md
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    mesh_case, out = Path(sys.argv[1]), Path(sys.argv[2])
    level = "coarse" if "coarse" in mesh_case.name else "medium"
    (out / "mesh").mkdir(parents=True, exist_ok=True)
    msh = next((mesh_case / "fluentInterface").glob("*.msh"))
    shutil.copy(msh, out / "mesh" / f"oran_{level}.msh")
    (out / "geometry").mkdir(exist_ok=True)
    for f in (ROOT / "results/cfd/geometrie").glob("buildings_*.stl"):
        shutil.copy(f, out / "geometry" / f.name)
    shutil.copy(ROOT / "results/cfd/geometrie/batiments_cfd.csv", out)
    (out / "scripts").mkdir(exist_ok=True)
    for f in (ROOT / "rhino_ansys/ansys").glob("A0*.py"):
        shutil.copy(f, out / "scripts" / f.name)
    done = []
    for sc_case in map(Path, sys.argv[3:]):
        meta = json.loads((sc_case / "scenario.json").read_text())
        sid = meta["scenario"]["id"] + ("_thermique" if meta["thermal"] else "")
        d = out / "scenarios" / sid; d.mkdir(parents=True, exist_ok=True)
        shutil.copy(sc_case / "scenario.json", d)
        for f in (sc_case / "fluent").glob("*.prof"):
            shutil.copy(f, d / f.name)
        done.append(sid)
    (out / "README_ANSYS.md").write_text(f"""# Paquet ANSYS Fluent — vent et chaleur à Oran

Maillage `mesh/oran_{level}.msh` : **identique** au maillage OpenFOAM utilisé pour les calculs déjà réalisés
(export `foamMeshToFluent`). Zones : `ground`, `buildings_CENTRE_VILLE`, `buildings_GAMBETTA`,
`buildings_BD_DES_LIONS`, `west`, `east`, `south`, `north`, `top`. Unités : mètres, repère de la maquette Rhino.

Scénarios préparés : {", ".join(done)}.

## Lancer (poste avec ANSYS Fluent ≥ 2023 R1)

```
pip install ansys-fluent-core
python scripts/A03_lancer_file_attente.py . {" ".join(done)} --cores 8
```

Les calculs tournent **un par un** dans un processus séparé (Rhino reste utilisable) ;
journal général `queue.log`, un journal par calcul `run_<id>.log`, résultats dans `runs/<id>/`
(case/data, `export_*.csv`, `report.json`). Les CSV se réimportent dans Rhino avec
`rhino/R03_import_resultats.py` après conversion au format d'échange (colonnes x,y,z,U,Ux,Uy,Uz,p[,T]).

## Ce qui n'a PAS été fait dans l'environnement de préparation

ANSYS n'y était pas disponible : ces scripts n'y ont **pas** été exécutés. Les résultats présentés dans le
rapport et la vidéo proviennent d'OpenFOAM v1912 (même maillage, mêmes conditions aux limites, même modèle
k-ε réalisable). Le paquet permet de refaire exactement les mêmes cas sous Fluent et de comparer.

## SpaceClaim (variante géométrique)

`geometry/*.stl` peut être importé dans SpaceClaim (Fichier > Ouvrir, STL en mètres) pour reconstruire un
domaine fluide (Préparer > Enveloppe, boîte 17 x 10 km x 900 m), puis maillé dans Fluent Meshing
(workflow Watertight) si vous préférez un maillage polyédrique Fluent.
""")
    print("package", out, done)


if __name__ == "__main__":
    main()
