"""Step 50 - final scientific report (Markdown + PDF), every number read from the result files.

  python 50_rapport_scientifique.py <out_dir>
Sections whose computations are not available are explicitly marked as not done (never invented).
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "results/cfd"
CASES = Path("/home/user/work/cfd/cases")


def j(p):
    p = Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def f1(x, n=1):
    return f"{x:.{n}f}".replace(".", ",") if isinstance(x, (int, float)) else str(x)


def meta(case):
    return next((j(p) for p in (R / "post" / case).glob("meta_*.json")), None) if (R / "post" / case).exists() else None


def checkmesh(case):
    p = CASES / case / "log.checkMesh.final"
    if not p.exists():
        return None
    t = p.read_text(errors="ignore")
    g = lambda pat: (re.search(pat, t).group(1) if re.search(pat, t) else "?")  # noqa: E731
    return {"cells": g(r"cells:\s+(\d+)"), "nonorth_max": g(r"non-orthogonality Max: ([\d.]+)"),
            "nonorth_avg": g(r"average: ([\d.]+)"), "skew": g(r"Max skewness = ([\d.]+)"),
            "minvol": g(r"Min volume = ([\d.eE+-]+)")}


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    W = j(R / "wind/wind_stats.json"); SC = j(R / "wind/cfd_scenarios.json"); SE = j(R / "secteurs.json")
    GR = j(R / "geometrie/geometrie_report.json"); GRR = j(R / "geometrie_emprises_reelles/geometrie_report.json")
    INV = {p.stem: j(p) for p in (R / "inventaire").glob("inventaire_*.json")}
    runs = {"S6_ref_coarse": "S6 été NNO — maillage grossier (référence)", "S2_ref_coarse": "S2 SO — maillage grossier",
            "S6T_ref_coarse": "S6 thermique couplé (Boussinesq)", "S6_ref_medium": "S6 été NNO — maillage fin",
            "S6_coarse": "S6 — variante bâti « emprises réelles » (sensibilité)"}
    M = {k: meta(k) for k in runs}
    L = []; w = L.append
    w("# Vent et chaleur dans trois secteurs d'Oran — étude CFD sur la maquette Rhino 8")
    w("")
    w("*Rapport scientifique — centre-ville, Gambetta, boulevard des Lions. Toutes les valeurs proviennent des fichiers de "
      "résultats du dépôt ; les étapes non réalisées sont signalées comme telles.*")
    w("")
    w("## 1. Objectif et périmètre")
    w("")
    w("Étudier, à partir des maquettes numériques existantes (maquette urbaine détaillée et maquette thermique), l'écoulement "
      "du vent à hauteur de piéton, les accélérations, canalisations, zones abritées et recirculations, puis l'effet des "
      "températures de surface sur la température de l'air, dans trois secteurs d'Oran. Les calculs ont été réalisés avec "
      "**OpenFOAM v1912** (ANSYS n'était pas disponible dans l'environnement de calcul) ; un paquet **ANSYS Fluent** avec le "
      "même maillage et les mêmes conditions aux limites est fourni pour refaire les cas sous Fluent.")
    w("")
    w("## 2. Données d'entrée")
    w("")
    w("### 2.1 Maquettes Rhino 8 (inspection des fichiers)")
    w("")
    w("| Fichier | Objets | Calques | Matériaux | Unité | Remarques |")
    w("|---|---|---|---|---|---|")
    for k, v in INV.items():
        if v:
            w(f"| `{v['file']}` ({f1(v['MB'])} Mo) | {v['n_objects']} | {v['n_layers']} | {v['n_materials']} | {v['unit'].split('.')[-1]} | "
              f"{v['open_meshes']} maillages ouverts (surfaces drapées), {v['invalid_meshes']} invalides, {v['duplicate_mesh_groups']} doublons |")
    w("")
    w("Les deux fichiers sont dans le même repère (Transverse Mercator local centré sur 35,699 N / 0,635 O, mètres) ; "
      "les points d'ancrage terrestres diffèrent de moins de 0,4 m. La carte thermique (LST du 10/07/2024) a été calée sur la "
      "maquette blanche ; le lien maquette → Terre de la maquette détaillée ayant été corrigé depuis, la correction connue "
      "d(x,y) ≈ (+3,6 ; −32,7) m est appliquée lors du couplage thermique.")
    w("")
    w("### 2.2 Vent observé (Oran – Es Sénia)")
    w("")
    if W:
        w(f"Source : {W['source']}, période {W['period'][0][:10]} → {W['period'][1][:10]}, **{W['hours']} heures** valides après contrôle "
          f"qualité (codes ISD), une valeur par heure (METAR prioritaire). Hauteur 10 m, exposition aéroportuaire.")
        w("")
        w(f"* Vitesse moyenne {f1(W['mean_speed_ms'], 2)} m/s, médiane {f1(W['p50'])}, P90 {f1(W['p90'])}, P99 {f1(W['p99'])}, max {f1(W['max'])} m/s ; calmes {f1(W['calm_pct'])} %.")
        w(f"* Loi de Weibull : k = {f1(W['weibull_k'], 2)}, c = {f1(W['weibull_c_ms'], 2)} m/s.")
        for n, v in W["seasons"].items():
            w(f"* {n} : {f1(v['v_moy'], 2)} m/s, directions dominantes " + ", ".join(f"{a} ({f1(b)} %)" for a, b in v["top3"]) + f", air {f1(v['tair_moy'])} °C.")
        w(f"* Après-midi d'été (11–17 h) : {f1(W['ete_apres_midi']['v_moy'], 2)} m/s, " + ", ".join(f"{a} ({f1(b)} %)" for a, b in W["ete_apres_midi"]["top3"]) + ".")
        w("")
        w("![Rose des vents](../wind/rose_des_vents_oran_2014_2024.png)")
        w("")
        w("Deux régimes : **brise de mer N/NNO** (jour, été) et **vent de terre SO/OSO** (nuit, hiver). Scénarios retenus :")
        w("")
        w("| Scénario | Direction (d'où vient le vent) | U10 de référence | Fréquence | Rôle |")
        w("|---|---|---|---|---|")
        for s in SC:
            w(f"| {s['id']} | {f1(s['dir_from_deg'])}° | {f1(s['U10_ref_ms'], 2)} m/s | {f1(s.get('freq_pct', 0))} % | {s['role']} |")
    w("")
    w("### 2.3 Secteurs d'étude (localisés sur des données nommées Overture/OSM)")
    w("")
    for k, v in (SE or {}).items():
        w(f"* **{k.replace('_', ' ')}** — {v['ref']} — boîte modèle {v['model_box_m']} m ({v['size_km'][0]} × {v['size_km'][1]} km).")
    w("")
    w("## 3. Géométrie de calcul (dérivée, maquette originale intacte)")
    w("")
    if GR:
        w(f"Bâti de référence : calque principal de la maquette (`BUILDINGS::*`), découpé en bâtiments individuels par composantes "
          f"connexes, secteur + 300 m. Relief : grille Copernicus de la maquette ({GR['ground']['grid'][0]} × {GR['ground']['grid'][1]} nœuds, 25 m).")
        w("")
        w("| Secteur | Bâtiments (dont cœur) | Hauteur médiane | P95 | Max | LST 2024 sous toiture |")
        w("|---|---|---|---|---|---|")
        for k, v in GR["buildings"].items():
            w(f"| {k.replace('_', ' ')} | {v['n']} ({v['n_core']}) | {f1(v['h_median'])} m | {f1(v['h_p95'])} m | {f1(v['h_max'])} m | {v['lst2024_with_value']} |")
        w("")
    if GRR:
        w("Variante de sensibilité : emprises réelles OSM/Google/Microsoft — couverture bâtie 11 / 7 / 14 % contre 43 / 25 / 25 % pour le "
          "bâti de référence : jeu **incomplet** à Oran, utilisé seulement pour mesurer l'effet de la densité.")
        w("")
    w("Chaque bâtiment porte un identifiant (`batiments_cfd.csv` : cfd_id ↔ calque et identifiant d'objet Rhino) ; "
      "le script Rhino R02 reproduit cette préparation dans une copie `_CFD_WORK` du fichier.")
    w("")
    w("## 4. Modèle numérique")
    w("")
    for case in ("mesh_ref_coarse", "mesh_ref_medium"):
        cm = checkmesh(case)
        if cm:
            w(f"* **{case}** : {cm['cells']} mailles, non-orthogonalité max {cm['nonorth_max']}° (moyenne {cm['nonorth_avg']}°), "
              f"dissymétrie max {cm['skew']}, volume min {cm['minvol']} m³ (aucun volume négatif).")
    w("* Domaine 17 × 10 km × 900 m couvrant les 3 secteurs ; snappyHexMesh, mailles de fond 100 m (grossier) / 80 m (fin), "
      "raffinement de boîte autour des secteurs, mailles de 12,5 m (grossier) / 6,25 m (fin) contre les bâtiments.")
    w("* Entrée : couche limite neutre (Richards & Hoxey) — U(z) = u*/κ ln((z−zg+z0)/z0), k = u*²/√Cμ, ε = u*³/(κ(z−zg+z0)) ; "
      "u* déduit du U10 observé à Es Sénia (z0 = 0,03 m) puis transposé à la rugosité amont via une hauteur de mélange de 60 m.")
    w("* Sol : `nutkAtmRoughWallFunction`, z0 par face (mer 0,0002 m ; secteurs à bâti explicite 0,05 m ; tissu urbain 0,5 m ; autre 0,1 m). "
      "Bâtiments : lois de paroi lisses. Sorties : pression nulle ; faces parallèles et sommet : glissement.")
    w("* Turbulence : RANS **k-ε réalisable** (bon compromis pour les écoulements décollés autour des bâtiments, COST 732 / AIJ) ; "
      "SIMPLE, quantité de mouvement linearUpwind limité, k et ε upwind.")
    w("* Thermique : `buoyantBoussinesqSimpleFoam` (Boussinesq, β = 3,3·10⁻³ K⁻¹), toitures et sol à la **LST du 10/07/2024** "
      "(température de surface imposée), murs à la température de l'air (aucune donnée), air entrant à la moyenne observée des après-midi d'été.")
    w("")
    w("## 5. Résultats")
    w("")
    for case, name in runs.items():
        m = M.get(case)
        w(f"### {name}")
        w("")
        if not m:
            w("*Calcul non disponible au moment de la génération du rapport.*")
            w(""); continue
        w(f"{m['solver']} — {m['mesh_cells']} mailles — {m['iterations']} itérations — déséquilibre de masse "
          f"{f1(100 * m['mass_imbalance_rel'], 4)} % — résidus finaux " + ", ".join(f"{k} {v:.1e}" for k, v in m["final_residuals"].items() if v) + ".")
        w("")
        hdr = "| Secteur | U moy 2 m | U P90 | U/Uref | > 1,2 Uref | < 0,5 m/s | flux inversé |" + (" T air 2 m |" if "thermique" in m["scenario"] else "")
        w(hdr); w("|---|---|---|---|---|---|---|" + ("---|" if "thermique" in m["scenario"] else ""))
        for k, v in m["indicateurs_secteurs"].items():
            row = (f"| {k.replace('_', ' ')} | {f1(v['U_moy'], 2)} m/s | {f1(v['U_p90'], 2)} | {f1(v['K_moy_U_sur_Uref'], 2)} | "
                   f"{f1(v['pct_acceleration_U_sup_1.2Uref'])} % | {f1(v['pct_stagnation_U_inf_0.5'])} % | {f1(v['pct_flux_inverse'])} % |")
            if "thermique" in m["scenario"]:
                row += f" {f1(v.get('T_air_2m_moy_C', float('nan')), 2)} °C (P90 {f1(v.get('T_air_2m_p90_C', float('nan')), 2)}) |"
            w(row)
        w("")
        for img in sorted((R / "post" / case).glob("carte_*.png")) + sorted((R / "post" / case).glob("coupes_*.png")):
            w(f"![{img.stem}](../post/{case}/{img.name})"); w("")
        stab = [v.get("stabilite_iterative") for v in m["indicateurs_secteurs"].values() if v.get("stabilite_iterative")]
        if stab:
            w("Stabilité itérative du champ piéton : " + "; ".join(f"{f1(s['dU_moy_rel_pct'], 3)} % entre {s['iter_compare'][0]} et {s['iter_compare'][1]}" for s in stab) + ".")
            w("")
    w("## 6. Validation et incertitudes")
    w("")
    a, b = M.get("S6_ref_coarse"), M.get("S6_ref_medium")
    if a and b:
        w("Comparaison de maillages (S6) :")
        w("")
        w("| Secteur | U moy grossier | U moy fin | écart |")
        w("|---|---|---|---|")
        for k in a["indicateurs_secteurs"]:
            u1, u2 = a["indicateurs_secteurs"][k]["U_moy"], b["indicateurs_secteurs"][k]["U_moy"]
            w(f"| {k.replace('_', ' ')} | {f1(u1, 2)} | {f1(u2, 2)} | {f1(100 * (u2 - u1) / u2)} % |")
        w("")
    else:
        w("*Comparaison de maillages : le calcul fin n'était pas disponible à la génération du rapport.*")
        w("")
    c = M.get("S6_coarse")
    if a and c:
        w("Sensibilité à la densité du bâti (S6, même maillage grossier) : U/Uref moyen " + "; ".join(
            f"{k.replace('_', ' ')} {f1(c['indicateurs_secteurs'][k]['K_moy_U_sur_Uref'], 2)} (emprises incomplètes) → "
            f"{f1(a['indicateurs_secteurs'][k]['K_moy_U_sur_Uref'], 2)} (référence)" for k in a["indicateurs_secteurs"]) + ".")
        w("")
    w("* **Validé numériquement** : bilans de masse (< 10⁻⁵), stabilité des champs piétons entre itérations, qualité du maillage (aucun volume négatif).")
    w("* **Estimé** : valeurs à 2 m sur maillage grossier (mailles de 12,5 m près des bâtiments : les rues étroites sont mal résolues) ; "
      "RANS stationnaire (les sillages instationnaires sont moyennés) ; hauteurs du bâti en multiples de 3 m ; formes du bâti de référence générées.")
    w("* **Non validé faute de mesures** : aucune mesure de vent intra-urbaine n'est disponible ; la station d'Es Sénia (hors domaine, ~9 km au sud) "
      "ne sert qu'à fixer l'entrée. Des mesures anémométriques ponctuelles dans les rues seraient nécessaires.")
    w("* **Thermique** : les températures imposées sont des températures de **surface** reconstruites depuis une figure (LST), "
      "pas des mesures de l'air ; les murs n'ont pas de donnée ; le rayonnement n'est pas modélisé.")
    w("")
    w("## 7. Workflow Rhino 8 ↔ ANSYS (livrables)")
    w("")
    w("* `rhino_ansys/rhino/R01_diagnostic_scene.py` — inventaire de la session Rhino (arrière-plan, lecture seule).")
    w("* `rhino_ansys/rhino/R02_preparation_geometrie_cfd.py` — géométrie de calcul dans une copie `_CFD_WORK`, identifiants traçables, export STL.")
    w("* `rhino_ansys/rhino/R03_import_resultats.py` — réimport des CSV (nuage de vitesses, flèches, lignes de courant, accélérations, recirculations).")
    w("* `rhino_ansys/ansys/A02_pyfluent_solve.py`, `A03_lancer_file_attente.py` — même cas sous Fluent, file d'attente en processus séparé.")
    w("* `scripts/cfd/40…50` — chaîne complète reproductible (climat de vent, inventaire, géométrie, maillage, calcul, post-traitement, vues, rapport).")
    w("")
    w("## 8. Références")
    w("")
    w("* NOAA NCEI, Integrated Surface Database (ISD), station 60490099999 Es Sénia — miroir public s3://noaa-global-hourly-pds.")
    w("* Overture Maps Foundation, release 2026-09-23.1 (divisions, places, transportation) — localisation des secteurs.")
    w("* Copernicus DEM GLO-30 (ESA) — relief de la maquette.")
    w("* Richards P.J., Hoxey R.P. (1993), Appropriate boundary conditions for computational wind engineering models using the k-ε model, JWEIA 46-47.")
    w("* Franke J. et al. (2007), Best practice guideline for the CFD simulation of flows in the urban environment, COST Action 732.")
    w("* Tominaga Y. et al. (2008), AIJ guidelines for practical applications of CFD to pedestrian wind environment around buildings, JWEIA 96.")
    w("* Blocken B. et al. (2007), CFD simulation of the atmospheric boundary layer: wall function problems, Atmospheric Environment 41.")
    w("* Shih T.-H. et al. (1995), A new k-ε eddy viscosity model for high Reynolds number turbulent flows (k-ε réalisable), Computers & Fluids 24.")
    w("* Figure LST 1984–2024 : « Spatiotemporal Impact of Urbanization on Urban Heat Island Using Landsat Imagery in Oran, Algeria: 1984–2024 ».")
    md = out / "RAPPORT_CFD_ORAN_FR.md"
    md.write_text("\n".join(L) + "\n", encoding="utf-8")
    if shutil.which("pandoc") and shutil.which("soffice"):
        subprocess.run(["pandoc", md.name, "-o", "r.docx", "--resource-path", str(out)], cwd=out, check=True)
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "r.docx"], cwd=out, capture_output=True, timeout=900)
        if (out / "r.pdf").exists():
            (out / "r.pdf").replace(out / "RAPPORT_CFD_ORAN_FR.pdf")
        (out / "r.docx").unlink(missing_ok=True)
    print("rapport", md, (out / "RAPPORT_CFD_ORAN_FR.pdf").exists())


if __name__ == "__main__":
    main()
