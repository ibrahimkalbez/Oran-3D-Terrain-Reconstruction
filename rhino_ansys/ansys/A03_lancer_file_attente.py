"""
A03 - background queue for the Fluent runs (one run at a time: licence/CPU/RAM safe).

Launches A02 for each scenario of the list as a SEPARATE PROCESS (Rhino and the
user session stay free), writes queue.log and one log per run, never two solves at
the same time. Resume-safe: a scenario whose runs/<id>/report.json exists is skipped.

  python A03_lancer_file_attente.py <pkg_dir> S6_ete_NNO S2_SO S6_ete_NNO_thermal --cores 8
Windows: start /B pythonw A03_lancer_file_attente.py ...   (no console window)
"""
import subprocess
import sys
import time
from pathlib import Path

pkg = Path(sys.argv[1]); args = sys.argv[2:]
cores = args[args.index("--cores") + 1] if "--cores" in args else "8"
ids = [a for a in args if not a.startswith("--") and a != cores]
here = Path(__file__).parent
with open(pkg / "queue.log", "a", encoding="utf-8") as q:
    for sid in ids:
        if (pkg / "runs" / sid / "report.json").exists():
            q.write(f"{time.ctime()} skip {sid} (already done)\n"); continue
        q.write(f"{time.ctime()} start {sid}\n"); q.flush()
        with open(pkg / f"run_{sid}.log", "w", encoding="utf-8") as lf:
            r = subprocess.run([sys.executable, str(here / "A02_pyfluent_solve.py"), str(pkg), sid, "--cores", cores],
                               stdout=lf, stderr=subprocess.STDOUT)
        q.write(f"{time.ctime()} end {sid} code={r.returncode}\n"); q.flush()
