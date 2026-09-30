"""Exact georeferencing of the Oran Rhino model (brahim_ib9a.3dm).

Established in documentation/rapport_geo.md:
  * model XY = spherical Web Mercator (EPSG:3857) metres, relative to the
    origin lon -0.635 / lat 35.699 (fit on the Copernicus coastline:
    1.4 m from this point; scale 0.99998, rotation 0.09 deg -> pure Mercator)
  * model Z = true metres (layer HEIGHT_104 is 104 units tall); the
    "Millimeters" unit label of the file is wrong.

Output frame ("LOCAL"): Transverse Mercator centred on the same origin
(true metres, north-up, scale error < 3e-6 over the model), so (0,0) is the
same place as in the original model and the Rhino EarthAnchorPoint is exact.
"""
import numpy as np
from pyproj import Transformer

ORIGIN_LON, ORIGIN_LAT = -0.635, 35.699
LOCAL_CRS = (f"+proj=tmerc +lat_0={ORIGIN_LAT} +lon_0={ORIGIN_LON} +k=1 +x_0=0 +y_0=0 "
             "+ellps=WGS84 +units=m +no_defs")
_WM = Transformer.from_crs(4326, 3857, always_xy=True)
_WM_INV = Transformer.from_crs(3857, 4326, always_xy=True)
_LOC = Transformer.from_crs("EPSG:4326", LOCAL_CRS, always_xy=True)
_LOC_INV = Transformer.from_crs(LOCAL_CRS, "EPSG:4326", always_xy=True)
_UTM = Transformer.from_crs(LOCAL_CRS, "EPSG:32630", always_xy=True)
X0_WM, Y0_WM = _WM.transform(ORIGIN_LON, ORIGIN_LAT)


def model_to_lonlat(x, y):
    return _WM_INV.transform(np.asarray(x) + X0_WM, np.asarray(y) + Y0_WM)


def model_to_local(x, y):
    return _LOC.transform(*model_to_lonlat(x, y))


def local_to_lonlat(x, y):
    return _LOC_INV.transform(x, y)


def local_to_utm(x, y):
    return _UTM.transform(x, y)
