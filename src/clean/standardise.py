"""Categorical standardisation into new columns (strategy D-11, section 8.1) and the
zip_lookup join for customers and sellers (D-10.4).

No function modifies its input or any source column. All results are new Series aligned
with the input index.
"""

from __future__ import annotations

import re
import unicodedata

import pandas as pd

from src.staging.transform import to_category

from .schema import MALFORMED_CITY_PATTERN, URL_ENCODED_APOSTROPHE, ZIP_KEY

_MALFORMED = re.compile(MALFORMED_CITY_PATTERN)


def normalise_city(value: str) -> str:
    """Accent-free, lower case, trimmed (the NB03 7.3 normalised key)."""
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower().strip()


def city_key(city: pd.Series) -> pd.Series:
    """``city_key``: deterministic normalised key; missing stays missing."""
    mapping = {v: normalise_city(v) for v in city.dropna().unique()}
    return city.map(mapping).astype("string")


def decode_url_apostrophe(city: pd.Series) -> pd.Series:
    """Copy of ``city`` with only the URL-encoded apostrophe decoded."""
    return city.astype("string").str.replace(URL_ENCODED_APOSTROPHE, "'", regex=False)


def city_malformed(city: pd.Series) -> pd.Series:
    """True where a city value contains digits, separators, a state suffix or an e-mail.

    Uses Python ``re`` (the engine the NB03 baseline was measured with) on every value.
    """
    return city.map(lambda v: bool(_MALFORMED.search(v)) if isinstance(v, str) else False).astype(bool)


def state_std(state: pd.Series) -> pd.Series:
    """Upper-case, trimmed 2-letter code as a categorical of the observed values.

    Values are not forced into the UF vocabulary (that would turn unknown codes into
    missing values); validation counts values outside the 27 UFs instead.
    """
    return to_category(state.astype("string").str.strip().str.upper())


def lookup(left: pd.DataFrame, right: pd.DataFrame, left_on: str, right_on: str,
           columns: list[str]) -> pd.DataFrame:
    """Left join ``columns`` of ``right`` onto ``left`` with ``validate="many_to_one"``.

    Returns only the looked-up columns, aligned with ``left.index`` (same rows, same order).
    """
    keys = left[[left_on]].rename(columns={left_on: right_on})
    merged = keys.merge(right[[right_on, *columns]], on=right_on, how="left", validate="many_to_one", sort=False)
    if len(merged) != len(left):  # pragma: no cover - guaranteed by validate="many_to_one"
        raise AssertionError("lookup changed the row count")
    merged.index = left.index
    return merged[columns]


def attach_geolocation(df: pd.DataFrame, zip_column: str, zip_lookup: pd.DataFrame) -> pd.DataFrame:
    """``lat_centroid``, ``lng_centroid`` and ``dq_no_geolocation`` for each row of ``df``."""
    found = lookup(df, zip_lookup, zip_column, ZIP_KEY, ["lat_centroid", "lng_centroid"])
    found["dq_no_geolocation"] = (found["lat_centroid"].isna() | found["lng_centroid"].isna()).astype(bool)
    return found
