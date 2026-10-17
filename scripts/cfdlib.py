"""Small helpers to build OpenFOAM v1912 cases from Python (no templates needed).

- terrain-following structured meshes: blockMesh on a unit-height box, then the
  points are mapped z = zs(x,y) + (Ztop - zs) * eta (sigma coordinate);
- Richards & Hoxey (1993) neutral ABL inflow profiles;
- writers for dictionaries and (non)uniform boundary fields."""
import os, re, subprocess, numpy as np

KAPPA, CMU = 0.41, 0.09
ENV = dict(os.environ, OMPI_ALLOW_RUN_AS_ROOT="1", OMPI_ALLOW_RUN_AS_ROOT_CONFIRM="1")

def header(cls, obj, loc=None):
    l = f'    location    "{loc}";\n' if loc else ""
    return ("FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
            f"    class       {cls};\n{l}    object      {obj};\n}}\n\n")

def write(case, rel, cls, body, loc=None):
    p = os.path.join(case, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    obj = os.path.basename(rel)
    open(p, "w").write(header(cls, obj, loc) + body)

def run(case, cmd, log=None, np_=1):
    """Run an OpenFOAM utility in `case`; parallel if np_>1. Returns exit code."""
    if np_ > 1:
        cmd = f"mpirun --allow-run-as-root --oversubscribe -np {np_} {cmd} -parallel"
    log = log or cmd.split()[0 if np_ == 1 else 6]
    with open(os.path.join(case, f"log.{log}"), "w") as f:
        return subprocess.call(cmd, shell=True, cwd=case, stdout=f, stderr=subprocess.STDOUT, env=ENV)

def nonuniform(vals):
    vals = np.asarray(vals)
    if vals.ndim == 1:
        s = "\n".join(f"{v:.6g}" for v in vals)
        return f"nonuniform List<scalar>\n{len(vals)}\n(\n{s}\n)"
    s = "\n".join(f"({v[0]:.6g} {v[1]:.6g} {v[2]:.6g})" for v in vals)
    return f"nonuniform List<vector>\n{len(vals)}\n(\n{s}\n)"

# ---------------------------------------------------------------- mesh
def block_mesh_dict(x0, x1, y0, y1, nx, ny, nz, grading_z):
    """Unit-height box (z in [0,1]) later mapped onto the terrain."""
    v = [(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0),
         (x0, y0, 1), (x1, y0, 1), (x1, y1, 1), (x0, y1, 1)]
    vs = "\n".join(f"    ({a} {b} {c})" for a, b, c in v)
    return f"""convertToMeters 1;
vertices
(
{vs}
);
blocks ( hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading (1 1 {grading_z}) );
edges ();
boundary
(
    ground {{ type wall;  faces ((0 3 2 1)); }}
    top    {{ type patch; faces ((4 5 6 7)); }}
    west   {{ type patch; faces ((0 4 7 3)); }}
    east   {{ type patch; faces ((1 2 6 5)); }}
    south  {{ type patch; faces ((0 1 5 4)); }}
    north  {{ type patch; faces ((3 7 6 2)); }}
);
mergePatchPairs ();
"""

def read_points(path):
    txt = open(path).read()
    m = re.search(r"\n(\d+)\s*\n\(", txt)
    n = int(m.group(1)); body = txt[m.end():]
    arr = np.array(re.findall(r"\(([^()]+)\)", body)[:n], dtype=object)
    return np.array([list(map(float, s.split())) for s in arr]), txt[:m.start()]

def write_points(path, pts, head):
    s = "\n".join(f"({p[0]:.4f} {p[1]:.4f} {p[2]:.4f})" for p in pts)
    open(path, "w").write(f"{head}\n{len(pts)}\n(\n{s}\n)\n")

def map_terrain(case, zs_func, ztop):
    p = os.path.join(case, "constant/polyMesh/points")
    pts, head = read_points(p)
    zs = zs_func(pts[:, 0], pts[:, 1])
    pts[:, 2] = zs + (ztop - zs) * pts[:, 2]
    write_points(p, pts, head)
    return zs

# ---------------------------------------------------------------- ABL
def rh_profile(zag, ustar, z0):
    """Richards & Hoxey 1993: U, k, epsilon at height above ground zag."""
    zag = np.maximum(zag, 0.0)
    U = ustar / KAPPA * np.log((zag + z0) / z0)
    k = np.full_like(zag, ustar**2 / np.sqrt(CMU))
    eps = ustar**3 / (KAPPA * (zag + z0))
    return U, k, eps

def ustar_matched(U10_ref, z0_ref, z0_new, zblend=500.0):
    """Friction velocity giving the same wind at the blending height zblend as
    a log profile with U(10 m) = U10_ref over z0_ref (Es Senia airfield)."""
    us_ref = KAPPA * U10_ref / np.log((10 + z0_ref) / z0_ref)
    Ub = us_ref / KAPPA * np.log((zblend + z0_ref) / z0_ref)
    return KAPPA * Ub / np.log((zblend + z0_new) / z0_new)

def wind_vector(dir_from_deg):
    """Unit vector of the flow for a meteorological direction (wind FROM)."""
    a = np.deg2rad(dir_from_deg)
    return np.array([-np.sin(a), -np.cos(a), 0.0])

def read_boundary(case):
    """Return {patch: (nFaces, startFace)} from constant/polyMesh/boundary."""
    txt = open(os.path.join(case, "constant/polyMesh/boundary")).read()
    out = {}
    for m in re.finditer(r"(\w+)\s*\{([^}]*)\}", txt):
        b = m.group(2)
        if "nFaces" in b:
            out[m.group(1)] = (int(re.search(r"nFaces\s+(\d+)", b).group(1)),
                               int(re.search(r"startFace\s+(\d+)", b).group(1)))
    return out

def read_field_internal(path):
    """Read an ascii volScalar/volVector internalField (nonuniform or uniform)."""
    txt = open(path).read()
    m = re.search(r"internalField\s+nonuniform\s+List<(\w+)>\s*\n?(\d+)\s*\n?\(", txt)
    if not m:
        raise ValueError("uniform field " + path)
    n = int(m.group(2)); body = txt[m.end():]
    if m.group(1) == "scalar":
        return np.array(body.split(None, n)[:n], float)
    vals = re.findall(r"\(([^()]+)\)", body[: body.find("\n)\n") + 3])[:n]
    return np.array([list(map(float, v.split())) for v in vals])

def read_patch_field(path, patch):
    """Face values of `patch` in an ascii boundary field (value nonuniform)."""
    txt = open(path).read()
    i = re.search(r"\n\s*" + patch + r"\s*\n\s*\{", txt).end()
    blk = txt[i:]
    m = re.search(r"value\s+nonuniform\s+List<(\w+)>\s*\n?(\d+)\s*\n?\(", blk)
    n = int(m.group(2)); body = blk[m.end():]
    if m.group(1) == "scalar":
        return np.array(body.split(None, n)[:n], float)
    vals = re.findall(r"\(([^()]+)\)", body)[:n]
    return np.array([list(map(float, v.split())) for v in vals])
