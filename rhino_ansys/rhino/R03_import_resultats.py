#! python3
# -*- coding: utf-8 -*-
"""
R03 - Réimport des résultats CFD (OpenFOAM ou ANSYS Fluent) dans Rhino 8.

Format d'échange (identique pour les deux solveurs, coordonnées du modèle en m) :
  champ_pieton_<scenario>.csv   x,y,z,U,Ux,Uy,Uz[,T]      points à 1,75 m au-dessus du sol
  lignes_courant_<scenario>.csv id,x,y,z,U                 polylignes (lignes de courant)
  facades_<scenario>.csv        x,y,z,Cp[,T],cfd_id         centres des faces des bâtiments
  meta_<scenario>.json          direction, U10 de référence, solveur, unités, échelles

Calques créés (jamais dans les calques d'origine) :
  RESULTATS_CFD::<scenario>::VITESSE_PIETON       nuage de points coloré + valeur U (m/s)
  RESULTATS_CFD::<scenario>::DIRECTION_VENT        flèches (lignes) tous les N points
  RESULTATS_CFD::<scenario>::LIGNES_COURANT        polylignes
  RESULTATS_CFD::<scenario>::ACCELERATION          points où U/Uref > 1,2
  RESULTATS_CFD::<scenario>::RECIRCULATION         points où U < 0,5 m/s
  RESULTATS_CFD::<scenario>::CP_FACADES / T_AIR    si présents
Utilisation : lancer le script, choisir le dossier qui contient les CSV.
La lecture des fichiers et le calcul des couleurs se font en arrière-plan.
"""
import csv
import json
import os
import System
import System.Threading.Tasks as Tasks
import Rhino
import Rhino.Geometry as G
import scriptcontext as sc
import Rhino.UI

doc = sc.doc


def ramp(t):
    """bleu -> cyan -> vert -> jaune -> rouge (t in 0..1)"""
    stops = [(0, (49, 54, 149)), (.25, (69, 117, 180)), (.5, (116, 196, 118)), (.75, (254, 224, 76)), (1, (215, 25, 28))]
    t = max(0.0, min(1.0, t))
    for (a, ca), (b, cb) in zip(stops[:-1], stops[1:]):
        if t <= b:
            u = (t - a) / (b - a)
            return System.Drawing.Color.FromArgb(*[int(ca[i] + u * (cb[i] - ca[i])) for i in range(3)])
    return System.Drawing.Color.FromArgb(*stops[-1][1])


def layer(path, color=System.Drawing.Color.Black):
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


def read_rows(p):
    with open(p) as f:
        return [{k: float(v) if k != "cfd_id" else v for k, v in r.items()} for r in csv.DictReader(f)]


def run():
    d = Rhino.UI.Dialogs.ShowBrowseForFolder(None, "Dossier des résultats CFD (CSV)")
    if not d:
        return
    metas = [f for f in os.listdir(d) if f.startswith("meta_") and f.endswith(".json")]

    def work():
        jobs = []
        for mf in metas:
            sid = mf[5:-5]
            meta = json.load(open(os.path.join(d, mf)))
            ped = os.path.join(d, "champ_pieton_%s.csv" % sid)
            rows = read_rows(ped) if os.path.exists(ped) else []
            sl = os.path.join(d, "lignes_courant_%s.csv" % sid)
            lines = {}
            if os.path.exists(sl):
                for r in read_rows(sl):
                    lines.setdefault(int(r["id"]), []).append((r["x"], r["y"], r["z"], r["U"]))
            jobs.append((sid, meta, rows, lines))

        def add():
            for sid, meta, rows, lines in jobs:
                uref = meta.get("U10_ref_ms", 5.0); umax = meta.get("U_scale_max", 2.0 * uref)
                base = "RESULTATS_CFD::%s" % sid
                pc = G.PointCloud(); acc = G.PointCloud(); rec = G.PointCloud()
                for r in rows:
                    p = G.Point3d(r["x"], r["y"], r["z"])
                    pc.Add(p, ramp(r["U"] / umax)); pc[pc.Count - 1].PointValue = r["U"]
                    if r["U"] > 1.2 * uref:
                        acc.Add(p)
                    if r["U"] < 0.5:
                        rec.Add(p)
                a = Rhino.DocObjects.ObjectAttributes(); a.LayerIndex = layer(base + "::VITESSE_PIETON")
                a.Name = "U pieton (m/s) %s, Uref=%.1f m/s, solveur %s" % (sid, uref, meta.get("solver", "?"))
                doc.Objects.AddPointCloud(pc, a)
                a = Rhino.DocObjects.ObjectAttributes(); a.LayerIndex = layer(base + "::ACCELERATION", System.Drawing.Color.Red)
                doc.Objects.AddPointCloud(acc, a)
                a = Rhino.DocObjects.ObjectAttributes(); a.LayerIndex = layer(base + "::RECIRCULATION", System.Drawing.Color.Blue)
                doc.Objects.AddPointCloud(rec, a)
                li = layer(base + "::DIRECTION_VENT", System.Drawing.Color.DimGray)
                for r in rows[::25]:
                    v = G.Vector3d(r["Ux"], r["Uy"], 0)
                    if v.Length > 1e-6:
                        v.Unitize(); v *= 4.0 + 2.0 * r["U"]
                        a = Rhino.DocObjects.ObjectAttributes(); a.LayerIndex = li
                        a.ObjectDecoration = Rhino.DocObjects.ObjectDecoration.EndArrowhead
                        doc.Objects.AddLine(G.Line(G.Point3d(r["x"], r["y"], r["z"]), v), a)
                li = layer(base + "::LIGNES_COURANT", System.Drawing.Color.Black)
                for k, pts in lines.items():
                    if len(pts) > 2:
                        a = Rhino.DocObjects.ObjectAttributes(); a.LayerIndex = li
                        a.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromObject
                        a.ObjectColor = ramp(sum(p[3] for p in pts) / len(pts) / umax)
                        doc.Objects.AddPolyline(G.Polyline([G.Point3d(p[0], p[1], p[2]) for p in pts]), a)
            doc.Views.Redraw()
            Rhino.RhinoApp.WriteLine("R03 : %d scénario(s) importé(s) dans RESULTATS_CFD" % len(jobs))
        Rhino.RhinoApp.InvokeOnUiThread(System.Action(add))

    Tasks.Task.Run(System.Action(work))
    Rhino.RhinoApp.WriteLine("R03 : lecture des résultats en arrière-plan...")


run()
