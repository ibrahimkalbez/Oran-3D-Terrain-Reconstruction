"""Geometry for the three nested scales of the Belgaid study.

Local metric frame: UTM 30N (EPSG:32630) shifted to the origin
O = (721986.85 E, 3958778.01 N) ~ (35.748 N, 0.545 W), centre of Belgaid.

Outputs (data/derived/):
  buildings_local.parquet : footprints in local frame + height + height source
  dem_25m.npy             : Copernicus GLO-30 resampled at 25 m (frame -9..9 km x -7..7 km)
  meso_z0.npy             : aerodynamic roughness length on the meso 100 m grid
Building heights: OSM num_floors x 3.0 m when known, otherwise footprint-area
classes (ESTIMATED) - to be replaced by the Rhino model heights when supplied."""
import json, numpy as np, pyarrow as pa, pyarrow.parquet as pq, shapely, pyproj, rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from scipy.ndimage import gaussian_filter

X0, Y0 = 721986.85, 3958778.01
EXT = (-9000, 9000, -7000, 7000)          # DEM / meso frame
tr = pyproj.Transformer.from_crs(4326, 32630, always_xy=True)
def to_local(g):
    return shapely.transform(g, lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1])) - [X0, Y0])

def area_class_height(a, cls):
    if cls in ("house", "detached", "semidetached_house"): return 7.0
    if a < 40: return 3.5          # annexes / sheds
    if a < 150: return 7.0         # R+1 individual housing
    if a < 400: return 10.0        # R+2
    if a < 1200: return 16.0       # R+4 collective housing
    return 12.0                    # large low-rise (commercial, equipment)

t = pq.read_table("data/buildings/meso_buildings.parquet").to_pylist()
geoms, H, src, ds = [], [], [], []
for r in t:
    g = to_local(shapely.from_wkb(r["geometry"]))
    if not g.is_valid: g = shapely.make_valid(g)
    g = shapely.get_parts(g); g = [p for p in g if p.geom_type == "Polygon" and p.area >= 15]
    if not g: continue
    g = shapely.MultiPolygon(g) if len(g) > 1 else g[0]
    if r["height"]:            h, s = float(r["height"]), "height"
    elif r["num_floors"]:      h, s = 3.0 * r["num_floors"] + 1.0, "floors"
    else:                      h, s = area_class_height(g.area, r["class"]), "estimated"
    geoms.append(g); H.append(h); src.append(s)
    ds.append(r["sources"][0]["dataset"] if r["sources"] else "")
H = np.array(H)
pq.write_table(pa.table({"geometry": shapely.to_wkb(geoms), "height": H, "height_source": src, "dataset": ds}),
               "data/derived/buildings_local.parquet")
cnt = {s: int(np.sum(np.array(src) == s)) for s in set(src)}

# --- DEM (already resampled at 25 m in the local frame by the previous step)
dem = np.load("data/copernicus/dem_local_25m.npy").astype(float)   # row 0 = north
np.save("data/derived/dem_25m.npy", dem)

# --- Meso roughness map (100 m): z0 = sea 2e-4 m; land 0.05 m (open/scrub);
# urban: z0 = 0.1 * mean height (Grimmond & Oke 1999), blended with plan fraction.
res, xm0, xm1, ym0, ym1 = 100, *EXT
nx, ny = (xm1 - xm0) // res, (ym1 - ym0) // res
fine = 5; T5 = from_origin(xm0, ym1, fine, fine); sh5 = ((ym1 - ym0) // fine, (xm1 - xm0) // fine)
lam = rasterize(((g, 1) for g in geoms), out_shape=sh5, transform=T5, dtype="uint8").astype(float)
hgt = rasterize(((g, h) for g, h in zip(geoms, H)), out_shape=sh5, transform=T5, dtype="float32")
b = res // fine
lam_c = lam.reshape(ny, b, nx, b).mean(axis=(1, 3))
hsum = hgt.reshape(ny, b, nx, b).sum(axis=(1, 3)); nb = lam.reshape(ny, b, nx, b).sum(axis=(1, 3))
hmean = np.where(nb > 0, hsum / np.maximum(nb, 1), 0)
dem100 = dem.reshape(ny, 4, nx, 4).mean(axis=(1, 3))
sea = dem100 < 0.5
w = np.clip(lam_c / 0.15, 0, 1)
z0 = (1 - w) * 0.05 + w * np.clip(0.1 * hmean, 0.05, 2.0)
z0[sea] = 2e-4
np.save("data/derived/meso_z0.npy", z0); np.save("data/derived/meso_lambda_p.npy", lam_c)
np.save("data/derived/meso_hmean.npy", hmean)
info = {"n_buildings": len(geoms), "height_source": cnt, "dataset": {d: ds.count(d) for d in set(ds)},
        "H_median": float(np.median(H)), "H_max": float(H.max()),
        "dem_min": float(dem.min()), "dem_max": float(dem.max()),
        "z0_urban_mean": float(z0[(lam_c > 0.1)].mean()), "lambda_p_max": float(lam_c.max())}
json.dump(info, open("data/derived/geometry_info.json", "w"), indent=1)
print(json.dumps(info, indent=1))
