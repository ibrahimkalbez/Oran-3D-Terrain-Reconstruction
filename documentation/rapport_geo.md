# Rapport de géoréférencement — `brahim ib9a.3dm`

## 1. Ce que contient le fichier Rhino (analyse)

| Élément | Constat |
|---|---|
| Logiciel | Rhinoceros 8 Commercial, auteur « mahi kelbe » |
| Contenu | 295 maillages (1 par calque), 2 458 323 faces, 9 755 087 sommets (non soudés) |
| Origine | Données OpenStreetMap (calques `BUILDING*`, `HIGHWAY_*`, `LANDUSE_*`, `NAME_*`…) |
| Bâtiments | ≈ 191 000 solides fermés (calque `EXTRA_BUILD_BY_TURBOCG_COM` + `BUILDING*` + `HEIGHT_*`) |
| Unité déclarée | **Millimètres — incorrecte** : le calque `HEIGHT_104` mesure exactement 104 unités de haut → 1 unité = 1 m |
| Géoréférencement | Point d'ancrage terrestre à 0°/0° → **aucun géoréférencement réel** |
| Objets invalides | 7 maillages de routes (`HIGHWAY_*`) : faces dégénérées / doublons → corrigés |
| Relief | Aucun : tout est posé à z = 0 sur une plaque `BASE` plate de 30 km (z = −0,5) |

## 2. Projection retrouvée : Web Mercator

Le calage du trait de côte du modèle (calque `BAY`, 11 700 points) sur le trait de côte
du MNT Copernicus (optimisation robuste échelle + rotation + translation) donne :

| Paramètre | Valeur trouvée | Interprétation |
|---|---|---|
| Échelle modèle → mètres réels | **0,812** | = cos(35,7°) → coordonnées **Web Mercator** |
| Ajustement libre en Mercator : facteur | **0,99998** | = 1 → Mercator pur |
| Ajustement libre en Mercator : rotation | 0,09° | ≈ 0 |
| Centre (origine 0,0 du modèle) | lon −0,63500135 / lat 35,69901277 | à **1,4 m** du point rond **−0,635 / 35,699** |
| Sphérique (EPSG:3857) vs ellipsoïdal (EPSG:3395) | écart moyen 15,7 m vs 17,3 m | **sphérique** retenu |

**Conclusion** : les X,Y du modèle sont des mètres Web Mercator (EPSG:3857) relatifs au point
lon −0,635° / lat 35,699° ; les Z sont des mètres réels. Le modèle d'origine est donc
**étiré de 23 % à l'horizontale** par rapport aux hauteurs (1 000 m « modèle » = 813,02 m réels).

## 3. Repère de sortie

Chaque sommet est reprojeté **individuellement, par formule exacte** (aucun ajustement) :
Web Mercator → lon/lat WGS84 → **Transverse Mercator local** centré sur le même point
(`+proj=tmerc +lat_0=35.699 +lon_0=-0.635 +k=1 +ellps=WGS84`).

* mètres vrais, nord en haut, déformation < 3·10⁻⁶ sur toute la zone ;
* l'origine (0,0) reste au même endroit que dans le fichier d'origine ;
* le point d'ancrage terrestre Rhino est renseigné (35,699 N / 0,635 W) ;
* conversion UTM 30N : (0,0) local = E 713 978,4 / N 3 953 141,8.

Contrôle : aller-retour local → lon/lat → Web Mercator = coordonnées d'origine (écart
maximal mesuré dans `results/relief_report.json`, `xy_roundtrip_max_m`).

## 4. Relief : Copernicus GLO-30 utilisé tel quel

Copernicus est un modèle de *surface*. Test de l'influence des bâtiments :
régression du relief haute-fréquence sur la hauteur des 191 000 bâtiments →
coefficient 0,07, corrélation 0,04, biais moyen 0,4 m en ville. L'effet est négligeable ;
un filtre « sol nu » (ouverture morphologique) enlèverait autant de vrai relief
(1–2 m en zone rurale) → **aucun filtre appliqué**, relief Copernicus conservé.
Mer ouverte mise exactement à 0 m.

## 5. Précision

* Planimétrie : précision OpenStreetMap (quelques mètres) + incertitude du calage
  (± 10–15 m, limitée par la maille 30 m du MNT ; le centre rond retrouvé à 1,4 m près).
* Altimétrie : Copernicus GLO-30, erreur verticale absolue typique < 4 m (90 %), maille ≈ 25 × 31 m.
