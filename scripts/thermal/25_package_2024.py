"""Step 25 - assemble ORAN_THERMAL_2024_PACKAGE/ and the archive
ORAN_THERMAL_2024_COMPLETE.zip, then check it.

Only files that really exist are copied (no placeholder). The archive is
re-opened, every member CRC-tested and the .3dm members re-read with rhino3dm.
Usage: python scripts/thermal/25_package_2024.py <dir containing the .3dm files>
"""
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

import rhino3dm as r3

from thermal_common import DATA, LAYER, PKG_NAME, ROOT, WORK

DIRS = {
    "01_RHINO_MODEL": ["Oran_Thermal_2024_3D.3dm", "Oran_Original_Backup.3dm"],
    "02_THERMAL_RASTER": ["Oran_Thermal_2024.tif", "Oran_Thermal_2024_UTM30N.tif", "panel_2024.png",
                          "values_px.npy", "legend_lut.npz"],
    "03_GIS_DATA": ["Oran_Thermal_2024.gpkg"],
    "04_STATISTICS": ["Oran_Thermal_2024_Statistics.csv", "Oran_Thermal_2024_Grille_1km.csv"],
    "06_METADATA_DOCUMENTATION": ["Oran_Thermal_2024_Metadata.json", "README_FR.md",
                                  "RAPPORT_THERMIQUE_2024_FR.md", "RAPPORT_THERMIQUE_2024_FR.pdf"],
    "07_PREVIEWS": ["Oran_Thermal_2024_carte_2D.png", "histogramme_2024.png", "apercu_3d_thermique.png",
                    "apercu_3d_thermique_centre.png", "apercu_zones_grille_2024.png", "comparaison_panneau_2024.png"],
    "08_VALIDATION_REPORT": ["validation_report.json", "VALIDATION_REPORT_FR.md", "extract_report.json",
                             "georef_report.json", "rhino_thermal_report.json",
                             "registration_2024.json", "controle_calage_2024.png", "thermal_config.json"],
}


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    model_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else WORK
    out_root = Path(sys.argv[2]) if len(sys.argv) > 2 else WORK.parent / "package"
    pkg = out_root / PKG_NAME
    shutil.rmtree(pkg, ignore_errors=True)
    missing = []
    search = [model_dir, WORK, DATA, ROOT / "documentation/thermal"]
    for d, names in DIRS.items():
        (pkg / d).mkdir(parents=True, exist_ok=True)
        for n in names:
            src = next((s / n for s in search if (s / n).exists()), None)
            if src is None:
                missing.append(f"{d}/{n}"); continue
            shutil.copy2(src, pkg / d / n)
    (pkg / "05_SCRIPTS").mkdir(exist_ok=True)
    for s in sorted((ROOT / "scripts/thermal").glob("*.py")) + [ROOT / "scripts/oran_georef.py"]:
        shutil.copy2(s, pkg / "05_SCRIPTS" / s.name)
    src_dir = pkg / "02_THERMAL_RASTER/source_document"
    src_dir.mkdir(exist_ok=True)
    for f in DATA.iterdir():
        if f.is_file():
            shutil.copy2(f, src_dir / f.name)
    manifest = {str(p.relative_to(pkg)): {"bytes": p.stat().st_size, "sha256": sha(p)}
                for p in sorted(pkg.rglob("*")) if p.is_file()}
    (pkg / "MANIFEST_SHA256.json").write_text(json.dumps(manifest, indent=1))
    z = out_root / "ORAN_THERMAL_2024_COMPLETE.zip"
    z.unlink(missing_ok=True)
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
        for p in sorted(pkg.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(out_root))
    # ---- check the archive
    chk = {"zip": str(z), "zip_bytes": z.stat().st_size, "missing_files": missing}
    with zipfile.ZipFile(z) as zf:
        chk["members"] = len(zf.namelist())
        chk["crc_test_first_bad"] = zf.testzip()
        tmp = out_root / "_zipcheck"; shutil.rmtree(tmp, ignore_errors=True)
        for n in zf.namelist():
            if n.endswith(".3dm"):
                zf.extract(n, tmp)
                m = r3.File3dm.Read(str(tmp / n))
                names = [m.Layers[i].Name for i in range(len(m.Layers))]
                chk[Path(n).name] = {"opens": m is not None, "objects": len(m.Objects),
                                     "thermal_layer": LAYER in names}
        shutil.rmtree(tmp, ignore_errors=True)
    chk["zip_sha256"] = sha(z)
    # split copy for git hosting (each part < 100 MB); join: 7-Zip opens .001 directly,
    # or `copy /b ORAN...zip.001+ORAN...zip.002+... ORAN_THERMAL_2024_COMPLETE.zip` / `cat ... > ...zip`
    part = int(float(sys.argv[3]) * 1024 * 1024) if len(sys.argv) > 3 else 0
    if part:
        parts = []
        with open(z, "rb") as fsrc:
            k = 1
            while True:
                b = fsrc.read(part)
                if not b:
                    break
                pp = out_root / f"{z.name}.{k:03d}"
                pp.write_bytes(b); parts.append(pp.name); k += 1
        (out_root / f"{z.name}.sha256.txt").write_text(f"{chk['zip_sha256']}  {z.name}\n")
        chk["split_parts"] = parts
    (out_root / "zip_check.json").write_text(json.dumps(chk, indent=1))
    print(json.dumps(chk, indent=1))


if __name__ == "__main__":
    main()
