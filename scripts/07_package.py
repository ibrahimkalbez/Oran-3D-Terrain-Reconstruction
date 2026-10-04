"""Package deliverables for download from GitHub (files > 100 MB are refused):
7z (LZMA2) archive split into 28 MiB volumes  name.7z.001, .002 ...
Re-assemble on Windows: put all parts in one folder, right-click the .001 file
-> 7-Zip -> Extract here.

Usage: python scripts/07_package.py <file|dir> [<file|dir> ...] --out livraison/<name>
       (a directory is stored with its own name, e.g. textures/...)
"""
import argparse
import hashlib
from pathlib import Path

import py7zr

PART = 28 * 1024 * 1024  # < 30 MiB chat upload limit and < 100 MB GitHub limit


def main():
    p = argparse.ArgumentParser()
    p.add_argument("files", nargs="+")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    arc = out.with_suffix(".7z")
    with py7zr.SevenZipFile(arc, "w", filters=[{"id": py7zr.FILTER_LZMA2, "preset": 7}]) as z:
        for f in a.files:
            if Path(f).is_dir():
                z.writeall(f, Path(f).name)
            else:
                z.write(f, Path(f).name)
    data = arc.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    n = 0
    for i in range(0, len(data), PART):
        n += 1
        Path(f"{arc}.{n:03d}").write_bytes(data[i:i + PART])
    arc.unlink()
    print(f"{arc.name}: {len(data) / 1e6:.1f} MB in {n} parts, sha256 {sha}")
    Path(f"{arc}.sha256.txt").write_text(f"{sha}  {arc.name}\n")


if __name__ == "__main__":
    main()
