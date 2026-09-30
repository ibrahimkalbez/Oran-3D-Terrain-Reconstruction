# Méthodologie — fusion du modèle Rhino (plat) avec le relief Copernicus 30 m

## Données d'entrée
- **Modèle Rhino d'Oran** (`data/rhino/*.3dm`) : ville complète (rues, bâtiments)
  modélisée **à plat** (z = 0 au sol). Géométrie de référence à respecter.
- **Copernicus DEM GLO-30** (`results/oran_dem_utm30n.tif`) : relief, maille 30 m,
  UTM 30N (EPSG:32630). Produit par `scripts/01_extract_dem.py`.

## Principe
La géométrie Rhino n'est jamais redessinée : on ne fait qu'ajouter une altitude.

1. **Géoréférencement** — transformation rigide (translation + rotation, échelle
   seulement si les unités sont fausses) du modèle vers UTM 30N, appliquée
   identiquement à tous les points. Calage par points de contrôle ou, à défaut,
   sur OpenStreetMap / trait de côte.
2. **Bâtiments** — chaque bâtiment est **translaté verticalement en bloc**
   (aucune déformation, hauteurs conservées). Altitude de pose = altitude
   **minimale** du terrain sous son emprise, pour qu'aucun bâtiment ne flotte ;
   le socle est prolongé vers le bas pour s'encastrer dans la pente.
3. **Rues et surfaces au sol** — drapées **point par point** : chaque sommet
   reçoit z = altitude du terrain (interpolation bilinéaire du MNT). Les courbes
   sont densifiées si un segment dépasse 30 m pour suivre le relief.
4. **Terrain** — maillage issu du MNT. Le GLO-30 est un modèle de *surface*
   (il inclut les bâtiments) : on le lisse légèrement en zone urbaine pour éviter
   un double relief sous les bâtiments Rhino.
5. **Fusion et impression** — union booléenne terrain + bâtiments + socle,
   réparation, contrôle d'étanchéité, export STL/OBJ dans `export_3d_print/`.

## Limites
- Précision altimétrique limitée par Copernicus (maille 30 m, erreur verticale
  typique de quelques mètres) : les micro-reliefs de rue ne sont pas captés.
- Précision planimétrique = précision du calage du modèle Rhino.
