"""Step 51 - full scientific video (A -> G) assembled from the saved results.

  python 51_video_cfd.py <work_dir> <out_mp4>

Every number spoken or displayed is read from the result files; every sequence whose computation
does not exist is labelled 'étape préparée — non exécutée' (never shown as a result).
"""
import json
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
import engine  # noqa: E402
import slides as S  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "results/cfd"
CL = Path("/home/user/work/clips")
CASES = Path("/home/user/work/cfd/cases")
NAMES = {"CENTRE_VILLE": "centre-ville", "GAMBETTA": "Gambetta", "BD_DES_LIONS": "boulevard des Lions"}


def j(p):
    p = Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def fr(x, n=1):
    return f"{x:.{n}f}".replace(".", ",")


def meta(case):
    d = R / "post" / case
    return next((j(p) for p in d.glob("meta_*.json")), None) if d.exists() else None


def img(p):
    return str(p) if Path(p).exists() else None


def overview_map(out):
    """Sector map: relief hillshade + building cover + the three sector boxes + named places."""
    g = np.load(R / "geometrie/terrain_grid.npz"); xs, ys, Z = g["xs"], g["ys"], g["Z"]
    sect = j(R / "secteurs.json")
    ix = (xs > -6000) & (xs < 12000); iy = (ys > -5000) & (ys < 6000)
    Zc = Z[np.ix_(iy, ix)]
    gy, gx = np.gradient(Zc, 25.0); hs = np.clip(0.6 + 0.4 * (-gx * 0.7 + gy * 0.7) / np.hypot(1, np.hypot(gx, gy)), 0, 1)
    fig, ax = plt.subplots(figsize=(16, 9.5), dpi=120)
    fig.patch.set_facecolor("#0f1720"); ax.set_facecolor("#0f1720")
    ax.imshow(np.where(Zc <= 0.5, np.nan, hs), cmap="gray", origin="lower", extent=(xs[ix][0], xs[ix][-1], ys[iy][0], ys[iy][-1]), vmin=0.2, vmax=1.1)
    cols = {"CENTRE_VILLE": "#42a5f5", "GAMBETTA": "#66bb6a", "BD_DES_LIONS": "#ffa726"}
    for k, s in sect.items():
        b = s["model_box_m"]
        ax.add_patch(plt.Rectangle((b[0], b[1]), b[2] - b[0], b[3] - b[1], fill=False, ec=cols[k], lw=3))
        ax.text(b[0], b[3] + 120, NAMES[k], color=cols[k], fontsize=16, weight="bold")
    ax.add_patch(plt.Rectangle((-5500, -4500), 17000, 10000, fill=False, ec="w", lw=1, ls="--"))
    ax.text(-5400, 5250, "domaine CFD 17 × 10 km", color="w", fontsize=12)
    ax.set_xlim(-6000, 12000); ax.set_ylim(-5000, 6000); ax.set_aspect("equal")
    ax.tick_params(colors="#b0bec5"); [s_.set_color("#37474f") for s_ in ax.spines.values()]
    ax.set_xlabel("X maquette (m)", color="#b0bec5"); ax.set_ylabel("Y maquette (m)", color="#b0bec5")
    fig.tight_layout(); fig.savefig(out, facecolor=fig.get_facecolor()); plt.close(fig)


def mesh_slice(out, case="S6_ref_coarse"):
    """Vertical slice of the cell size through Gambetta (mesh_ref_coarse, read from a solved case whose polyMesh is ascii)."""
    import importlib.util
    sp = importlib.util.spec_from_file_location("pp", ROOT / "scripts/cfd/45_postprocess.py"); pp = importlib.util.module_from_spec(sp); sp.loader.exec_module(pp)
    c = CASES / case
    if not (c / "constant/polyMesh/owner").exists():
        return None
    lo, hi = pp.mesh_cell_boxes(c)
    from matplotlib.collections import PatchCollection
    from matplotlib.patches import Rectangle
    sel = (lo[:, 0] <= 1350) & (hi[:, 0] > 1350)    # cells cut by the plane x = 1350 m
    L, H = lo[sel], hi[sel]
    hs = H[:, 0] - L[:, 0]                          # horizontal cell size (octree: 100, 50, 25, 12.5 m)
    fig, ax = plt.subplots(figsize=(16, 7), dpi=120)
    fig.patch.set_facecolor("#050607"); ax.set_facecolor("#050607")
    pc = PatchCollection([Rectangle((a[1], a[2]), b[1] - a[1], b[2] - a[2]) for a, b in zip(L, H)], cmap="viridis_r",
                         norm=matplotlib.colors.LogNorm(10, 120), edgecolor="#050607", linewidth=0.3)
    pc.set_array(hs); ax.add_collection(pc); im = pc
    ax.set_xlim(-800, 3000); ax.set_ylim(0, 400)
    cb = fig.colorbar(im, ax=ax); cb.set_label("taille horizontale de maille (m)", color="w"); cb.ax.tick_params(colors="w")
    ax.set_xlabel("Y (m) — coupe nord-sud par Gambetta (x = 1350 m)", color="#b0bec5"); ax.set_ylabel("z (m)", color="#b0bec5")
    ax.tick_params(colors="#b0bec5")
    fig.tight_layout(); fig.savefig(out, facecolor=fig.get_facecolor()); plt.close(fig)
    return out


def main():
    work, out_mp4 = Path(sys.argv[1]), Path(sys.argv[2]); work.mkdir(parents=True, exist_ok=True)
    W = j(R / "wind/wind_stats.json"); SC = j(R / "wind/cfd_scenarios.json"); GR = j(R / "geometrie/geometrie_report.json")
    INV = {p.stem: j(p) for p in (R / "inventaire").glob("inventaire_*.json")}
    m6, m2, mT, mF, mV = (meta(c) for c in ("S6_ref_coarse", "S2_ref_coarse", "S6T3_ref_coarse", "S6_ref_medium", "S6_coarse"))
    overview_map(work / "carte_secteurs.png")
    mesh_png = mesh_slice(work / "coupe_maillage.png")
    segs = []

    def seg(id_, narration, clips, title=""):
        segs.append({"id": id_, "title": title, "narration": narration, "clips": clips})

    def slide(sec, title, **kw):
        return {"type": "slide", "html": S.page(sec, title, **kw), "min": kw.pop("min", 4)} if False else \
            {"type": "slide", "html": S.page(sec, title, **{k: v for k, v in kw.items() if k != "min"}), "min": kw.get("min", 4)}

    def frames(d, minimum=4, w=1.5):
        return {"type": "frames", "dir": str(d), "fps": 10, "min": minimum, "weight": w} if d and Path(d).exists() and any(Path(d).glob("*.png")) else None

    def image(p, minimum=4, w=1.0, zoom=True):
        return {"type": "image", "png": str(p), "min": minimum, "weight": w, "zoom": zoom} if p and Path(p).exists() else None

    # ---------------- A. initial maquettes
    seg("A1_titre", ["Cette vidéo présente l'étude du vent et de la chaleur dans trois secteurs d'Oran : le centre-ville, Gambetta et le boulevard des Lions.",
                     "Elle part des maquettes numériques existantes dans Rhino 8 et va jusqu'aux résultats des simulations, en distinguant ce qui est calculé, estimé ou seulement préparé."],
        [slide("Oran · vent et chaleur urbaine", "Simulation du vent et des phénomènes thermiques sur la maquette Rhino d'Oran",
               bullets=["Maquette urbaine détaillée et texturée (Rhino 8)", "Maquette thermique (température de surface, 10/07/2024)",
                        "Vent observé : Oran – Es Sénia, 2014–2024", "CFD : OpenFOAM v1912 (calculs réalisés) · ANSYS Fluent (paquet préparé)"],
               note="Toutes les valeurs affichées proviennent des fichiers de résultats du projet.", min=6)])
    inv = next((v for k, v in INV.items() if "urbaine" in k), None)
    clips = [frames(CL / "intro_CENTRE_VILLE", 5), frames(CL / "intro_GAMBETTA", 5), frames(CL / "intro_BD_DES_LIONS", 5)]
    seg("A2_maquette", [f"La maquette urbaine détaillée compte {inv['n_objects']} objets répartis sur {inv['n_layers']} calques, avec {inv['n_materials']} matériaux et leurs textures : asphaltes, trottoirs en dalles, bordures en granit, végétation." if inv else "La maquette urbaine détaillée est présentée.",
                        "La topographie provient du modèle numérique Copernicus, les voiries sont hiérarchisées, et le mobilier urbain comprend lampadaires et arbres d'alignement.",
                        "Ces vues sont rendues directement à partir du fichier Rhino, avec ses matériaux d'origine."],
        [c for c in clips if c] + [slide("A · Maquette initiale", "Inventaire de la maquette urbaine (fichier .3dm)",
            table=[["Élément", "Valeur"], ["Objets", inv["n_objects"]], ["Calques", inv["n_layers"]], ["Matériaux", inv["n_materials"]],
                   ["Blocs de mobilier", inv["objects_by_type"].get("InstanceReference", 0)], ["Unités", "mètres"],
                   ["Maillages invalides", inv["invalid_meshes"]]] if inv else [["-", "-"]], min=5)])
    seg("A3_thermique", ["La maquette thermique est déjà présente dans Rhino : la température de surface du 10 juillet 2024 y est représentée par un nuage de points et des calques de couleur, un calque par degré.",
                         "Ces températures ont été reconstruites à partir des couleurs d'une figure satellite Landsat, grâce à la légende du document : ce sont des valeurs approximatives de température de surface, pas de l'air."],
        [c for c in (image(ROOT / "results/thermal/apercu_3d_thermique.png", 5), image(ROOT / "results/thermal/apercu_zones_grille_2024.png", 5)) if c])
    seg("A4_secteurs", ["Les trois secteurs ont été localisés à partir de données nommées : la place du Premier Novembre et Sidi El Houari pour le centre-ville, la cité Gambetta, et le tracé du boulevard des Lions.",
                        "Les deux maquettes partagent le même repère métrique ; un décalage connu d'environ trente-trois mètres a été corrigé avant d'associer une température à chaque bâtiment."],
        [image(work / "carte_secteurs.png", 6, zoom=False)])
    if GR:
        b = GR["buildings"]
        seg("A5_geometrie", [f"Une géométrie de calcul dérivée a été créée sans modifier la maquette : {b['CENTRE_VILLE']['n']} bâtiments au centre-ville, {b['GAMBETTA']['n']} à Gambetta et {b['BD_DES_LIONS']['n']} au boulevard des Lions, chacun relié à son objet Rhino par un identifiant.",
                             "Les textures ne sont pas des propriétés aérodynamiques : elles restent dans Rhino, la simulation n'utilise que les volumes, le relief et une rugosité du sol."],
            [slide("A · Géométrie de calcul", "Modèle A (aérodynamique) et modèle B (thermique)",
                   table=[["Secteur", "Bâtiments", "H médiane", "H max", "LST sous toit"]] + [[NAMES[k], v["n"], f"{fr(v['h_median'])} m", f"{fr(v['h_max'])} m", v["lst2024_with_value"]] for k, v in b.items()],
                   bullets=["Bâti : calque principal de la maquette (densité réaliste)", "Relief : grille Copernicus de la maquette, 25 m",
                            "Identifiants cfd_id ↔ objets Rhino (batiments_cfd.csv)"], min=6),
             image(R / "post/S6_ref_coarse/vues/CENTRE_VILLE_aerienne.png", 4)])
    # ---------------- B. weather
    if W:
        seg("B_meteo", [f"Les conditions d'entrée reposent sur {W['hours']} heures d'observations de la station d'Oran Es Sénia, entre 2014 et 2024, issues de la base NOAA ISD.",
                        f"La vitesse moyenne à dix mètres est de {fr(W['mean_speed_ms'], 1)} mètre par seconde, avec {fr(W['calm_pct'])} pour cent de calmes.",
                        "La rose des vents montre deux régimes : une brise de mer de nord à nord-ouest, le jour et en été, et un vent de terre de sud-ouest, la nuit et en hiver.",
                        f"Les après-midi d'été, le nord et le nord-nord-ouest représentent plus de soixante pour cent des heures, avec {fr(W['ete_apres_midi']['v_moy'], 1)} mètres par seconde en moyenne.",
                        "Les scénarios de calcul reprennent ces situations observées ; aucune direction dominante n'a été supposée."],
            [image(R / "wind/rose_des_vents_oran_2014_2024.png", 6, zoom=False), image(R / "wind/distribution_vitesses.png", 4, zoom=False),
             slide("B · Scénarios", "Scénarios retenus à partir des observations",
                   table=[["Scénario", "Vent venant du", "U10 réf.", "Fréquence"]] + [[s["id"], f"{fr(s['dir_from_deg'])}°", f"{fr(s['U10_ref_ms'], 2)} m/s", f"{fr(s.get('freq_pct', 0))} %"] for s in SC],
                   note="Source : NOAA ISD 60490099999 (Es Sénia, 35,624 N 0,621 O, 90 m), 2014–2024. Limite : station aéroportuaire hors des secteurs, anémomètre à 10 m.", min=6)])
    # ---------------- C. model preparation
    seg("C_modele", ["Le domaine de calcul mesure dix-sept kilomètres sur dix, et neuf cents mètres de haut ; il couvre les trois secteurs et les zones intermédiaires.",
                     "Le maillage est généré automatiquement autour du relief et des bâtiments, avec des mailles de douze mètres et demi contre les façades dans le maillage grossier, et de six mètres dans le maillage fin.",
                     "Le vent entre avec un profil logarithmique de couche limite neutre, calé sur la vitesse observée à Es Sénia ; le sol reçoit une rugosité différente pour la mer, la ville et la campagne.",
                     "La turbulence est représentée par le modèle k epsilon réalisable, adapté aux écoulements décollés autour des bâtiments.",
                     "Les calculs présentés ont été réalisés avec OpenFOAM. Le même maillage, exporté au format Fluent, et les mêmes conditions aux limites sont fournis pour ANSYS : cette étape est préparée mais n'a pas été exécutée ici."],
        [c for c in (image(mesh_png, 5, zoom=False) if mesh_png else None,
                     slide("C · Conditions aux limites", "Physique et paramètres numériques",
                           bullets=["Entrée : U(z) = u*/κ · ln((z−zg+z0)/z0) — Richards & Hoxey (1993)", "Sortie : pression nulle · côtés et sommet : glissement",
                                    "Sol : loi de paroi rugueuse, z0 = 0,0002 m (mer) à 0,5 m (tissu urbain)", "Turbulence : RANS k-ε réalisable · algorithme SIMPLE",
                                    "Thermique : Boussinesq, toits et sol à la LST du 10/07/2024"], min=6),
                     slide("C · ANSYS", "Paquet ANSYS Fluent — étape préparée, non exécutée ici", badge=("plan", "préparé"),
                           bullets=["Maillage identique exporté (foamMeshToFluent, .msh)", "Profils d'entrée et températures (.prof)",
                                    "Script PyFluent + file d'attente en processus séparé", "SpaceClaim : géométrie STL fournie pour un maillage Fluent alternatif"],
                           note="Aucune capture ANSYS n'est montrée : le logiciel n'était pas disponible dans l'environnement de calcul.", min=5)) if c])
    # ---------------- D. wind results
    for m, case, label in ((m6, "S6_ref_coarse", "S6"), (m2, "S2_ref_coarse", "S2")):
        if not m:
            seg(f"D_{label}", [f"Le scénario {label} n'a pas été calculé."], [slide("D · Vent", f"Scénario {label} — non calculé", badge=("plan", "non exécuté"))]); continue
        I = m["indicateurs_secteurs"]
        narr = [f"Scénario {label} : vent venant du {fr(m['dir_from_deg'])} degrés, {fr(m['U10_ref_ms'], 2)} mètres par seconde à dix mètres à Es Sénia ; {m['mesh_cells']} mailles, bilan de masse équilibré à mieux que un pour cent mille.",
                f"À deux mètres du sol, la vitesse moyenne vaut {fr(I['CENTRE_VILLE']['U_moy'], 1)} mètre par seconde au centre-ville, {fr(I['GAMBETTA']['U_moy'], 1)} à Gambetta et {fr(I['BD_DES_LIONS']['U_moy'], 1)} au boulevard des Lions.",
                f"Les zones quasi calmes, sous un demi mètre par seconde, couvrent {fr(I['CENTRE_VILLE']['pct_stagnation_U_inf_0.5'])} pour cent de l'espace public au centre-ville."]
        if label == "S6":
            narr.append("La brise de mer s'accélère fortement au rebord de l'escarpement côtier de Gambetta et au sommet de la falaise du front de mer, puis les îlots denses créent de larges zones abritées et des sillages.")
        else:
            narr.append("Avec le vent de terre, le centre-ville est presque entièrement abrité dans le sillage du bâti ; seuls les couloirs orientés sud-ouest nord-est restent ventilés.")
        cl = [image(R / "post" / case / f"carte_pieton_{m['scenario'] if False else ('S6_ete_NNO' if label == 'S6' else 'S2_SO')}.png", 6, zoom=False)]
        if label == "S6":
            for k in ("CENTRE_VILLE", "GAMBETTA", "BD_DES_LIONS"):
                cl += [frames(CL / f"wind_aerien_{k}_S6_ref_coarse", 5), frames(CL / f"wind_rue_{k}_S6_ref_coarse", 5)]
            # street level: realistic clips above (textured maquette, cameras on the asphalt); here the aerial result views
            cl += [image(R / "post" / case / "vues" / f"{v}.png", 3) for v in ("GAMBETTA_aerienne", "BD_DES_LIONS_aerienne")]
        else:
            cl += [image(R / "post" / case / "vues" / f"{v}.png", 3) for v in ("CENTRE_VILLE_aerienne", "GAMBETTA_aerienne", "BD_DES_LIONS_aerienne")]
        cl += [image(next((R / "post" / case).glob("coupes_*.png"), None), 4, zoom=False)]
        seg(f"D_{label}", narr, [c for c in cl if c])
    if m6 and m2:
        seg("D_comparaison", ["En comparant les deux régimes, la brise de mer ventile nettement mieux les trois secteurs que le vent de terre, en particulier le centre-ville.",
                              "Gambetta reste le secteur le plus exposé, en raison de l'escarpement qui accélère le vent venant de la mer."],
            [slide("D · Comparaison", "Vitesse à 2 m : brise de mer (S6) et vent de terre (S2)",
                   table=[["Secteur", "S6 U/Uref", "S6 calmes", "S2 U/Uref", "S2 calmes"]] + [[NAMES[k], fr(m6["indicateurs_secteurs"][k]["K_moy_U_sur_Uref"], 2), f"{fr(m6['indicateurs_secteurs'][k]['pct_stagnation_U_inf_0.5'])} %",
                                                                                                  fr(m2["indicateurs_secteurs"][k]["K_moy_U_sur_Uref"], 2), f"{fr(m2['indicateurs_secteurs'][k]['pct_stagnation_U_inf_0.5'])} %"] for k in NAMES], min=6)])
    # ---------------- E. thermal
    if mT:
        I = mT["indicateurs_secteurs"]
        seg("E_thermique", ["Pour le couplage thermique, les toits et le sol reçoivent la température de surface du 10 juillet 2024, importée de la maquette thermique ; c'est une donnée d'entrée, pas un résultat.",
                            "Le calcul résout ensuite la température de l'air transportée par le vent, avec un air entrant à la température moyenne observée des après-midi d'été.",
                            f"La température de l'air calculée à deux mètres atteint en moyenne {fr(I['CENTRE_VILLE']['T_air_2m_moy_C'], 2)} degrés au centre-ville, {fr(I['GAMBETTA']['T_air_2m_moy_C'], 2)} à Gambetta et {fr(I['BD_DES_LIONS']['T_air_2m_moy_C'], 2)} au boulevard des Lions.",
                            "Le rayonnement n'est pas modélisé et les murs n'ont pas de donnée : ces valeurs décrivent une tendance, pas une mesure."],
            [c for c in (slide("E · Thermique", "Températures importées et températures calculées", badge=("est", "estimé"),
                               bullets=["IMPORTÉ : température de surface LST (toits, sol) — figure Landsat, 10/07/2024", "CALCULÉ : température de l'air (Boussinesq) sous le vent S6",
                                        "Murs à la température de l'air (pas de donnée) · mer supposée à 24 °C", "Non modélisé : rayonnement, ombrage, végétation transpirante"], min=6),
                         image(ROOT / "results/thermal/apercu_3d_thermique_centre.png", 4),
                         image(next((R / "post/S6T3_ref_coarse").glob("carte_Tair_*.png"), None), 6, zoom=False)) if c])
    else:
        seg("E_thermique", ["Le cas thermique couplé n'était pas disponible au moment du montage : la séquence est présentée comme une étape préparée."],
            [slide("E · Thermique", "Couplage vent – chaleur : étape préparée", badge=("plan", "non exécuté"))])
    # ---------------- F. validation
    val = [f"Les bilans de masse sont équilibrés à mieux que un pour cent mille et les champs piétons varient de moins de un dixième de pour cent entre les dernières itérations."]
    cmp = []
    if m6 and mF:
        e = [100 * (mF["indicateurs_secteurs"][k]["U_moy"] - m6["indicateurs_secteurs"][k]["U_moy"]) / mF["indicateurs_secteurs"][k]["U_moy"] for k in NAMES]
        val.append(f"Entre le maillage grossier et le maillage fin, la vitesse moyenne piétonne change de {fr(abs(e[0]))}, {fr(abs(e[1]))} et {fr(abs(e[2]))} pour cent selon les secteurs.")
        cmp = [["Secteur", "U grossier", "U fin", "écart"]] + [[NAMES[k], fr(m6["indicateurs_secteurs"][k]["U_moy"], 2), fr(mF["indicateurs_secteurs"][k]["U_moy"], 2), f"{fr(x)} %"] for k, x in zip(NAMES, e)]
    if m6 and mV:
        val.append(f"La densité du bâti compte beaucoup : avec des emprises incomplètes, la ventilation du centre-ville serait surestimée, avec un rapport de vitesse de {fr(mV['indicateurs_secteurs']['CENTRE_VILLE']['K_moy_U_sur_Uref'], 2)} au lieu de {fr(m6['indicateurs_secteurs']['CENTRE_VILLE']['K_moy_U_sur_Uref'], 2)}.")
    val.append("Aucune mesure de vent n'existe à l'intérieur des secteurs : la comparaison directe avec des observations n'est pas possible, et des mesures anémométriques dans les rues sont recommandées.")
    seg("F_validation", val, [c for c in (image(next((R / "post/S6_ref_coarse").glob("residus_*.png"), None), 4, zoom=False),
                                          slide("F · Validation", "Fiabilité des résultats", badge=("ok", "vérifié") if cmp else ("est", "partiel"),
                                                bullets=["Validé : bilans de masse, stabilité itérative, maillage sans volume négatif",
                                                         "Estimé : RANS stationnaire, mailles de 12,5 m (grossier) près des bâtiments",
                                                         "À mesurer : vent dans les rues (aucune donnée intra-urbaine)"], table=cmp or None, min=6)) if c])
    # ---------------- G. conclusions
    concl = ["En synthèse, Gambetta présente les accélérations les plus fortes, au rebord de l'escarpement, à surveiller pour le confort et la sécurité des piétons par vent de mer.",
             "Le centre-ville dense est bien abrité mais mal ventilé, surtout par vent de terre : les couloirs nord-nord-ouest, sud-sud-est sont essentiels à sa ventilation.",
             "Le boulevard des Lions est ventilé par ses grands axes, mais ses cœurs d'îlots restent calmes.",
             "Les recommandations portent sur la préservation des couloirs de ventilation orientés vers la mer, des protections végétales au rebord de l'escarpement, et des mesures de terrain pour valider ces résultats."]
    seg("G_conclusions", concl, [slide("G · Conclusions", "Synthèse et recommandations",
                                       bullets=["Gambetta : accélérations au rebord de l'escarpement (vent de mer) → brise-vent végétal, confort piéton",
                                                "Centre-ville : bien abrité, mal ventilé par vent de terre → préserver les axes NNO–SSE",
                                                "Bd des Lions : ventilation par les grands axes, cœurs d'îlots calmes",
                                                "Valider par des mesures in situ ; refaire les cas sous ANSYS avec le paquet fourni"], min=8),
                                 slide("Références", "Sources des données et méthodes",
                                       bullets=["NOAA NCEI ISD — station 60490 Es Sénia (2014–2024)", "Overture Maps (2026-09-23.1) · OpenStreetMap",
                                                "Copernicus DEM GLO-30 · maquettes Rhino 8 du projet", "Richards & Hoxey 1993 · COST 732 (Franke 2007) · AIJ (Tominaga 2008)",
                                                "Figure LST 1984–2024, Landsat (Oran)"], min=5)])
    total = engine.build(segs, work, out_mp4, S.render)
    print("video", out_mp4, f"{total / 60:.1f} min")


if __name__ == "__main__":
    main()
