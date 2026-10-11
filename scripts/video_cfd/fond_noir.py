"""Replace the blue sky / fog / sea of already rendered maquette-only frames by the black technical
background (no re-render). Only for frames WITHOUT CFD colour overlays (blue would be ambiguous).
Pixels whose colour is bluish (b > r + 18 and b >= g) are mapped to black with a smooth weight; the
HUD text (white) and the neutral maquette (grey/white buildings, dark roads, green vegetation) are kept.
  python fond_noir.py <frames_dir> [<frames_dir> ...]
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def key(path, out=None):
    a = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    blue = np.clip((b - r - 18) / 30, 0, 1) * (b >= g)
    bg = np.array([5, 6, 7], np.float32)
    o = a * (1 - blue[..., None]) + bg * blue[..., None]
    Image.fromarray(o.round().astype(np.uint8)).save(out or path)


if __name__ == "__main__":
    for d in sys.argv[1:]:
        for f in sorted(Path(d).glob("*.png")):
            key(f)
        print(d, "ok")
