"""Extract Overture Maps features (buildings / places / divisions) inside a bbox.
Usage: python fetch_overture.py <theme> <type> <xmin> <ymin> <xmax> <ymax> <out.parquet>
Reads the public S3 release anonymously, prunes row groups with the bbox struct."""
import sys, pyarrow.dataset as ds, pyarrow.compute as pc, pyarrow.parquet as pq, s3fs
REL = "2026-09-23.0"
theme, typ = sys.argv[1], sys.argv[2]
xmin, ymin, xmax, ymax = map(float, sys.argv[3:7])
fs = s3fs.S3FileSystem(anon=True, client_kwargs={"region_name": "us-west-2"})
d = ds.dataset(f"overturemaps-us-west-2/release/{REL}/theme={theme}/type={typ}/", filesystem=fs, format="parquet")
f = ((pc.field("bbox", "xmin") < xmax) & (pc.field("bbox", "xmax") > xmin) &
     (pc.field("bbox", "ymin") < ymax) & (pc.field("bbox", "ymax") > ymin))
t = d.to_table(filter=f)
pq.write_table(t, sys.argv[7])
print(theme, typ, t.num_rows, "rows")
