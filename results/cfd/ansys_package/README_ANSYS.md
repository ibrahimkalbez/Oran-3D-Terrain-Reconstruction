# Paquet ANSYS Fluent — vent et chaleur à Oran

Maillage `mesh/oran_coarse.msh.gz` (lu tel quel par Fluent) : **identique** au maillage OpenFOAM utilisé pour les calculs déjà réalisés
(export `foamMeshToFluent`). Zones : `ground`, `buildings_CENTRE_VILLE`, `buildings_GAMBETTA`,
`buildings_BD_DES_LIONS`, `west`, `east`, `south`, `north`, `top`. Unités : mètres, repère de la maquette Rhino.

Scénarios préparés : S6_ete_NNO, S2_SO, S6_ete_NNO_thermique.

## Lancer (poste avec ANSYS Fluent ≥ 2023 R1)

```
pip install ansys-fluent-core
python scripts/A03_lancer_file_attente.py . S6_ete_NNO S2_SO S6_ete_NNO_thermique --cores 8
```

Les calculs tournent **un par un** dans un processus séparé (Rhino reste utilisable) ;
journal général `queue.log`, un journal par calcul `run_<id>.log`, résultats dans `runs/<id>/`
(case/data, `export_*.csv`, `report.json`). Les CSV se réimportent dans Rhino avec
`rhino/R03_import_resultats.py` après conversion au format d'échange (colonnes x,y,z,U,Ux,Uy,Uz,p[,T]).

## Ce qui n'a PAS été fait dans l'environnement de préparation

ANSYS n'y était pas disponible : ces scripts n'y ont **pas** été exécutés. Les résultats présentés dans le
rapport et la vidéo proviennent d'OpenFOAM v1912 (même maillage, mêmes conditions aux limites, même modèle
k-ε réalisable). Le paquet permet de refaire exactement les mêmes cas sous Fluent et de comparer.

## Cas thermique : stabilisation

Sous OpenFOAM, le cas thermique démarré au repos a divergé (mailles aplaties plaquées sur le relief hors des
secteurs). Le calcul retenu part du champ isotherme convergé de S6 : sous Fluent, calculer d'abord
`S6_ete_NNO`, puis initialiser `S6_ete_NNO_thermique` depuis ses données (File > Interpolate, ou lancer le
cas thermique sur le case/data de S6), avec des facteurs de sous-relaxation réduits (0,4 sur U, k, ε, T).

## Géométrie

`geometry/` n'est pas copié dans ce paquet : les bâtiments STL et `batiments_cfd.csv` sont dans `results/cfd/geometrie/` du dépôt ; `ground.stl` se régénère avec `scripts/cfd/43_geometrie_cfd.py`.

## SpaceClaim (variante géométrique)

`geometry/*.stl` peut être importé dans SpaceClaim (Fichier > Ouvrir, STL en mètres) pour reconstruire un
domaine fluide (Préparer > Enveloppe, boîte 17 x 10 km x 900 m), puis maillé dans Fluent Meshing
(workflow Watertight) si vous préférez un maillage polyédrique Fluent.
