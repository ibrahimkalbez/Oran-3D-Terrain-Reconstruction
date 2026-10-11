#! python3
# -*- coding: utf-8 -*-
"""
R02 - Préparation de la géométrie de calcul DANS Rhino 8 (copie de travail).

Sécurité : le script refuse de travailler si le fichier courant n'a pas été
enregistré sous un nom se terminant par "_CFD_WORK" (Fichier > Enregistrer sous...).
La maquette originale n'est jamais modifiée : on ne crée que des COPIES dans de
nouveaux calques "CFD_MODELE_A::..." (aérodynamique) et "CFD_MODELE_B::..."
(thermique), avec des identifiants UserText traçables :
    cfd_id        CENTRE_VILLE_B00012
    rhino_source  <id de l'objet d'origine>|<calque>|<composante>
    hauteur_m, emprise_m2, lst_2024_toit (si la maquette thermique est présente)

Sources utilisées (identiques au traitement hors Rhino, script 43) :
  * bâtiments : calque BUILDINGS_EMPRISES_REELLES_OSM_GOOGLE_MICROSOFT (emprises réelles) ;
    le calque BUILDINGS::EXTRA_BUILD_BY_TURBOCG_COM n'est pas utilisé pour la CFD (emprises
    générées, IoU 0,03 avec le bâti réel) mais il est conservé ;
  * relief : TERRAIN_ET_SOCLE ;
  * secteurs : boîtes du fichier secteurs.json (centre-ville, Gambetta, boulevard des Lions).

Export : un STL par secteur + ground.stl dans <dossier du .3dm>/CFD_EXPORT/, en mètres,
coordonnées du modèle (aucune transformation), + batiments_cfd_rhino.csv.
Le découpage par composantes connexes se fait en arrière-plan (Task) ; l'ajout des objets
dans le document est fait ensuite sur le fil de l'interface.
"""
import csv
import json
import os
import System
import System.Threading.Tasks as Tasks
import Rhino
import Rhino.Geometry as G
import scriptcontext as sc

doc = sc.doc
REAL = "BUILDINGS_EMPRISES_REELLES_OSM_GOOGLE_MICROSO"
SECTEURS = {   # boîtes en mètres modèle (results/cfd/secteurs.json), tampon 300 m
    "CENTRE_VILLE": [-1901, -333, -271, 1054],
    "GAMBETTA": [452, 555, 2262, 2108],
    "BD_DES_LIONS": [5068, 57, 7691, 1779]}
BUF = 300.0


def check_copy():
    name = os.path.splitext(doc.Name or "")[0]
    if not name.endswith("_CFD_WORK"):
        Rhino.RhinoApp.WriteLine("R02 : enregistrez d'abord une COPIE sous <nom>_CFD_WORK.3dm (rien n'a été modifié).")
        return False
    return True


def layer(path, color):
    idx = doc.Layers.FindByFullPath(path, -1)
    if idx >= 0:
        return idx
    parent = None
    for part in path.split("::"):
        full = part if parent is None else doc.Layers[parent].FullPath + "::" + part
        i = doc.Layers.FindByFullPath(full, -1)
        if i < 0:
            L = Rhino.DocObjects.Layer(); L.Name = part; L.Color = color
            if parent is not None:
                L.ParentLayerId = doc.Layers[parent].Id
            i = doc.Layers.Add(L)
        parent = i
    return parent


def sector_of(c):
    for k, b in SECTEURS.items():
        if b[0] - BUF < c.X < b[2] + BUF and b[1] - BUF < c.Y < b[3] + BUF:
            return k
    return None


def run():
    if not check_copy():
        return
    src = []
    for o in doc.Objects:
        L = doc.Layers[o.Attributes.LayerIndex]
        if L.Name.startswith(REAL) and isinstance(o.Geometry, G.Mesh):
            src.append((str(o.Id), L.FullPath, o.Geometry.DuplicateMesh()))
    if not src:
        Rhino.RhinoApp.WriteLine("R02 : calque des emprises réelles introuvable."); return
    out_dir = os.path.join(os.path.dirname(doc.Path), "CFD_EXPORT")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    def work():
        parts = []
        for oid, lp, m in src:
            m.Vertices.CombineIdentical(True, True)
            for k, piece in enumerate(m.SplitDisjointPieces()):
                bb = piece.GetBoundingBox(True)
                sec = sector_of(bb.Center)
                if sec is None or bb.Max.Z - bb.Min.Z < 2.0:
                    continue
                piece.Normals.ComputeNormals()
                parts.append((sec, oid, lp, k, piece, bb))

        def add():
            cols = {"CENTRE_VILLE": System.Drawing.Color.FromArgb(70, 120, 200),
                    "GAMBETTA": System.Drawing.Color.FromArgb(80, 160, 90),
                    "BD_DES_LIONS": System.Drawing.Color.FromArgb(200, 120, 50)}
            n = {k: 0 for k in SECTEURS}
            rows = []
            for sec, oid, lp, k, piece, bb in parts:
                n[sec] += 1
                cid = "%s_B%05d" % (sec, n[sec])
                for model in ("CFD_MODELE_A", "CFD_MODELE_B"):
                    li = layer("%s::BATIMENTS::%s" % (model, sec), cols[sec])
                    a = Rhino.DocObjects.ObjectAttributes(); a.LayerIndex = li; a.Name = cid
                    a.SetUserString("cfd_id", cid); a.SetUserString("rhino_source", "%s|%s|%d" % (oid, lp, k))
                    a.SetUserString("hauteur_m", "%.2f" % (bb.Max.Z - bb.Min.Z))
                    doc.Objects.AddMesh(piece, a)
                rows.append([cid, sec, oid, lp, k, "%.2f" % bb.Center.X, "%.2f" % bb.Center.Y, "%.2f" % (bb.Max.Z - bb.Min.Z)])
            # STL export per sector (meshes of model A)
            for sec in SECTEURS:
                li = doc.Layers.FindByFullPath("CFD_MODELE_A::BATIMENTS::" + sec, -1)
                doc.Objects.UnselectAll()
                for o in doc.Objects.FindByLayer(doc.Layers[li]):
                    o.Select(True)
                Rhino.RhinoApp.RunScript('_-Export "%s" _Enter _Enter' % os.path.join(out_dir, "buildings_%s.stl" % sec), False)
                doc.Objects.UnselectAll()
            with open(os.path.join(out_dir, "batiments_cfd_rhino.csv"), "w") as f:
                w = csv.writer(f); w.writerow(["cfd_id", "secteur", "rhino_objet_id", "calque", "composante", "x", "y", "hauteur_m"])
                w.writerows(rows)
            doc.Views.Redraw()
            Rhino.RhinoApp.WriteLine("R02 terminé : %s -> %s" % (json.dumps(n), out_dir))
        Rhino.RhinoApp.InvokeOnUiThread(System.Action(add))

    Tasks.Task.Run(System.Action(work))
    Rhino.RhinoApp.WriteLine("R02 : découpage des bâtiments en arrière-plan (%d maillages source)..." % len(src))


run()
