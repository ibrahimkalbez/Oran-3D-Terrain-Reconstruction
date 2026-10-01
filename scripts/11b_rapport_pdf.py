"""PDF report 'Oran 3D - methodes de calcul et precision' (black / grey / red,
after Bernard Tschumi's graphic language). Figures from 11a_figures_rapport.py,
numbers read from the JSON reports produced by the pipeline.
Output: documentation/Rapport_Oran3D_KALBEZ_Ibrahim_El_Khalil.pdf"""
import json
import math
import re
import os
from pathlib import Path

import matplotlib
from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, NextPageTemplate,
                                PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle)

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "documentation/figures"
OUT = ROOT / "documentation/Rapport_Oran3D_KALBEZ_Ibrahim_El_Khalil.pdf"
RED, BLACK, G1, G2, G3 = HexColor("#E30613"), HexColor("#111111"), HexColor("#4d4d4d"), HexColor("#9a9a9a"), HexColor("#e6e6e6")
AUTHOR, DIRECTOR = "KALBEZ Ibrahim El Khalil", "Monsieur IBKA"
W, H = A4

fd = os.path.join(matplotlib.get_data_path(), "fonts/ttf")
pdfmetrics.registerFont(TTFont("Sans", os.path.join(fd, "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("Sans-B", os.path.join(fd, "DejaVuSans-Bold.ttf")))
pdfmetrics.registerFont(TTFont("Sans-I", os.path.join(fd, "DejaVuSans-Oblique.ttf")))
pdfmetrics.registerFont(TTFont("Mono", os.path.join(fd, "DejaVuSansMono.ttf")))
from reportlab.pdfbase.pdfmetrics import registerFontFamily  # noqa: E402
registerFontFamily("Sans", normal="Sans", bold="Sans-B", italic="Sans-I", boldItalic="Sans-B")

S = {
    "body": ParagraphStyle("body", fontName="Sans", fontSize=9.2, leading=13.2, textColor=G1, alignment=TA_LEFT),
    "h1": ParagraphStyle("h1", fontName="Sans-B", fontSize=22, leading=26, textColor=BLACK, spaceAfter=6),
    "h2": ParagraphStyle("h2", fontName="Sans-B", fontSize=11.5, leading=15, textColor=BLACK, spaceBefore=10, spaceAfter=4),
    "num": ParagraphStyle("num", fontName="Sans-B", fontSize=46, leading=46, textColor=RED),
    "eq": ParagraphStyle("eq", fontName="Sans", fontSize=10, leading=15, textColor=BLACK, leftIndent=14,
                         borderPadding=(6, 6, 6, 10), backColor=G3, spaceBefore=12, spaceAfter=10),
    "cap": ParagraphStyle("cap", fontName="Sans-I", fontSize=7.8, leading=10, textColor=G2, spaceAfter=8),
    "code": ParagraphStyle("code", fontName="Mono", fontSize=7.8, leading=10.5, textColor=BLACK, leftIndent=10,
                           backColor=G3, borderPadding=(4, 4, 4, 8), spaceAfter=6),
    "note": ParagraphStyle("note", fontName="Sans-B", fontSize=8.6, leading=12, textColor=RED, spaceBefore=4, spaceAfter=6),
}
rel = json.loads((ROOT / "results/relief_report.json").read_text())
prn = json.loads((ROOT / "export_3d_print/print_report.json").read_text())
wht = json.loads((ROOT / "results/maquette_blanche_report.json").read_text())


def fr(t):
    """French typography: decimal comma, scientific notation, kept spacing."""
    t = re.sub(r"(\d)\.(\d+)e-0?(\d+)", lambda m: f"{m.group(1)},{m.group(2)}·10<super>−{m.group(3)}</super>", t)
    t = re.sub(r"(?<=\d)\.(?=\d+(?![\w]))", ",", t)
    return re.sub(r" {2,}", lambda m: "&nbsp;" * len(m.group(0)), t)


def P(t, s="body"):
    return Paragraph(fr(t), S[s])


def EQ(t):
    return Paragraph(fr(t), S["eq"])


def table(rows, widths, head=True):
    cell = ParagraphStyle("cell", fontName="Sans", fontSize=8.2, leading=10, textColor=G1)
    rows = [[(Paragraph(fr(c), cell) if "<" in fr(c) else fr(c).replace("&nbsp;", " "))
             if isinstance(c, str) else c for c in r] for r in rows]
    t = Table(rows, colWidths=widths, hAlign="LEFT")
    st = [("FONT", (0, 0), (-1, -1), "Sans", 8.2), ("TEXTCOLOR", (0, 0), (-1, -1), G1),
          ("LINEBELOW", (0, 0), (-1, -1), 0.3, G2), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
          ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if head:
        st += [("FONT", (0, 0), (-1, 0), "Sans-B", 8.2), ("TEXTCOLOR", (0, 0), (-1, 0), white),
               ("BACKGROUND", (0, 0), (-1, 0), BLACK)]
    t.setStyle(TableStyle(st))
    return t


def fig(name, width_mm, caption):
    from PIL import Image as PI
    w, h = PI.open(FIG / name).size
    return KeepTogether([Image(str(FIG / name), width=width_mm * mm, height=width_mm * mm * h / w), P(caption, "cap")])


def section(n, title):
    return [PageBreak(), P(f"{n:02d}", "num"), P(title.upper(), "h1"),
            Table([[""]], colWidths=[40 * mm], rowHeights=[2.2],
                  style=[("BACKGROUND", (0, 0), (-1, -1), RED)], hAlign="LEFT"), Spacer(1, 8)]


# ----------------------------------------------------------------- page decorations
def cover(c, doc):
    c.saveState()
    c.setFillColor(BLACK); c.rect(0, 0, W, H, stroke=0, fill=1)
    # Tschumi-like grid of 'folies': red points on a 12 m-like grid + construction lines
    c.setStrokeColor(G1); c.setLineWidth(0.3)
    for i in range(1, 9):
        c.line(i * W / 9, 0, i * W / 9, H)
    for j in range(1, 13):
        c.line(0, j * H / 13, W, j * H / 13)
    c.setFillColor(RED)
    for (i, j) in [(1, 10), (3, 11), (5, 9), (7, 10), (2, 7), (6, 6), (4, 4), (8, 3), (1, 2)]:
        c.rect(i * W / 9 - 5 * mm, j * H / 13 - 5 * mm, 10 * mm, 10 * mm, stroke=0, fill=1)
    c.setStrokeColor(RED); c.setLineWidth(1.4)
    c.line(0, 0.62 * H, W, 0.30 * H)
    c.setFillColor(RED); c.rect(18 * mm, 0.70 * H, 9 * mm, 0.13 * H, stroke=0, fill=1)
    c.setFillColor(white); c.setFont("Sans-B", 64); c.drawString(32 * mm, 0.775 * H, "ORAN")
    c.setFillColor(RED); c.drawString(32 * mm, 0.705 * H, "3D")
    c.setFillColor(white); c.setFont("Sans-B", 15)
    c.drawString(32 * mm, 0.645 * H, "RECONSTRUCTION TOPOGRAPHIQUE")
    c.drawString(32 * mm, 0.645 * H - 7 * mm, "MODÈLE URBAIN RHINO + MNT COPERNICUS")
    c.setFont("Sans", 11); c.setFillColor(G2)
    c.drawString(32 * mm, 0.645 * H - 17 * mm, "Méthodes de calcul, géoréférencement, précision")
    c.drawString(32 * mm, 0.645 * H - 23 * mm, "et préparation de la maquette pour l'impression 3D")
    # author block
    c.setFillColor(white); c.rect(18 * mm, 22 * mm, W - 36 * mm, 46 * mm, stroke=0, fill=1)
    c.setFillColor(RED); c.rect(18 * mm, 22 * mm, 4 * mm, 46 * mm, stroke=0, fill=1)
    c.setFillColor(G2); c.setFont("Sans", 8.5)
    c.drawString(28 * mm, 58 * mm, "RÉALISÉ PAR")
    c.drawString(28 * mm, 39 * mm, "SOUS LA DIRECTION DE")
    c.setFillColor(BLACK); c.setFont("Sans-B", 15)
    c.drawString(28 * mm, 51 * mm, AUTHOR)
    c.drawString(28 * mm, 32 * mm, DIRECTOR)
    c.setFont("Sans", 8.5); c.setFillColor(G2)
    c.drawRightString(W - 24 * mm, 27 * mm, "Oran, Algérie  —  octobre 2026")
    c.restoreState()


def page(c, doc):
    c.saveState()
    c.setFillColor(RED); c.rect(0, 0, 6 * mm, H, stroke=0, fill=1)
    c.setStrokeColor(BLACK); c.setLineWidth(0.6); c.line(18 * mm, 16 * mm, W - 18 * mm, 16 * mm)
    c.setFont("Sans", 7.2); c.setFillColor(G2)
    c.drawString(18 * mm, 11 * mm, f"ORAN 3D  —  {AUTHOR}  —  sous la direction de {DIRECTOR}")
    c.setFillColor(BLACK); c.setFont("Sans-B", 9)
    c.drawRightString(W - 18 * mm, 11 * mm, f"{doc.page:02d}")
    c.restoreState()


# ----------------------------------------------------------------- content
def story():
    s = [NextPageTemplate("body"), PageBreak()]
    # --- sommaire
    s += [P("SOMMAIRE", "h1"),
          Table([[""]], colWidths=[40 * mm], rowHeights=[2.2], style=[("BACKGROUND", (0, 0), (-1, -1), RED)], hAlign="LEFT"),
          Spacer(1, 10)]
    toc = ["Données d'entrée et analyse du modèle Rhino", "Géoréférencement : projection retrouvée",
           "Relief : Copernicus GLO-30", "Pose des bâtiments sur le relief", "Drapage des routes et surfaces",
           "Nettoyage et validité des maillages", "Courbes de niveau et maquette blanche",
           "Maquette imprimable et socle", "Bilan de précision", "Rhino 8 et add-ons : guide pas à pas",
           "Chaîne de traitement reproductible"]
    s.append(table([[f"{i + 1:02d}", t] for i, t in enumerate(toc)], [16 * mm, 150 * mm], head=False))
    s += [Spacer(1, 14), P("<b>Objet.</b> Ce document décrit, calcul par calcul, la transformation du modèle "
                           "urbain plat d'Oran (fichier Rhino <i>brahim ib9a.3dm</i>) en un modèle 3D posé sur "
                           "le relief réel (MNT Copernicus GLO-30), puis en maquette blanche et en maquette "
                           "imprimable. Chaque résultat chiffré provient des contrôles automatiques de la chaîne "
                           "de traitement (fichiers JSON du dépôt).")]

    # 01 --------------------------------------------------------------
    s += section(1, "Données d'entrée")
    s += [P("<b>Modèle Rhino.</b> Fichier Rhino 8 de 92 Mo : 295 maillages (un par calque OpenStreetMap : "
            "BUILDING*, HIGHWAY_*, LANDUSE_*, NAME_*…), 2 458 323 faces, 9 755 087 sommets non soudés."),
          table([["Contrôle", "Constat", "Conséquence"],
                 ["Unité déclarée", "millimètres", "fausse : HEIGHT_104 mesure 104 unités → 1 u = 1 m"],
                 ["Ancrage terrestre", "lat 0 / lon 0", "aucun géoréférencement réel"],
                 ["Relief", "z = 0 partout (plaque BASE à −0,5)", "ville plate"],
                 ["Validité", "7 maillages invalides (routes)", "faces dégénérées à corriger"]],
                [38 * mm, 52 * mm, 76 * mm]),
          Spacer(1, 6),
          P("<b>MNT.</b> Copernicus DEM GLO-30 (tuiles N35W001 et N35W002), pas de 1 seconde d'arc : "
            "Δy = 30,9 m, Δx = 30,9·cos 35,7° = 25,1 m ; altitudes orthométriques (géoïde EGM2008) ; "
            "précision verticale absolue annoncée < 4 m (LE90).")]

    # 02 --------------------------------------------------------------
    phi = math.radians(35.699)
    k = 1 / math.cos(phi)
    a, e2 = 6378137.0, 0.00669437999014
    N = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    s += section(2, "Géoréférencement")
    s += [P("Le modèle ne contient aucune information de position. Elle a été retrouvée par le calcul, en "
            "superposant le trait de côte du modèle (calque BAY, 11 724 points) au trait de côte du MNT."),
          P("1. Recherche d'une similitude", "h2"),
          P("On cherche l'échelle s, la rotation θ et la translation t qui minimisent la distance au trait de "
            "côte du MNT (distance de chanfrein, moyenne tronquée à 60 % pour ignorer la fermeture du polygone en mer) :"),
          EQ("min<sub>s, θ, t</sub>  (1/n) Σ<sub>i</sub> d<sub>côte</sub>( s·R(θ)·p<sub>i</sub> + t )"),
          P(f"Résultat : <b>s = 0,812</b>. Or cos(35,7°) = {str(round(math.cos(math.radians(35.7)), 4)).replace('.', ',')} : l'échelle du modèle "
            "est exactement celle de la <b>projection Web Mercator</b> (celle d'OpenStreetMap)."),
          P("2. Projection Web Mercator (EPSG:3857)", "h2"),
          EQ("x = R·(λ − λ<sub>0</sub>)          y = R·[ ln tan(π/4 + φ/2) − ln tan(π/4 + φ<sub>0</sub>/2) ]<br/>"
             "inverse :  φ = 2·arctan( e<super>y/R + ln tan(π/4+φ0/2)</super> ) − π/2 ,   λ = λ<sub>0</sub> + x/R"
             "<br/>R = 6 378 137 m"),
          P(f"Facteur d'échelle de Mercator : k = sec φ. À Oran : <b>k = 1/cos 35,699° = {str(round(k, 4)).replace('.', ',')}</b>. "
            "Les longueurs horizontales du fichier d'origine sont donc <b>étirées de 23 %</b>, alors que les "
            "hauteurs sont en vrais mètres : les bâtiments étaient déformés."),
          fig("f_mercator.png", 105, "Figure 1 — Facteur d'échelle de la projection Mercator en fonction de la latitude."),
          P("3. Ajustement fin et centre", "h2"),
          table([["Paramètre", "Valeur", "Interprétation"],
                 ["facteur résiduel k'", "0,99998", "Mercator pur (k' = 1)"],
                 ["rotation résiduelle", "0,09°", "nulle"],
                 ["centre ajusté", "35,69901277 N  /  0,63500135 W", "à 1,4 m du point rond 35,699 / −0,635"],
                 ["sphérique vs ellipsoïdal", "15,7 m vs 17,3 m", "EPSG:3857 (sphérique) retenu"]],
                [40 * mm, 60 * mm, 66 * mm]),
          Spacer(1, 6),
          fig("f_coast.png", 165, "Figure 2 — Après calage, le trait de côte du modèle Rhino (rouge) se superpose au trait "
              "de côte Copernicus (noir) sur 30 km."),
          P("4. Repère de sortie", "h2"),
          P("Chaque sommet est reprojeté individuellement : Web Mercator → (φ, λ) WGS84 → Transverse Mercator "
            "local centré sur 35,699 N / 0,635 W (k<sub>0</sub> = 1). Distance réelle d'un déplacement x le long du "
            "parallèle d'origine :"),
          EQ(f"d = (x / R)·N(φ)·cos φ ,   N(φ) = a / √(1 − e²·sin²φ) = {N:_.1f} m".replace("_", " ")
             + f"<br/>d(1000 m) = 1000 × {N / a:.5f} × {math.cos(phi):.5f} = <b>{1000 * N / a * math.cos(phi):.2f} m</b>"),
          P(f"Contrôle : l'aller-retour local → (φ, λ) → Web Mercator redonne les coordonnées d'origine à "
            f"<b>{rel['checks']['xy_roundtrip_max_m']:.1e} m</b> près.")]

    # 03 --------------------------------------------------------------
    s += section(3, "Relief : Copernicus GLO-30")
    gx, gy = rel["terrain_grid"]
    ex = rel["terrain_extent_local_m"]
    s += [P(f"Grille régulière au pas de 25 m (résolution native) : {gx} × {gy} nœuds, "
            f"{ex[2] - ex[0]:_.0f} × {ex[3] - ex[1]:_.0f} m, altitudes de {rel['terrain_z_m'][0]:.0f} à "
            f"{rel['terrain_z_m'][1]:.1f} m. La mer ouverte (composante connexe z ≤ 0,5 m reliée au large) est mise "
            "exactement à 0 m.".replace("_", " ")),
          P("Surface du terrain", "h2"),
          P("Chaque cellule est coupée par sa diagonale ; la surface est linéaire par morceaux. Avec (u, v) les "
            "coordonnées locales dans la cellule (0 ≤ u, v ≤ 1) :"),
          EQ("si u + v ≤ 1 :  T = z<sub>00</sub> + u·(z<sub>10</sub> − z<sub>00</sub>) + v·(z<sub>01</sub> − z<sub>00</sub>)<br/>"
             "sinon :          T = z<sub>11</sub> + (1−u)·(z<sub>01</sub> − z<sub>11</sub>) + (1−v)·(z<sub>10</sub> − z<sub>11</sub>)"),
          P("Cette fonction T(x, y) est <b>exactement</b> la surface du maillage de terrain du fichier Rhino : tous les "
            "objets sont posés sur la même surface, sans écart d'interpolation."),
          P("Le MNT voit-il les bâtiments ?", "h2"),
          P("Copernicus est un modèle de surface. Test par régression du relief haute fréquence (filtre passe-haut "
            "de 400 m) sur la hauteur des bâtiments lissée à la résolution du MNT :"),
          EQ("Z<sub>hp</sub> = a · B<sub>hp</sub>   →   a = 0,072 ,   r = 0,040 ,   n = 1 130 020 cellules"),
          P("Biais moyen en ville : 0,41 m. L'effet est négligeable et un filtre « sol nu » enlèverait autant de vrai "
            "relief : <b>le MNT est utilisé sans filtrage</b>."),
          fig("f_map.png", 160, "Figure 3 — Relief Copernicus (ombrage gris) et 236 575 bâtiments (rouge), repère local en km.")]

    # 04 --------------------------------------------------------------
    f50, f95, f99, fmax = rel["foundation_extension_m_p50_p95_p99_max"]
    s += section(4, "Pose des bâtiments")
    s += [P("Après soudure des sommets et séparation aux arêtes non-manifold, le modèle contient <b>236 575</b> "
            "solides. 249 087 pièces avaient leurs normales tournées vers l'intérieur ; elles sont réorientées par le "
            "signe du volume :"),
          EQ("V = (1/6) Σ<sub>faces</sub> a · (b × c)     si V &lt; 0 : inversion de l'ordre des sommets"),
          P("Chaque bâtiment est déplacé <b>en bloc</b> (XY inchangés). Sur son emprise E, échantillonnée tous les 2 m :"),
          EQ("G = médiane<sub>E</sub> T(x, y)     z' = z + G     (toit à la vraie hauteur au-dessus du sol)<br/>"
             "base :  z'<sub>base</sub> = min<sub>E</sub> T(x, y)     (fondation verticale, jamais en l'air)"),
          table([["Contrôle", "Résultat"],
                 ["variation de hauteur des bâtiments", f"{rel['checks']['rigid_height_change_max_m']:.1e} m"],
                 ["base au-dessus du sol (flottement)", f"{rel['checks']['rigid_float_gap_max_m']:.3f} m"],
                 ["fondation : médiane / 95 % / 99 % / max", f"{f50} / {f95} / {f99} / {fmax} m"]],
                [90 * mm, 76 * mm]),
          Spacer(1, 8),
          fig("f_3d.png", 165, "Figure 4 — Centre-ville et port vus depuis la mer, échelle réelle : bâtiments (rouge) sur le relief.")]

    # 05 --------------------------------------------------------------
    nlay = len(rel["drape_tolerance_m"])
    bad = {k_: v for k_, v in rel["drape_tolerance_m"].items() if v["min_clearance_m"] < 0}
    s += section(5, "Drapage des routes et surfaces")
    s += [P("Routes, voies ferrées, occupation du sol, parcs… (épaisseur ≤ 1,5 m) sont posés sommet par sommet :"),
          EQ("z' = T(x, y) + z + 0,5      (0,5 m = ancienne plaque BASE remplacée par le terrain : empilement conservé)"),
          P("Un triangle plat posé sur un terrain linéaire par morceaux s'en écarte. L'écart e = T − plan est lui-même "
            "linéaire par morceaux : son <b>maximum est atteint sur un sommet de l'arrangement</b>, c'est-à-dire "
            "là où une arête coupe une ligne de la grille (x = x<sub>i</sub>, y = y<sub>j</sub>, diagonale "
            "x + y = c<sub>k</sub>) ou en un nœud de la grille intérieur au triangle. Ces points sont calculés "
            "exactement ; on coupe les arêtes tant que :"),
          EQ("max |e| &gt; τ ,    τ = clip( o<sub>min</sub> − 0,02 ;  0,05 ;  0,5 ) m"),
          P("où o<sub>min</sub> est la hauteur minimale du calque au-dessus du sol : la tolérance est toujours plus petite "
            "que cette hauteur, donc le calque ne passe pas sous le terrain. Le découpage en 2, 3 ou 4 triangles "
            "conserve la conformité (pas de jonction en T). L'eau (mer, lacs) reste plane."),
          fig("f_drape.png", 120, "Figure 5 — L'écart maximal entre une arête et le terrain se situe sur une ligne de la grille."),
          P(f"Contrôle par 60 000 points tirés au hasard proportionnellement à la surface, sur {nlay} calques : "
            f"{nlay - len(bad)} calques ne touchent jamais le terrain ; {len(bad)} calques "
            f"({', '.join(bad)}) passent localement jusqu'à "
            f"{-min(v['min_clearance_m'] for v in bad.values()):.3f} m sous la surface, sur des points de falaise.")]

    # 06 --------------------------------------------------------------
    s += section(6, "Nettoyage et validité")
    s += [table([["Opération", "Formule / règle", "Effet"],
                 ["soudure", "sommets identiques à 10⁻⁶ m fusionnés", "topologie continue"],
                 ["faces dégénérées", "coins répétés ou aire nulle supprimés", "69 703 faces retirées"],
                 ["doublons", "même triplet de sommets", "supprimés"],
                 ["non-manifold", "arête partagée par > 2 faces → séparation", "1 bâtiment = 1 solide"],
                 ["orientation", "signe de V (§ 04)", "249 087 pièces réorientées"],
                 ["découpe", "4 demi-plans du rectangle 29 × 22 km", "rien ne dépasse la maquette"],
                 ["validité Rhino", "ON_Mesh::IsValid", "0 maillage invalide"]],
                [32 * mm, 74 * mm, 60 * mm])]

    # 07 --------------------------------------------------------------
    s += section(7, "Courbes de niveau et maquette blanche")
    s += [P("Les courbes sont l'intersection exacte du maillage de terrain avec les plans z = 10·k m. Sur une arête "
            "[a, b] coupée par le niveau L :"),
          EQ("t = (L − z<sub>a</sub>) / (z<sub>b</sub> − z<sub>a</sub>)      p = a + t·(b − a)"),
          P("Chaque triangle coupé fournit un segment ; les segments sont chaînés par arête commune en polylignes "
            f"continues : <b>{wht['contour_polylines']} courbes</b> sur {wht['contour_levels']} niveaux "
            "(calques COURBES_NIVEAU_10m et COURBES_NIVEAU_50m). Les courbes sont sur la surface du terrain."),
          P("La maquette blanche reprend tous les calques de détail, tous en blanc, et remplace la surface du terrain "
            "par un bloc fermé : terrain au-dessus, parois verticales, fond plat 60 m sous le niveau de la mer."),
          P("Contrôle sur les lieux nommés du fichier Rhino (position exacte = calques NAME_ / HISTORIC_) :", "h2"),
          table([["Lieu (calque du modèle)", "Position", "Maquette", "Copernicus brut"]] +
                [[r[0], r[1], f"{r[2]} m", f"{r[3]} m"] for r in
                 json.loads((ROOT / "results/altitudes_lieux_fichier.json").read_text())],
                [62 * mm, 46 * mm, 26 * mm, 32 * mm]),
          P("Écarts de 0 à 4 m, sauf au Square Port Saïd (9 m) situé en bord de falaise : sur une pente "
            "forte, la grille de 25 m et le pixel brut ne tombent pas au même endroit de la pente.", "cap")]

    # 08 --------------------------------------------------------------
    hd, op = prn["HD"], prn["OPT"]
    s += section(8, "Maquette imprimable et socle")
    s += [EQ(f"échelle :  k = 200 mm / {prn['extent_m'][0]:_.0f} m = {prn['mm_per_m']:.6f} mm/m   →   "
             f"<b>{prn['scale'].replace(',', ' ')}</b><br/>z<sub>mm</sub> = z · k · 2 + 4     (relief ×2, socle plein de 4 mm sous le niveau de la mer)"
             .replace("_", " ")),
          P("Chaque bâtiment est un solide fermé ; sa base est prolongée de 0,3 mm dans le terrain et les parties "
            "en l'air (étages, passerelles : 112) jusqu'au sol. Union booléenne exacte (manifold3d) du socle et de "
            f"{prn['parts']['kept']:_} bâtiments ; {prn['detached_bodies_removed']['count']:_} feuilles de volume nul "
            "retirées.".replace("_", " ")),
          table([["Contrôle", "HD", "OPT"],
                 ["triangles", f"{hd['triangles']:_}".replace("_", " "), f"{op['triangles']:_}".replace("_", " ")],
                 ["dimensions (mm)", " × ".join(f"{v:.1f}" for v in hd["dimensions_mm"]),
                  " × ".join(f"{v:.1f}" for v in op["dimensions_mm"])],
                 ["étanche", "oui" if hd["watertight"] else "non", "oui" if op["watertight"] else "non"],
                 ["arêtes ouvertes / non-manifold", f"{hd['open_edges']} / {hd['nonmanifold_edges']}",
                  f"{op['open_edges']} / {op['nonmanifold_edges']}"],
                 ["orientation cohérente", "oui" if hd["winding_consistent"] else "non",
                  "oui" if op["winding_consistent"] else "non"],
                 ["nombre de corps", str(hd["bodies"]), str(op["bodies"])],
                 ["volume (cm³)", f"{hd['volume_cm3']}", f"{op['volume_cm3']}"]],
                [70 * mm, 48 * mm, 48 * mm]),
          Spacer(1, 6),
          fig("f_hist.png", 165, "Figure 6 — Gauche : taille des bâtiments à l'échelle d'impression, comparée à une buse de 0,4 mm. "
              "Droite : écart maximal mesuré par calque drapé, comparé à sa tolérance."),
          P(f"Limite : la plus petite dimension médiane d'un bâtiment imprimé est "
            f"{prn['building_min_dim_mm_percentiles_5_50_95'][1]:.2f} mm ; {prn['buildings_below_nozzle']:_} "
            "bâtiments (99 %) sont plus fins qu'une buse de 0,4 mm : à cette échelle la ville apparaît comme une "
            "texture. Pour des bâtiments distincts : imprimer un quartier (1:25 000) ou en résine.".replace("_", " "),
            "note")]

    # 09 --------------------------------------------------------------
    sxy = math.sqrt(4 ** 2 + 10 ** 2)
    sz = math.sqrt((4 / 1.645) ** 2 + 1.0 ** 2 + (0.5 / math.sqrt(3)) ** 2)
    s += section(9, "Bilan de précision")
    s += [P("Les erreurs indépendantes s'additionnent quadratiquement (écart-type σ) :"),
          EQ("σ<sub>total</sub> = √( Σ σ<sub>i</sub>² )        LE90 ≈ 1,645 · σ"),
          table([["Source", "Composante", "σ estimé"],
                 ["Géométrie OpenStreetMap", "horizontale", "≈ 4 m"],
                 ["Calage (trait de côte, MNT 25–31 m)", "horizontale", "≈ 10 m"],
                 ["Reprojection (formules exactes)", "horizontale", "3,7·10⁻⁹ m"],
                 ["Copernicus GLO-30 (LE90 < 4 m)", "verticale", "≈ 2,4 m"],
                 ["Interpolation sur la grille de 25 m", "verticale", "≈ 1 m"],
                 ["Drapage (tolérance ≤ 0,5 m, uniforme)", "verticale", "≈ 0,29 m"]],
                [80 * mm, 40 * mm, 46 * mm]),
          EQ(f"σ<sub>xy</sub> = √(4² + 10²) ≈ <b>{sxy:.1f} m</b>        "
             f"σ<sub>z</sub> = √(2,4² + 1² + 0,29²) ≈ <b>{sz:.1f} m</b>  (LE90 ≈ {1.645 * sz:.1f} m)"),
          P("Ces valeurs sont des estimations (ordres de grandeur) ; les contrôles internes (aller-retour, hauteurs, "
            "flottement, validité) sont, eux, mesurés exactement par la chaîne de traitement.")]

    # 10 --------------------------------------------------------------
    s += section(10, "Rhino 8 et add-ons : guide pas à pas")
    s += [P("<b>Important.</b> Le modèle a été produit sans add-on Rhino, par une chaîne Python (§ 11) écrivant "
            "directement le format .3dm avec <b>rhino3dm</b>, la bibliothèque officielle de McNeel. Les étapes "
            "ci-dessous sont des <b>recommandations</b> pour exploiter et prolonger la maquette dans Rhino 8.", "note"),
          table([["Étape", "Outil / commande", "But"],
                 ["1", "Ouvrir  Oran_maquette_blanche_complete_Rhino8.3dm", "unités m, ancrage 35,699 N / 0,635 W"],
                 ["2", "Propriétés > Emplacement terrestre", "vérifier le géoréférencement"],
                 ["3", "Mode d'affichage Arctic / Rendu technique", "lecture blanc sur blanc"],
                 ["4", "ScaleNU (1, 1, 2) depuis 0,0,0", "exagérer le relief si besoin"],
                 ["5", "Contour sur TERRAIN_ET_SOCLE (pas 5 m)", "courbes intermédiaires"],
                 ["6", "ClippingPlane, Section, Make2D", "coupes et plans 2D"],
                 ["7", "VisualARQ 3 : niveaux, étiquettes, coupes", "documentation architecturale"],
                 ["8", "Grasshopper + Heron / Elk / Urbano", "mise à jour OSM paramétrique"],
                 ["9", "Ladybug (Grasshopper)", "ensoleillement, ombres portées"],
                 ["10", "Check, ShowEdges (arêtes nues)", "contrôle avant impression"],
                 ["11", "Export STL en mm → PrusaSlicer / Bambu Studio / Cura", "impression 3D"]],
                [12 * mm, 84 * mm, 70 * mm])]

    # 11 --------------------------------------------------------------
    s += section(11, "Chaîne de traitement reproductible")
    s += [P("Scripts Python du dépôt <i>Oran-3D-Terrain-Reconstruction</i>, exécutés dans cet ordre :"),
          table([["Script", "Rôle"],
                 ["01_extract_dem.py", "extraction et reprojection du MNT Copernicus"],
                 ["03_analyze_rhino.py", "analyse du fichier .3dm"],
                 ["oran_georef.py", "Web Mercator → Transverse Mercator local (exact)"],
                 ["05_build_oran_relief.py", "nettoyage, pose des bâtiments, drapage, fichier Rhino 8"],
                 ["05b_clean_rhino.py / 05c_finish_rhino.py", "validité Rhino, découpe, couleurs"],
                 ["06_print_export.py", "union booléenne, socle, STL / OBJ contrôlés"],
                 ["10_maquette_blanche_complete.py", "maquette blanche, courbes de niveau"],
                 ["07_package.py", "archives 7z en volumes de 28 Mo"]],
                [70 * mm, 96 * mm]),
          Spacer(1, 6),
          P("Bibliothèques : rhino3dm 8, pyproj, rasterio, numpy, scipy, manifold3d, trimesh, shapely, scikit-image, py7zr."),
          P("Données : Copernicus DEM GLO-30 © ESA / DLR / Airbus (distribution AWS Open Data) ; "
            "géométrie urbaine © contributeurs OpenStreetMap (ODbL), via le modèle Rhino fourni.", "cap")]
    return s


def main():
    doc = BaseDocTemplate(str(OUT), pagesize=A4, title="Oran 3D — méthodes de calcul et précision",
                          author=AUTHOR, subject=f"Sous la direction de {DIRECTOR}")
    fr = Frame(22 * mm, 22 * mm, W - 40 * mm, H - 40 * mm, id="f")
    doc.addPageTemplates([PageTemplate("cover", [Frame(0, 0, W, H)], onPage=cover),
                          PageTemplate("body", [fr], onPage=page)])
    doc.build(story())
    print("wrote", OUT)


if __name__ == "__main__":
    main()
