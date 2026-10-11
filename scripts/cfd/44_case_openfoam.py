"""Step 44 - OpenFOAM (ESI v1912) case generator for the Oran urban wind / thermal study.

One mesh for all wind directions: rectangular domain (model metres) around the three
sectors, terrain (Copernicus, from the maquette) + explicit buildings of the 3 sectors
(real footprints), snappyHexMesh.  For every scenario the inflow faces are the boundary
faces whose outward normal opposes the wind; the downstream ones are outlets; parallel
ones are slip.  Inflow = neutral atmospheric surface layer (Richards & Hoxey 1993):
    U(z) = u*/k ln((z - zg + z0)/z0), k = u*^2/sqrt(Cmu), eps = u*^3/(k (z - zg + z0))
with u* derived from the observed 10 m speed at Es Senia (z0 = 0.03 m, open airfield) and
transferred to the upstream roughness through a blending height of 60 m (log-law matching).

Usage:
  python 44_case_openfoam.py mesh  <case_dir> <coarse|medium>
  python 44_case_openfoam.py setup <case_dir> <scenario_id> [--thermal]   (after the mesh)
"""
import json
import math
import re
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
GEO = ROOT / "results/cfd/geometrie"
SECT = json.loads((ROOT / "results/cfd/secteurs.json").read_text())
DOMAIN = (-5500.0, -4500.0, 11500.0, 5500.0)
ZMIN, ZTOP = -10.0, 900.0
KAPPA, CMU = 0.41, 0.09
Z0_AIRPORT, ZBLEND = 0.03, 60.0
HDR = """FoamFile
{{
    version     2.0;
    format      ascii;
    class       {cls};
    object      {obj};
}}
"""
LEVELS = {"coarse": {"bg": 100.0, "bld": (3, 3), "bld_dist": 12.0, "box": 2, "ground": 1},
          "medium": {"bg": 80.0, "bld": (4, 4), "bld_dist": 8.0, "box": 2, "ground": 1}}


def w(path, cls, obj, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HDR.format(cls=cls, obj=obj) + body)


def mesh_case(case, level):
    L = LEVELS[level]
    case = Path(case)
    if case.exists():
        shutil.rmtree(case)
    tri = case / "constant/triSurface"; tri.mkdir(parents=True)
    names = ["ground"] + [f"buildings_{k}" for k in SECT]
    for n in names:
        shutil.copy(GEO / f"{n}.stl", tri / f"{n}.stl")
    nx = int(round((DOMAIN[2] - DOMAIN[0]) / L["bg"])); ny = int(round((DOMAIN[3] - DOMAIN[1]) / L["bg"]))
    nz = 26
    x0, y0, x1, y1 = DOMAIN
    w(case / "system/blockMeshDict", "dictionary", "blockMeshDict", f"""
convertToMeters 1;
vertices
(
 ({x0} {y0} {ZMIN}) ({x1} {y0} {ZMIN}) ({x1} {y1} {ZMIN}) ({x0} {y1} {ZMIN})
 ({x0} {y0} {ZTOP}) ({x1} {y0} {ZTOP}) ({x1} {y1} {ZTOP}) ({x0} {y1} {ZTOP})
);
blocks ( hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading (1 1 8) );
edges ();
boundary
(
 west  {{ type patch; faces ((0 4 7 3)); }}
 east  {{ type patch; faces ((1 2 6 5)); }}
 south {{ type patch; faces ((0 1 5 4)); }}
 north {{ type patch; faces ((3 7 6 2)); }}
 top   {{ type patch; faces ((4 5 6 7)); }}
 bottom {{ type wall; faces ((0 3 2 1)); }}
);
""")
    boxes = ""
    for k, s in SECT.items():
        b = s["model_box_m"]
        boxes += f"""    box_{k} {{ type searchableBox; min ({b[0] - 150} {b[1] - 150} -20); max ({b[2] + 150} {b[3] + 150} 260); }}\n"""
    geom = "".join(f"    {n}.stl {{ type triSurfaceMesh; name {n}; }}\n" for n in names)
    rsurf = f"        ground {{ level ({L['ground']} {L['ground'] + 1}); patchInfo {{ type wall; }} }}\n" + "".join(
        f"        buildings_{k} {{ level ({L['bld'][0]} {L['bld'][1]}); patchInfo {{ type wall; }} }}\n" for k in SECT)
    rreg = "".join(f"        box_{k} {{ mode inside; levels ((1E15 {L['box']})); }}\n" for k in SECT) + "".join(
        f"        buildings_{k} {{ mode distance; levels (({L['bld_dist']} {L['bld'][0]})); }}\n" for k in SECT)
    w(case / "system/snappyHexMeshDict", "dictionary", "snappyHexMeshDict", f"""
castellatedMesh true; snap true; addLayers false;
geometry
{{
{geom}{boxes}}};
castellatedMeshControls
{{
    maxLocalCells 4000000; maxGlobalCells 9000000; minRefinementCells 10; maxLoadUnbalance 0.10;
    nCellsBetweenLevels 3;
    features ();
    refinementSurfaces
    {{
{rsurf}    }}
    resolveFeatureAngle 30;
    refinementRegions
    {{
{rreg}    }}
    locationInMesh (2500.1 -1000.1 700.1);
    allowFreeStandingZoneFaces true;
}}
snapControls {{ nSmoothPatch 3; tolerance 2.0; nSolveIter 100; nRelaxIter 8; nFeatureSnapIter 10;
               implicitFeatureSnap true; explicitFeatureSnap false; multiRegionFeatureSnap false; }}
addLayersControls {{ relativeSizes true; layers {{}}; expansionRatio 1.2; finalLayerThickness 0.5; minThickness 0.1;
    nGrow 0; featureAngle 60; nRelaxIter 3; nSmoothSurfaceNormals 1; nSmoothNormals 3; nSmoothThickness 10;
    maxFaceThicknessRatio 0.5; maxThicknessToMedialRatio 0.3; minMedialAxisAngle 90; nBufferCellsNoExtrude 0;
    nLayerIter 50; }}
meshQualityControls {{ maxNonOrtho 65; maxBoundarySkewness 20; maxInternalSkewness 4; maxConcave 80;
    minVol 1e-13; minTetQuality -1e30; minArea -1; minTwist 0.02; minDeterminant 0.001; minFaceWeight 0.05;
    minVolRatio 0.01; minTriangleTwist -1; nSmoothScale 4; errorReduction 0.75; relaxed {{ maxNonOrtho 75; }} }}
mergeTolerance 1e-6;
""")
    w(case / "system/controlDict", "dictionary", "controlDict", """
application simpleFoam; startFrom latestTime; startTime 0; stopAt endTime; endTime 2000; deltaT 1;
writeControl timeStep; writeInterval 500; purgeWrite 2; writeFormat binary; writePrecision 7;
writeCompression off; timeFormat general; timePrecision 6; runTimeModifiable true;
""")
    w(case / "system/fvSchemes", "dictionary", "fvSchemes", "\nddtSchemes { default steadyState; }\n"
      "gradSchemes { default Gauss linear; }\ndivSchemes { default none; }\nlaplacianSchemes { default Gauss linear corrected; }\n"
      "interpolationSchemes { default linear; }\nsnGradSchemes { default corrected; }\n")
    w(case / "system/fvSolution", "dictionary", "fvSolution", "\nsolvers {}\n")
    w(case / "system/decomposeParDict", "dictionary", "decomposeParDict",
      "\nnumberOfSubdomains 4;\nmethod simple;\nsimpleCoeffs { n (2 2 1); delta 0.001; }\n")
    w(case / "system/meshQualityDict", "dictionary", "meshQualityDict", "\n#includeEtc \"caseDicts/mesh/generation/meshQualityDict\"\n")
    (case / "Allmesh").write_text(f"""#!/bin/bash
# mesh generation for level '{level}' (logs in log.*)
cd "$(dirname "$0")"
. /usr/share/openfoam/etc/bashrc 2>/dev/null; export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
blockMesh > log.blockMesh 2>&1 || exit 1
decomposePar -force > log.decomposePar.mesh 2>&1 || exit 1
mpirun --allow-run-as-root --oversubscribe -np 4 snappyHexMesh -overwrite -parallel > log.snappyHexMesh 2>&1 || exit 1
reconstructParMesh -constant > log.reconstructParMesh 2>&1 || exit 1
rm -rf processor*
checkMesh -allGeometry -allTopology > log.checkMesh 2>&1
# remove only the CRITICAL cells (negative volume, inverted faces, non-orthogonality > 90 deg);
# their exposed faces join the ground patch. Other warnings (skewness, concavity) are kept.
cat > system/topoSetDict <<EOT
FoamFile {{ version 2.0; format ascii; class dictionary; object topoSetDict; }}
actions
(
  {{ name badF; type faceSet; action new; source faceToFace; sets (meshQualityFaces); }}
  {{ name keep; type cellSet; action new; source faceToCell; set badF; option any; }}
  {{ name keep; type cellSet; action invert; }}
);
EOT
# strict-error criteria only (non-orthogonality > 88 deg, negative volume/inverted faces)
cat > system/meshQualityDict <<EOT
FoamFile {{ version 2.0; format ascii; class dictionary; object meshQualityDict; }}
maxNonOrtho 88; maxBoundarySkewness -1; maxInternalSkewness -1; maxConcave 180; minVol 1e-13;
minTetQuality -1e30; minArea -1; minTwist -1; minDeterminant -1; minFaceWeight -1; minVolRatio -1;
minTriangleTwist -1; nSmoothScale 4; errorReduction 0.75;
EOT
rm -rf constant/polyMesh/sets
checkMesh -meshQuality -constant > log.checkMesh.quality 2>&1
ls constant/polyMesh/sets > log.sets 2>&1
topoSet > log.topoSet 2>&1 && subsetMesh keep -patch ground -overwrite > log.subsetMesh 2>&1
rm -rf constant/polyMesh/sets
checkMesh -allGeometry -allTopology > log.checkMesh.final 2>&1
foamMeshToFluent > log.foamMeshToFluent 2>&1
echo MESH_DONE
""")
    (case / "Allmesh").chmod(0o755)
    print("mesh case written", case)


# ------------------------------------------------------------------ setup
def read_boundary(case):
    txt = (Path(case) / "constant/polyMesh/boundary").read_text()
    pats = {}
    for m in re.finditer(r"(\w+)\s*\{\s*type\s+(\w+);[^}]*?nFaces\s+(\d+);\s*startFace\s+(\d+);", txt, re.S):
        pats[m.group(1)] = {"type": m.group(2), "n": int(m.group(3)), "start": int(m.group(4))}
    return pats


def face_centres(case, pats):
    """Boundary face centres and z-component of the unit normal, from the polyMesh converted to
    ascii (foamFormatConvert run in the OpenFOAM environment with writeFormat ascii)."""
    import subprocess
    pm = Path(case) / "constant/polyMesh"
    if b"binary" in (pm / "points").read_bytes()[:1200]:
        cd = Path(case) / "system/controlDict"
        orig = cd.read_text()
        cd.write_text(re.sub(r"writeFormat\s+\w+;", "writeFormat ascii;", orig))
        subprocess.run(["bash", "-c", ". /usr/share/openfoam/etc/bashrc 2>/dev/null; foamFormatConvert -constant > log.foamFormatConvert 2>&1"],
                       cwd=case, check=True)
        cd.write_text(orig)
    def body(name):
        t = (pm / name).read_text(errors="ignore")
        h = t.index("}") if "FoamFile" in t else 0
        i = t.index("(", h)
        return t[i + 1:t.rindex(")")]
    P = np.fromstring(body("points").replace("(", " ").replace(")", " "), sep=" ").reshape(-1, 3)
    ints = np.fromstring(body("faces").replace("(", " ").replace(")", " "), sep=" ", dtype=np.int64)
    # ints = n0 a b c ... n1 ...  -> starts of each face
    starts = []; pos = 0; nf = 0
    lens = []
    while pos < len(ints):
        n = int(ints[pos]); starts.append(pos + 1); lens.append(n); pos += n + 1
    starts = np.array(starts); lens = np.array(lens)
    out = {}
    for name, p in pats.items():
        if p["n"] == 0:
            continue
        sl = slice(p["start"], p["start"] + p["n"])
        st, ln = starts[sl], lens[sl]
        fc = np.zeros((len(st), 3)); nz = np.zeros(len(st))
        for m in np.unique(ln):
            k = ln == m
            idx = ints[st[k][:, None] + np.arange(m)[None, :]]
            Q = P[idx]
            fc[k] = Q.mean(1)
            a = np.cross(Q[:, 1] - Q[:, 0], Q[:, 2] - Q[:, 0])
            nz[k] = a[:, 2] / (np.linalg.norm(a, axis=1) + 1e-30)
        out[name] = fc
        out[name + "__nz"] = nz
    return out


def nonuniform_scalar(v):
    return "nonuniform List<scalar> %d (%s)" % (len(v), " ".join(f"{x:.6g}" for x in v))


def nonuniform_vector(V):
    return "nonuniform List<vector> %d (%s)" % (len(V), " ".join(f"({a:.5g} {b:.5g} {c:.5g})" for a, b, c in V))


def setup(case, sid, thermal=False):
    case = Path(case)
    sc = {s["id"]: s for s in json.loads((ROOT / "results/cfd/wind/cfd_scenarios.json").read_text())}[sid]
    th = math.radians(sc["dir_from_deg"])
    flow = np.array([-math.sin(th), -math.cos(th), 0.0])          # direction the wind blows TO
    pats = read_boundary(case)
    fcs = face_centres(case, pats)
    g = np.load(GEO / "terrain_grid.npz"); xs, ys, Z = g["xs"], g["ys"], g["Z"]
    from scipy.interpolate import RegularGridInterpolator
    zg = RegularGridInterpolator((ys, xs), Z, bounds_error=False, fill_value=0.0)
    # observed 10 m speed (airfield z0 0.03) -> u* at the upstream roughness through the blending height
    ustar_ap = KAPPA * sc["U10_ref_ms"] / math.log((10 + Z0_AIRPORT) / Z0_AIRPORT)
    Ublend = ustar_ap / KAPPA * math.log((ZBLEND + Z0_AIRPORT) / Z0_AIRPORT)
    normals = {"west": (-1, 0, 0), "east": (1, 0, 0), "south": (0, -1, 0), "north": (0, 1, 0)}
    roles = {}
    for p, nrm in normals.items():
        d = float(np.dot(nrm, flow))
        roles[p] = "inlet" if d < -0.1 else ("outlet" if d > 0.1 else "slip")
    # ground roughness per face: sea 0.0002, explicit-building sectors 0.05, urban fabric 0.5, other land 0.1
    fc_g = fcs["ground"]
    gz = fc_g[:, 2]
    insec = np.zeros(len(fc_g), bool)
    for s in SECT.values():
        b = s["model_box_m"]
        insec |= (fc_g[:, 0] > b[0] - 300) & (fc_g[:, 0] < b[2] + 300) & (fc_g[:, 1] > b[1] - 300) & (fc_g[:, 1] < b[3] + 300)
    cov = urban_cover(fc_g[:, 0], fc_g[:, 1])
    z0g = np.where(gz < 0.6, 0.0002, np.where(insec, 0.05, np.where(cov > 0.15, 0.5, 0.1)))
    # upstream roughness = median z0 of the ground faces within 1.5 km of the inflow boundaries
    up = []
    for p, r in roles.items():
        if r == "inlet":
            fc = fcs[p]; up.append(fc)
    upc = np.vstack(up)
    from scipy.spatial import cKDTree
    t = cKDTree(fc_g[:, :2]); _, idx = t.query(upc[:, :2])
    z0_in = np.where(gz[idx] < 0.6, 0.0002, z0g[idx])
    U, K, E, T = {}, {}, {}, {}
    for p, r in roles.items():
        if r != "inlet":
            continue
        fc = fcs[p]
        _, ii = t.query(fc[:, :2]); z0f = np.where(gz[ii] < 0.6, 0.0002, z0g[ii])
        zgf = zg(np.c_[fc[:, 1], fc[:, 0]])
        h = np.maximum(fc[:, 2] - zgf, 0.0) + z0f
        us = KAPPA * Ublend / np.log((ZBLEND + z0f) / z0f)
        u = us / KAPPA * np.log(h / z0f)
        U[p] = u[:, None] * flow[None]; K[p] = us ** 2 / math.sqrt(CMU); E[p] = us ** 3 / (KAPPA * h)
    ustar_ref = float(np.median(KAPPA * Ublend / np.log((ZBLEND + z0_in) / z0_in)))
    k_ref = ustar_ref ** 2 / math.sqrt(CMU)
    bld = [p for p in pats if p.startswith("buildings_") and pats[p]["n"] > 0]

    def bc(field, val_in, wall_ground, wall_bld, out, slip="slip"):
        s = ""
        for p, r in roles.items():
            if pats.get(p, {}).get("n", 0) == 0:
                s += f"    {p} {{ type zeroGradient; }}\n" if field != "U" else f"    {p} {{ type slip; }}\n"
                continue
            if r == "inlet":
                s += f"    {p} {{ type fixedValue; value {val_in(p)}; }}\n"
            elif r == "outlet":
                s += f"    {p} {{ {out} }}\n"
            else:
                s += f"    {p} {{ type {slip}; }}\n"
        s += f"    top {{ type {slip}; }}\n    bottom {{ {wall_ground} }}\n    ground {{ {wall_ground} }}\n"
        s += "".join(f"    {b} {{ {wall_bld} }}\n" for b in bld)
        return s
    Uin = [float(np.max(np.linalg.norm(v, axis=1))) for v in U.values()]
    w(case / "0/U", "volVectorField", "U", f"""
dimensions [0 1 -1 0 0 0 0];
internalField uniform ({flow[0] * Ublend * 0.8:.4f} {flow[1] * Ublend * 0.8:.4f} 0);
boundaryField
{{
{bc("U", lambda p: nonuniform_vector(U[p]), "type noSlip;", "type noSlip;", "type inletOutlet; inletValue uniform (0 0 0); value uniform (0 0 0);")}}}
""")
    w(case / "0/p", "volScalarField", "p", f"""
dimensions [0 2 -2 0 0 0 0];
internalField uniform 0;
boundaryField
{{
{bc("p", lambda p: "uniform 0", "type zeroGradient;", "type zeroGradient;", "type fixedValue; value uniform 0;").replace("fixedValue; value uniform 0; }", "fixedValue; value uniform 0; }").replace("type fixedValue; value uniform 0;", "type fixedValue; value uniform 0;")}}}
""".replace("{ type fixedValue; value uniform 0; }\n", "{ type fixedValue; value uniform 0; }\n"))
    # inlet p must be zeroGradient
    pt = (case / "0/p").read_text()
    for p, r in roles.items():
        if r == "inlet":
            pt = pt.replace(f"    {p} {{ type fixedValue; value uniform 0; }}", f"    {p} {{ type zeroGradient; }}")
    (case / "0/p").write_text(pt)
    w(case / "0/k", "volScalarField", "k", f"""
dimensions [0 2 -2 0 0 0 0];
internalField uniform {k_ref:.5g};
boundaryField
{{
{bc("k", lambda p: nonuniform_scalar(np.full(len(fcs[p]), 0) + K[p]), "type kqRWallFunction; value uniform {0};".format(k_ref), "type kqRWallFunction; value uniform {0};".format(k_ref), "type inletOutlet; inletValue uniform {0}; value uniform {0};".format(k_ref), "zeroGradient")}}}
""")
    eps_ref = ustar_ref ** 3 / (KAPPA * 50)
    w(case / "0/epsilon", "volScalarField", "epsilon", f"""
dimensions [0 2 -3 0 0 0 0];
internalField uniform {eps_ref:.5g};
boundaryField
{{
{bc("epsilon", lambda p: nonuniform_scalar(E[p]), "type epsilonWallFunction; value uniform {0};".format(eps_ref), "type epsilonWallFunction; value uniform {0};".format(eps_ref), "type inletOutlet; inletValue uniform {0}; value uniform {0};".format(eps_ref), "zeroGradient")}}}
""")
    z0b = "z0 " + nonuniform_scalar(z0g) + ";"
    w(case / "0/nut", "volScalarField", "nut", f"""
dimensions [0 2 -1 0 0 0 0];
internalField uniform 0;
boundaryField
{{
{bc("nut", lambda p: "uniform 0", "type calculated; value uniform 0;", "type nutkWallFunction; value uniform 0;", "type calculated; value uniform 0;", "calculated; value uniform 0")}}}
""")
    nt = (case / "0/nut").read_text().replace("    ground { type calculated; value uniform 0; }",
                                               f"    ground {{ type nutkAtmRoughWallFunction; {z0b} value uniform 0; }}")
    for p, r in roles.items():
        if r == "inlet":
            nt = nt.replace(f"    {p} {{ type fixedValue; value uniform 0; }}", f"    {p} {{ type calculated; value uniform 0; }}")
    (case / "0/nut").write_text(nt.replace("type slip; }", "type calculated; value uniform 0; }"))
    for f in ("k", "epsilon"):
        t_ = (case / f"0/{f}").read_text().replace("type slip; }", "type slip; }")
        (case / f"0/{f}").write_text(t_)
    w(case / "constant/transportProperties", "dictionary", "transportProperties",
      "\ntransportModel Newtonian;\nnu [0 2 -1 0 0 0 0] 1.5e-05;\n"
      "beta [0 0 0 -1 0 0 0] 3.3e-03;\nTRef [0 0 0 1 0 0 0] 302.65;\nPr [0 0 0 0 0 0 0] 0.71;\nPrt [0 0 0 0 0 0 0] 0.85;\n")
    w(case / "constant/turbulenceProperties", "dictionary", "turbulenceProperties",
      "\nsimulationType RAS;\nRAS { RASModel realizableKE; turbulence on; printCoeffs on; }\n")
    probes = []
    for k, s in SECT.items():
        b = s["model_box_m"]
        for fx in (0.25, 0.5, 0.75):
            for fy in (0.25, 0.5, 0.75):
                x = b[0] + fx * (b[2] - b[0]); y = b[1] + fy * (b[3] - b[1])
                probes.append((x, y, float(zg([[y, x]])[0]) + 10.0))
    inl = " ".join(p for p, r in roles.items() if r == "inlet")
    outl = " ".join(p for p, r in roles.items() if r == "outlet")
    w(case / "system/controlDict", "dictionary", "controlDict", f"""
application {"buoyantBoussinesqSimpleFoam" if thermal else "simpleFoam"};
startFrom latestTime; startTime 0; stopAt endTime; endTime {2500 if thermal else 2000}; deltaT 1;
writeControl timeStep; writeInterval 500; purgeWrite 1; writeFormat ascii; writePrecision 7;
writeCompression off; timeFormat general; timePrecision 6; runTimeModifiable true;
libs ("libatmosphericModels.so");
// function objects disabled: this OpenFOAM v1912 Debian build aborts on any functionObject
// (IOstream "sha1" error); residuals, mass balance and sampling are computed by 45_postprocess.py
""")
    div = ("div(phi,U) bounded Gauss linearUpwind grad(U);\n div(phi,k) bounded Gauss upwind;\n div(phi,epsilon) bounded Gauss upwind;\n"
           " div((nuEff*dev2(T(grad(U))))) Gauss linear;\n div(phi,T) bounded Gauss linearUpwind grad(T);\n")
    w(case / "system/fvSchemes", "dictionary", "fvSchemes", f"""
ddtSchemes {{ default steadyState; }}
gradSchemes {{ default Gauss linear; grad(U) cellLimited Gauss linear 1; }}
divSchemes {{ default none;
 {div}}}
laplacianSchemes {{ default Gauss linear limited corrected 0.5; }}
interpolationSchemes {{ default linear; }}
snGradSchemes {{ default limited corrected 0.5; }}
wallDist {{ method meshWave; }}
""")
    pn = "p_rgh" if thermal else "p"
    w(case / "system/fvSolution", "dictionary", "fvSolution", f"""
solvers
{{
    {pn} {{ solver GAMG; smoother GaussSeidel; tolerance 1e-7; relTol 0.05; }}
    "(U|k|epsilon|T)" {{ solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol 0.1; }}
}}
SIMPLE {{ nNonOrthogonalCorrectors 1; consistent no; {"pRefCell 0; pRefValue 0;" if thermal else ""}
    residualControl {{ {pn} 1e-4; U 1e-5; "(k|epsilon)" 1e-5; {"T 1e-6;" if thermal else ""} }} }}
relaxationFactors {{ fields {{ {pn} 0.3; }} equations {{ U 0.6; "(k|epsilon)" 0.6; {"T 0.6;" if thermal else ""} }} }}
""")
    meta = {"scenario": sc, "flow_to": flow.tolist(), "roles": roles, "Ublend_60m": Ublend, "ustar_airport": ustar_ap,
            "ustar_upstream_median": ustar_ref, "z0_upstream_median": float(np.median(z0_in)),
            "U_inlet_max": max(Uin) if Uin else None, "probes": probes, "thermal": thermal,
            "z0_ground_classes": {"mer": 0.0002, "secteurs_bati_explicite": 0.05, "tissu_urbain": 0.5, "autre": 0.1},
            "z0_ground_counts": {str(v): int((z0g == v).sum()) for v in (0.0002, 0.05, 0.5, 0.1)}}
    # Fluent profiles (same inflow data for the ANSYS run, see rhino_ansys/ansys/A02)
    fl = case / "fluent"; fl.mkdir(exist_ok=True)
    for p in U:
        write_prof(fl / f"inlet_{p}.prof", f"inlet_{p}", fcs[p], {"u": U[p][:, 0], "v": U[p][:, 1], "w": U[p][:, 2],
                                                                   "k": np.full(len(fcs[p]), 0) + K[p], "epsilon": E[p]})
    if thermal:
        thermal_bc(case, fcs, pats, roles, bld, sc, zg)
        meta["thermal_bc"] = json.loads((case / "thermal_bc.json").read_text())
    (case / "scenario.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    (case / "Allrun").write_text(f"""#!/bin/bash
# solve scenario {sid} ({'thermal Boussinesq' if thermal else 'isothermal'}) on 4 cores, logs in log.*
cd "$(dirname "$0")"
. /usr/share/openfoam/etc/bashrc 2>/dev/null; export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
decomposePar -force > log.decomposePar 2>&1 || exit 1
mpirun --allow-run-as-root --oversubscribe -np 4 {"buoyantBoussinesqSimpleFoam" if thermal else "simpleFoam"} -parallel > log.solver 2>&1
reconstructPar -latestTime > log.reconstructPar 2>&1
rm -rf processor*
echo SOLVE_DONE
""")
    (case / "Allrun").chmod(0o755)
    print(json.dumps({k: meta[k] for k in ("roles", "Ublend_60m", "ustar_airport", "ustar_upstream_median",
                                            "z0_upstream_median", "U_inlet_max", "z0_ground_counts")}, indent=1))


def write_prof(path, name, fc, fields):
    """Fluent point profile: ((name point n) (x ...) (y ...) (z ...) (field ...) ...)"""
    n = len(fc)
    def row(k, v):
        return f"({k}\n" + "\n".join(f"{x:.6g}" for x in v) + "\n)\n"
    txt = f"(({name} point {n})\n" + row("x", fc[:, 0]) + row("y", fc[:, 1]) + row("z", fc[:, 2])
    txt += "".join(row(k, v) for k, v in fields.items()) + ")\n"
    Path(path).write_text(txt)


def urban_cover(x, y):
    """Building cover (white maquette buildings, 100 m cells) used only for the ground roughness."""
    cache = GEO / "urban_cover_100m.npz"
    if not cache.exists():
        P = np.fromfile(ROOT / "results/thermal_evolution/web/bld_pos.bin", np.float32).reshape(-1, 3, 3)
        Pm = np.stack([P[..., 0], -P[..., 2], P[..., 1]], -1)
        n = np.cross(Pm[:, 1] - Pm[:, 0], Pm[:, 2] - Pm[:, 0])[:, 2] * 0.5
        c = Pm.mean(1)
        up = n > 0
        x0, y0 = DOMAIN[0], DOMAIN[1]
        nx, ny = int((DOMAIN[2] - x0) / 100) + 1, int((DOMAIN[3] - y0) / 100) + 1
        ci = np.floor((c[up, 0] - x0) / 100).astype(int); ri = np.floor((c[up, 1] - y0) / 100).astype(int)
        ok = (ci >= 0) & (ci < nx) & (ri >= 0) & (ri < ny)
        C = np.zeros((ny, nx)); np.add.at(C, (ri[ok], ci[ok]), n[up][ok]); C /= 1e4
        np.savez(cache, C=C)
    C = np.load(cache)["C"]
    ci = np.clip(np.floor((x - DOMAIN[0]) / 100).astype(int), 0, C.shape[1] - 1)
    ri = np.clip(np.floor((y - DOMAIN[1]) / 100).astype(int), 0, C.shape[0] - 1)
    return C[ri, ci]


def thermal_bc(case, fcs, pats, roles, bld, sc, zg):
    """Model B: imposed SURFACE temperatures from the LST map of 10/07/2024 on roofs and ground
    (the satellite sees roofs and ground), walls at the air temperature (no data), inflow air at the observed
    mean summer-afternoon air temperature of Es Senia. Values are LST read from the figure."""
    import rasterio
    ds = rasterio.open(ROOT / "results/thermal/Oran_Thermal_2024.tif")
    A = ds.read(1, masked=True).filled(np.nan); tr = ds.transform
    def lst(x, y):
        dx = 3.5831863 - 9.84e-05 * x - 6.252e-04 * y; dy = -32.6866685 - 1.353e-04 * x + 4.518e-03 * y
        c = np.floor((x + dx - tr.c) / tr.a).astype(int); r = np.floor((tr.f - (y + dy)) / -tr.e).astype(int)
        ok = (c >= 0) & (c < A.shape[1]) & (r >= 0) & (r < A.shape[0])
        out = np.full(len(x), np.nan); out[ok] = A[r[ok], c[ok]]; return out
    Tair = sc.get("Tair_ref_C", 29.5)
    Tsea = 24.0      # documented assumption: Oran coastal SST in July (no LST over the sea in the map)
    rep = {"Tair_inflow_C": Tair, "T_sea_C_assumed": Tsea}
    def Tfield(name, fc, roof_only):
        v = lst(fc[:, 0], fc[:, 1])
        sea = fc[:, 2] < 0.6
        v = np.where(np.isnan(v), np.where(sea, Tsea, Tair), v)
        rep[name] = {"faces": int(len(v)), "with_LST": int(np.isfinite(lst(fc[:, 0], fc[:, 1])).sum()),
                     "mean_C": float(v.mean()), "max_C": float(v.max())}
        return v + 273.15
    s = ""
    for p, r in roles.items():
        if pats.get(p, {}).get("n", 0) == 0:
            s += f"    {p} {{ type zeroGradient; }}\n"; continue
        s += (f"    {p} {{ type fixedValue; value uniform {Tair + 273.15}; }}\n" if r == "inlet" else
              f"    {p} {{ type inletOutlet; inletValue uniform {Tair + 273.15}; value uniform {Tair + 273.15}; }}\n" if r == "outlet" else
              f"    {p} {{ type zeroGradient; }}\n")
    s += "    top { type zeroGradient; }\n    bottom { type zeroGradient; }\n"
    Tg = Tfield('ground', fcs['ground'], False)
    s += f"    ground {{ type fixedValue; value {nonuniform_scalar(Tg)}; }}\n"
    write_prof(Path(case) / "fluent" / "T_ground.prof", "T_ground", fcs["ground"], {"t": Tg})
    for b in bld:
        fc = fcs[b]
        # roofs (|nz| > 0.7): LST of the cell (the satellite sees roofs); walls: no data -> air temperature
        # (no imposed wall-to-air temperature difference)
        v = Tfield(b, fc, True)
        v = np.where(np.abs(fcs[b + "__nz"]) > 0.7, v, Tair + 273.15)
        rep[b]["roof_faces"] = int((np.abs(fcs[b + "__nz"]) > 0.7).sum())
        s += f"    {b} {{ type fixedValue; value {nonuniform_scalar(v)}; }}\n"
        write_prof(Path(case) / "fluent" / f"T_{b}.prof", f"T_{b}", fc, {"t": v})
    w(case / "0/T", "volScalarField", "T", f"""
dimensions [0 0 0 1 0 0 0];
internalField uniform {Tair + 273.15};
boundaryField
{{
{s}}}
""")
    # alphat + p_rgh
    al = "".join(f"    {p} {{ type calculated; value uniform 0; }}\n" for p in list(roles) + ["top", "bottom"])
    al += "".join(f"    {p} {{ type alphatJayatillekeWallFunction; Prt 0.85; value uniform 0; }}\n" for p in ["ground"] + bld)
    w(case / "0/alphat", "volScalarField", "alphat", f"\ndimensions [0 2 -1 0 0 0 0];\ninternalField uniform 0;\nboundaryField\n{{\n{al}}}\n")
    pr = (case / "0/p").read_text().replace("object      p;", "object      p_rgh;")
    pr = pr.replace("type zeroGradient;", "type fixedFluxPressure; value uniform 0;")
    (case / "0/p_rgh").write_text(pr)
    pc = (case / "0/p").read_text().replace("fixedValue; value uniform 0;", "calculated; value uniform 0;").replace(
        "type zeroGradient;", "type calculated; value uniform 0;")
    (case / "0/p").write_text(pc)
    w(case / "constant/g", "uniformDimensionedVectorField", "g", "\ndimensions [0 1 -2 0 0 0 0];\nvalue (0 0 -9.81);\n")
    w(case / "constant/hRef", "uniformDimensionedScalarField", "hRef", "\ndimensions [0 1 0 0 0 0 0];\nvalue 0;\n")
    (case / "thermal_bc.json").write_text(json.dumps(rep, indent=1))


if __name__ == "__main__":
    if sys.argv[1] == "mesh":
        mesh_case(sys.argv[2], sys.argv[3])
    else:
        setup(sys.argv[2], sys.argv[3], thermal="--thermal" in sys.argv)
