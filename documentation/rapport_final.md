# Rapport final — Oran 3D : modèle Rhino + relief Copernicus + maquette imprimable

## 1. Livrables

| Fichier | Contenu |
|---|---|
| `livraison/Oran_relief_Rhino8.7z.001…007` | **Fichier Rhino 8** `Oran_relief_Copernicus_Rhino8.3dm` (295 Mo) |
| `livraison/Oran_impression3D_OPT.7z.*` | Maquette imprimable optimisée : STL + OBJ (4,8 M triangles) |
| `livraison/Oran_impression3D_HD.7z.*` | Maquette imprimable haute résolution : STL + OBJ (8,9 M triangles) |
| `results/apercu_oran.png`, `results/apercu_3d_centre.png` | Aperçus |
| `results/relief_report.json`, `export_3d_print/print_report.json` | Tous les contrôles chiffrés |

**Ouvrir** : mettre toutes les parties `.001, .002…` d'une archive dans un même dossier →
clic droit sur `.001` → 7-Zip → *Extraire ici*. Empreinte SHA-256 dans `*.sha256.txt`.

## 2. Logiciels

Python 3.11 : rhino3dm 8 (lecture/écriture .3dm, sans Rhino), pyproj (projections),
rasterio/numpy/scipy (MNT), manifold3d (booléens exacts, garantis manifold),
py7zr (archives). Scripts dans `scripts/` (01 → 08), entièrement reproductibles.

## 3. Traitement

1. **Analyse** du `.3dm` (`03_analyze_rhino.py`, `documentation/rapport_geo.md`) : 295 maillages
   OpenStreetMap, unité « mm » erronée (en réalité mètres), aucun géoréférencement, 7 maillages invalides.
2. **Géoréférencement exact** (`oran_georef.py`) : le modèle est en Web Mercator (EPSG:3857) centré
   sur 35,699 N / 0,635 W (retrouvé à 1,4 m près sur le trait de côte Copernicus). Chaque sommet est
   reprojeté par formule en Transverse Mercator local (mètres vrais) → correction de l'étirement
   horizontal de 23 % du fichier d'origine. Contrôle aller-retour : **3,7·10⁻⁹ m**.
3. **Relief** : Copernicus GLO-30 (tuiles N35W001, N35W002), grille 25 m (résolution native),
   mer = 0 m exactement. Influence des bâtiments sur le MNT mesurée négligeable (corrélation 0,04) →
   pas de filtrage (qui aurait abîmé le vrai relief).
4. **Nettoyage** : soudure des sommets, suppression des faces dégénérées et doublons, séparation
   aux arêtes non-manifold (236 575 bâtiments individuels), **249 087 pièces réorientées** (normales
   tournées vers l'intérieur dans le fichier source), faces dégénérées finales supprimées
   (69 703) → **0 maillage invalide** au contrôle Rhino.
5. **Bâtiments** (blocs rigides, XY intacts) : posés au niveau **médian** du sol sous leur emprise,
   hauteur réelle conservée (écart 6·10⁻¹⁴ m) ; fondation prolongée verticalement jusqu'au point le
   plus bas → **aucun bâtiment ne flotte** (écart mesuré 0,000 m). Fondation : médiane 0,36 m,
   95 % < 1,6 m.
6. **Routes, voies ferrées, occupation du sol…** : drapées sommet par sommet avec raffinement
   adaptatif et test d'erreur exact ; tolérance par calque < hauteur de l'objet au-dessus du sol
   (≤ 0,5 m), empilement d'origine conservé. Eau (mer, lacs) plane.
7. **Maquette imprimable** (`06_print_export.py`) : terrain + 236 428 bâtiments fusionnés
   (union booléenne exacte) sur un **socle plein** ; chaque fichier exporté est contrôlé.

## 4. Contrôles de la maquette imprimable

| | HD | OPT |
|---|---|---|
| Triangles | 8 869 284 | 4 764 502 |
| Dimensions | 200 × 150,1 × 12,4 mm | idem |
| Étanche (watertight) | oui | oui |
| Arêtes ouvertes / non-manifold | 0 / 0 | 0 / 0 |
| Orientation cohérente | oui | oui |
| Nombre de corps | **1** | **1** |
| Volume | 175,7 cm³ | 175,8 cm³ |
| Posée à plat (z min) | 0,000 mm | 0,000 mm |

Échelle **1:145 375**, relief exagéré **×2**, socle **4 mm** sous le niveau de la mer.
1 559 « feuilles » de volume nul (contacts) retirées ; quelques centaines de triangles d'aire
nulle subsistent (sans effet pour le trancheur).

## 5. Limites de précision (à connaître)

* **Planimétrie** : précision OpenStreetMap (quelques m) + calage ± 10–15 m.
* **Altimétrie** : Copernicus GLO-30, maille ≈ 25 × 31 m, erreur verticale typique < 4 m.
* **Drapage** : 223 calques sur 225 ne touchent jamais le terrain ; 2 calques (`HIGHWAY_TRUNK`,
  `LANDUSE_BR`) passent localement jusqu'à 3,4 cm sous la surface du terrain, sur quelques points
  de falaise (invisible, très inférieur à la précision du MNT).
* 108 pièces ouvertes (avions de l'aéroport, non fermés) et 39 pièces hors zone ne sont pas dans
  la maquette imprimable (elles restent dans le fichier Rhino).
* **Taille des bâtiments imprimés** : à 1:145 375, la largeur médiane d'un bâtiment est 0,08 mm ;
  99 % sont plus fins qu'une buse de 0,4 mm → en FDM la ville apparaît comme une **texture** (relief,
  tissu urbain, grands équipements), pas comme des bâtiments individuels. Pour des bâtiments
  distincts : imprimer un **quartier** (ex. centre-ville 5 × 4 km sur 200 mm = 1:25 000) ou utiliser
  une imprimante **résine**.

## 6. Recommandations d'impression

* Orientation : socle à plat sur le plateau (déjà en z = 0).
* FDM : buse 0,4 mm (0,2 mm pour plus de détail), couche 0,08–0,12 mm, remplissage 10–15 %,
  sans supports. Résine : couche 0,03–0,05 mm, modèle creusé possible (socle plein de 4 mm).
* Taille : 200 × 150 mm passe sur la plupart des imprimantes ; mise à l'échelle uniforme possible
  dans le trancheur.
