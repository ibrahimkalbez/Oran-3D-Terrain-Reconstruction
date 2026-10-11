"""Step 42 - locate the three study sectors from named geodata (Overture Maps, public
AWS bucket): 'Gambetta' neighbourhood, 'Lions' boulevard / place, Oran centre.
Searches transportation segments (street names), divisions (neighbourhoods) and
places (POI) in the model bbox. Output: data/overture/noms_secteurs.json
"""
import json
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.fs as fs

ROOT = Path(__file__).resolve().parents[2]
BBOX = (-0.81, 35.59, -0.47, 35.80)
REL = "2026-09-23.1"
KEYS = ["gambetta", "lion", "lions", "centre", "ville nouvelle", "1er novembre", "larbi ben m", "emir abdelkader",
        "front de mer", "hôtel de ville", "hotel de ville", "derb", "sidi el houari", "choupot", "saint-eugène", "plateau"]


def scan(theme, typ, cols):
    s3 = fs.S3FileSystem(anonymous=True, region="us-west-2")
    d = ds.dataset(f"overturemaps-us-west-2/release/{REL}/theme={theme}/type={typ}/", filesystem=s3, format="parquet")
    f = ((pc.field("bbox", "xmin") > BBOX[0]) & (pc.field("bbox", "xmax") < BBOX[2]) &
         (pc.field("bbox", "ymin") > BBOX[1]) & (pc.field("bbox", "ymax") < BBOX[3]))
    return d.to_table(filter=f, columns=cols)


def main():
    out = {}
    for theme, typ in (("divisions", "division"), ("places", "place"), ("transportation", "segment")):
        t = scan(theme, typ, ["id", "names", "bbox"] + (["subtype", "class"] if theme != "places" else ["basic_category"]))
        rows = t.to_pylist()
        hits = []
        for r in rows:
            nm = r["names"] or {}
            com = nm.get("common") or []
            com = list(com.values()) if isinstance(com, dict) else [v for _, v in com]
            txt = " | ".join([str(nm.get("primary") or "")] + [str(v) for v in com])
            low = txt.lower()
            if any(k in low for k in KEYS):
                b = r["bbox"]
                hits.append({"name": txt, "theme": typ, "sub": r.get("subtype") or r.get("class") or r.get("basic_category"),
                             "lon": (b["xmin"] + b["xmax"]) / 2, "lat": (b["ymin"] + b["ymax"]) / 2,
                             "bbox": [b["xmin"], b["ymin"], b["xmax"], b["ymax"]]})
        out[typ] = hits
        print(typ, len(rows), "rows,", len(hits), "hits")
    p = ROOT / "data/overture/noms_secteurs.json"
    p.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    for typ, h in out.items():
        for x in h[:80]:
            if any(k in x["name"].lower() for k in ("gambetta", "lion")):
                print(typ, x["name"], x["sub"], round(x["lon"], 5), round(x["lat"], 5))


if __name__ == "__main__":
    main()
