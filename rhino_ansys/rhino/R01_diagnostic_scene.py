#! python3
# -*- coding: utf-8 -*-
"""
R01 - Diagnostic de la scène Rhino 8 ouverte (LECTURE SEULE, rien n'est modifié).

A lancer dans Rhino 8 : commande ScriptEditor > ouvrir ce fichier > Run (Python 3),
ou commande _-RunPythonScript "chemin\\R01_diagnostic_scene.py".

Ce que fait le script
  1. Sur le fil de l'interface (rapide) : copie légère de la liste des objets
     (id, calque, type, attributs, boîte englobante, géométrie dupliquée).
  2. Dans une tâche d'arrière-plan (System.Threading.Tasks) : analyse lourde
     (maillages ouverts / invalides, doublons, statistiques par calque,
     données thermiques : couleurs de calque, matériaux, valeurs des nuages de
     points, UserText). Rhino reste utilisable pendant l'analyse.
  3. Écrit, à côté du fichier .3dm :
       <nom>_diagnostic.json   (tout)
       <nom>_calques.csv       (inventaire par calque)
     et affiche un résumé dans la ligne de commande.

Repérage des deux maquettes
  - maquette thermique : calque racine "THERMAL_ORAN_2024_07_10" (ou tout calque
    dont le nom contient THERMAL / THERMIQUE / LST / TEMP) ;
  - maquette urbaine : calques BUILDING*, HEIGHT_*, ROAD_*, HIGHWAY_*, TERRAIN*,
    SIDEWALK*/TROTTOIR*, LANDUSE_*, etc.
"""
import csv
import hashlib
import json
import os
import System
import System.Threading.Tasks as Tasks
import Rhino
import scriptcontext as sc

doc = sc.doc
THERMAL_KEYS = ("THERMAL", "THERMIQUE", "LST", "TEMP")
URBAN_PREFIX = ("BUILDING", "HEIGHT_", "EXTRA_BUILD", "ROAD_", "HIGHWAY_", "TERRAIN", "SIDEWALK", "TROTTOIR",
                "CURB", "BORDURE", "LANDUSE_", "LEISURE_", "RAILWAY_", "PEDESTRIAN", "CROSSING", "ILOT")


def layer_path(i):
    L = doc.Layers[i]
    return L.FullPath if L is not None else "?"


def snapshot():
    """UI thread: light copies only."""
    snap = []
    for o in doc.Objects.GetObjectList(Rhino.DocObjects.ObjectEnumeratorSettings()):
        g = o.Geometry
        a = o.Attributes
        snap.append({
            "id": str(o.Id), "layer": a.LayerIndex, "type": g.ObjectType.ToString(),
            "name": a.Name or "", "user": {k: a.GetUserString(k) for k in a.GetUserStrings().AllKeys},
            "mat_src": a.MaterialSource.ToString(), "mat": a.MaterialIndex,
            "col_src": a.ColorSource.ToString(), "geom": g.Duplicate()})
    return snap


def analyse(snap):
    lay = {}
    hashes = {}
    thermal_values = []
    for s in snap:
        L = lay.setdefault(s["layer"], {"path": layer_path(s["layer"]), "n": 0, "types": {}, "faces": 0,
                                         "open": 0, "invalid": 0, "pc_points": 0, "pc_values": 0,
                                         "user_text": 0, "bbox": None})
        g = s["geom"]
        L["n"] += 1
        L["types"][s["type"]] = L["types"].get(s["type"], 0) + 1
        if s["user"]:
            L["user_text"] += 1
        bb = g.GetBoundingBox(True)
        if bb.IsValid:
            b = [bb.Min.X, bb.Min.Y, bb.Min.Z, bb.Max.X, bb.Max.Y, bb.Max.Z]
            L["bbox"] = b if L["bbox"] is None else [min(L["bbox"][0], b[0]), min(L["bbox"][1], b[1]), min(L["bbox"][2], b[2]),
                                                     max(L["bbox"][3], b[3]), max(L["bbox"][4], b[4]), max(L["bbox"][5], b[5])]
        if isinstance(g, Rhino.Geometry.Mesh):
            L["faces"] += g.Faces.Count
            if not g.IsValid:
                L["invalid"] += 1
            if not g.IsClosed:
                L["open"] += 1
            key = hashlib.sha1(("%d|%.2f|%.2f|%.2f|%.2f" % (g.Vertices.Count, bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y)).encode()).hexdigest()
            hashes.setdefault(key, []).append(s["id"])
        elif isinstance(g, Rhino.Geometry.Brep):
            if not g.IsValid:
                L["invalid"] += 1
            if not g.IsSolid:
                L["open"] += 1
        elif isinstance(g, Rhino.Geometry.PointCloud):
            L["pc_points"] += g.Count
            if g.ContainsValues:
                L["pc_values"] += g.Count
                vals = list(g.GetPointValues())
                if vals:
                    thermal_values.append((L["path"], min(vals), max(vals), sum(vals) / len(vals), len(vals)))
    dups = [v for v in hashes.values() if len(v) > 1]
    return lay, dups, thermal_values


def run():
    path = doc.Path or os.path.join(os.path.expanduser("~"), "rhino_scene")
    base = os.path.splitext(path)[0]
    Rhino.RhinoApp.WriteLine("R01 : copie de la scène...")
    snap = snapshot()
    units = doc.ModelUnitSystem.ToString()
    tol = doc.ModelAbsoluteTolerance
    ea = doc.EarthAnchorPoint
    mats = []
    for i in range(doc.Materials.Count):
        m = doc.Materials[i]
        tex = m.GetBitmapTexture()
        mats.append({"index": i, "name": m.Name, "diffuse": [m.DiffuseColor.R, m.DiffuseColor.G, m.DiffuseColor.B],
                     "texture": tex.FileReference.FullPath if tex is not None else None})
    layer_info = {i: {"color": [doc.Layers[i].Color.R, doc.Layers[i].Color.G, doc.Layers[i].Color.B],
                      "material": doc.Layers[i].RenderMaterialIndex, "visible": doc.Layers[i].IsVisible}
                  for i in range(doc.Layers.Count) if not doc.Layers[i].IsDeleted}

    def work():
        try:
            lay, dups, thv = analyse(snap)
            rep = {"file": doc.Name, "units": units, "tolerance": tol,
                   "earth_anchor": [ea.EarthBasepointLatitude, ea.EarthBasepointLongitude],
                   "n_objects": len(snap), "materials": mats, "duplicates_groups": len(dups),
                   "duplicates_examples": dups[:20], "thermal_pointcloud_values": thv, "layers": []}
            for i, L in sorted(lay.items()):
                L.update(layer_info.get(i, {}))
                up = L["path"].upper()
                L["role"] = ("thermique" if any(k in up for k in THERMAL_KEYS) else
                             "urbain" if any(seg.startswith(URBAN_PREFIX) for seg in up.split("::")) else "autre")
                rep["layers"].append(L)
            with open(base + "_diagnostic.json", "w") as f:
                json.dump(rep, f, indent=1, ensure_ascii=False)
            with open(base + "_calques.csv", "w") as f:
                w = csv.writer(f)
                w.writerow(["calque", "role", "objets", "types", "faces", "ouverts", "invalides", "points_nuage",
                            "points_avec_valeur", "user_text", "couleur", "materiau", "bbox"])
                for L in rep["layers"]:
                    w.writerow([L["path"], L["role"], L["n"], L["types"], L["faces"], L["open"], L["invalid"],
                                L["pc_points"], L["pc_values"], L["user_text"], L.get("color"), L.get("material"), L["bbox"]])
            msg = ("R01 terminé : %d objets, %d calques (%d thermiques, %d urbains), %d doublons, unités %s -> %s"
                   % (len(snap), len(rep["layers"]), sum(1 for L in rep["layers"] if L["role"] == "thermique"),
                      sum(1 for L in rep["layers"] if L["role"] == "urbain"), len(dups), units, base + "_diagnostic.json"))
        except Exception as e:
            msg = "R01 ERREUR : %s" % e
        Rhino.RhinoApp.InvokeOnUiThread(System.Action(lambda: Rhino.RhinoApp.WriteLine(msg)))

    Tasks.Task.Run(System.Action(work))
    Rhino.RhinoApp.WriteLine("R01 : analyse lancée en arrière-plan, Rhino reste utilisable.")


run()
