# Préparer une définition Grasshopper pour Claude

Le connecteur fonctionne avec **n'importe quelle définition**, sans modification. Quelques conventions
simples rendent cependant les échanges plus fiables : Claude retrouve les bons paramètres du premier coup
et sait quelles valeurs comparer entre variantes.

## 1. Les paramètres d'entrée

Sont reconnus automatiquement comme **entrées** :

| Objet Grasshopper | Valeur lue / modifiée par Claude |
|---|---|
| Number Slider | valeur, min, max, décimales, type (réel, entier, pair, impair) |
| Boolean Toggle | vrai / faux |
| Value List | élément sélectionné (par nom, valeur ou index) |
| Panel non connecté en entrée | texte |
| Colour Swatch | couleur `#RRGGBB` |
| Paramètres flottants non connectés contenant une valeur (Number, Integer, Text, Boolean, Point, Vector, Colour, File Path) | valeur(s) |

**Nommez vos sliders** (clic droit → nom / *nickname*) avec des noms explicites :
`Building_Height`, `Floors`, `Setback`, `Plot_Ratio`… Un slider sans nom reçoit un nom déduit de ce qu'il
alimente (`Extrude.Distance`), utilisable mais moins clair.

Option : préfixez par `IN_` ou regroupez les entrées dans un groupe nommé `INPUTS` / `PARAMÈTRES` /
`ENTRÉES` pour exposer aussi des paramètres flottants vides.

Claude comprend les modifications relatives :

| Demande | Appel |
|---|---|
| « +10 % de hauteur » | `mode: "percent", value: 10` |
| « 2 étages de plus » | `mode: "add", value: 2` |
| « double la largeur » | `mode: "multiply", value: 2` |
| « active les toitures vertes » | `mode: "toggle"` ou `value: true` |

Si la nouvelle valeur sort de la plage du slider, la plage est **étendue** (et signalé), sauf demande
contraire (`on_out_of_range: "clamp"` ou `"error"`).

## 2. Les résultats (sorties)

Désignez les résultats à mesurer de l'une de ces deux façons :

- **nommez le paramètre ou le panel** avec le préfixe `OUT_` : `OUT_GFA`, `OUT_Volume`, `OUT_Buildings` ;
- **ou regroupez-les** dans un groupe nommé `OUTPUTS`, `RESULTS`, `SORTIES`, `RÉSULTATS` ou `MÉTRIQUES`
  (un composant placé dans ce groupe expose toutes ses sorties).

Sans aucune sortie désignée, le connecteur prend les paramètres flottants terminaux et les panels connectés.

Ce que Claude obtient pour chaque sortie :

- **nombres** (ou panel affichant un nombre) → valeur et statistiques min/max/somme/moyenne ; un nombre
  unique devient la métrique `<nom>`, une liste de nombres la métrique `<nom>.sum` ;
- **géométrie** → nombre d'objets, types, longueur/surface/volume totaux, boîte englobante →
  métriques `<nom>.area`, `<nom>.volume`, `<nom>.length`, `<nom>.count` ;
- **textes** → liste.

Toutes les métriques sont enregistrées avec chaque variante et servent aux comparaisons et classements
(« maximise `OUT_GFA`, minimise `OUT_Shadow_Area` »).

## 3. Exemple de définition bien préparée

```
[INPUTS]                          [OUTPUTS]
 Building_Height  (slider 3–60)    OUT_Buildings  ← géométrie des bâtiments
 Floors           (slider 1–20)    OUT_GFA        ← surface de plancher (m²)
 Setback          (slider 0–10)    OUT_Footprint  ← emprise au sol (m²)
 Green_Roof       (toggle)         OUT_Plot_Ratio ← COS
```

## 4. Bonnes pratiques

- Unités : les valeurs sont dans les unités du document Rhino (vérifiez-les avec « Que contient mon modèle ? »).
- Gardez les calculs lourds désactivés (composants *Disable*) si vous n'en avez pas besoin pour les variantes.
- Les variantes sont enregistrées dans `Documents\RhinoMCP\variants\<définition>\V01_…` :
  `variant.json` (paramètres + métriques), `preview.png`, `geometry.3dm`, et `definition.gh` si demandé.
- Pour un cadrage identique de toutes les images de variantes, réglez d'abord la vue Rhino comme vous
  le souhaitez : par défaut, le connecteur conserve la vue courante.
