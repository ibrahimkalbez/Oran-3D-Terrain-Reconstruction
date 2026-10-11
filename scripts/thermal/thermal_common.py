"""Shared settings for the 10/07/2024 thermal-map integration (scripts 20-24).

All parameters that depend on the source image (panel crop, legend bar,
ground control points) live in data/thermal/thermal_config.json so that the
scripts stay generic and every manual choice is traceable.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from oran_georef import LOCAL_CRS, ORIGIN_LAT, ORIGIN_LON  # noqa: E402,F401

DATA = Path(os.environ.get("THERMAL_DATA", ROOT / "data/thermal"))
CONFIG = DATA / "thermal_config.json"
WORK = Path(os.environ.get("THERMAL_WORK", ROOT / "results/thermal"))
PKG_NAME = "ORAN_THERMAL_2024_PACKAGE"
LAYER = "THERMAL_ORAN_2024_07_10"
DATE = "2024-07-10"


def load_config():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


# Thermal palette of the source map: blue (low) -> green -> yellow -> orange -> red (high).
# Used only when the legend bar of the document cannot be sampled.
FALLBACK_STOPS = [(0.00, (49, 54, 149)), (0.20, (69, 117, 180)), (0.40, (116, 196, 118)),
                  (0.55, (255, 255, 51)), (0.75, (253, 141, 60)), (1.00, (215, 25, 28))]


def palette(t, stops=FALLBACK_STOPS):
    """t in [0,1] -> uint8 RGB (piecewise linear)."""
    t = np.clip(np.asarray(t, float), 0, 1)
    xs = np.array([s[0] for s in stops])
    cs = np.array([s[1] for s in stops], float)
    return np.stack([np.interp(t, xs, cs[:, k]) for k in range(3)], -1).round().astype(np.uint8)


def lut_colors(values, lut_v, lut_rgb):
    """Map values to the RGB colours of the document legend (lut from script 20)."""
    v = np.asarray(values, float)
    return np.stack([np.interp(v, lut_v, lut_rgb[:, k]) for k in range(3)], -1).round().astype(np.uint8)
