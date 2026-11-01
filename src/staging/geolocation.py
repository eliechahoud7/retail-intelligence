"""Lossless geolocation structures (strategy D-10, structural part only).

* ``geolocation_rows``  : one row per distinct full row (all 5 columns), with
  ``occurrence_count`` = number of identical raw rows. Repeating each row
  ``occurrence_count`` times reconstructs the raw table exactly.
* ``geolocation_points``: one row per distinct (zip, lat, lng), counting each coordinate
  once whatever its city/state text, with occurrence information preserved.

ZIP centroids (``zip_lookup``) are intentionally NOT computed here.
"""

from __future__ import annotations

import pandas as pd

from .schema import BRAZIL_BBOX

ZIP, LAT, LNG = "geolocation_zip_code_prefix", "geolocation_lat", "geolocation_lng"
CITY, STATE = "geolocation_city", "geolocation_state"
FULL_ROW = [ZIP, LAT, LNG, CITY, STATE]
POINT = [ZIP, LAT, LNG]


def build_geolocation_rows(geo: pd.DataFrame) -> pd.DataFrame:
    """Collapse exact duplicate full rows, keeping their multiplicity."""
    rows = (geo.groupby(FULL_ROW, sort=True, observed=True, dropna=False)
            .size().rename("occurrence_count").reset_index())
    rows["occurrence_count"] = rows["occurrence_count"].astype("int64")
    return rows


def outside_brazil(lat: pd.Series, lng: pd.Series) -> pd.Series:
    """True where a coordinate falls outside the approximate national bounding box."""
    return ~(lat.between(*BRAZIL_BBOX["lat"]) & lng.between(*BRAZIL_BBOX["lng"]))


def build_geolocation_points(rows: pd.DataFrame) -> pd.DataFrame:
    """One row per distinct (zip, lat, lng), built from ``geolocation_rows``."""
    points = (rows.groupby(POINT, sort=True, dropna=False)
              .agg(raw_row_count=("occurrence_count", "sum"),
                   n_full_rows=("occurrence_count", "size"),
                   n_city_spellings=(CITY, "nunique"),
                   n_states=(STATE, "nunique"))
              .reset_index())
    for col in ("raw_row_count", "n_full_rows", "n_city_spellings", "n_states"):
        points[col] = points[col].astype("int64")
    points["dq_outside_brazil"] = outside_brazil(points[LAT], points[LNG])
    return points
