"""Download the Overture Maps data of the Oran area (public AWS bucket, anonymous).

  * buildings (OpenStreetMap + Google Open Buildings + Microsoft ML footprints,
    with OSM height / building:levels when mapped)
  * transportation segments (road centre lines with OSM class/subclass:
    crosswalk, sidewalk, link, bridge/tunnel flags ...)

Used by scripts 14 (georeferencing check, storeys) and 15 (road axes, crossings).
Output: data/overture/oran_buildings.parquet, data/overture/oran_segments.parquet
Usage : python scripts/13b_fetch_overture.py [--release 2026-09-23.1]
"""
import argparse
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.fs as fs
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/overture"
BBOX = (-0.81, 35.59, -0.47, 35.80)   # lon/lat, covers the whole model


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--release", default="2026-09-23.1")
    a = p.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    s3 = fs.S3FileSystem(anonymous=True, region="us-west-2")
    root = f"overturemaps-us-west-2/release/{a.release}"
    w, s, e, n = BBOX
    f = ((pc.field("bbox", "xmin") > w) & (pc.field("bbox", "xmax") < e) &
         (pc.field("bbox", "ymin") > s) & (pc.field("bbox", "ymax") < n))
    for theme, typ, cols, name in [
            ("buildings", "building", ["id", "height", "num_floors", "roof_shape", "class", "sources", "geometry"],
             "oran_buildings.parquet"),
            ("transportation", "segment", ["id", "class", "subclass", "subtype", "road_flags", "width_rules", "geometry"],
             "oran_segments.parquet")]:
        d = ds.dataset(f"{root}/theme={theme}/type={typ}/", filesystem=s3, format="parquet")
        t = d.to_table(columns=cols, filter=f)
        pq.write_table(t, OUT / name)
        print(f"{name}: {t.num_rows} features")


if __name__ == "__main__":
    main()
