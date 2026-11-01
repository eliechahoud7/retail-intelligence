"""Staging validation checks, using the three check classes of strategy section 11.

* blocking   : pipeline invariants (outputs vs inputs of the same run); must always hold.
* regression : comparisons with baselines measured in notebooks 01-03; only evaluated
               when the raw files match the approved checksums.
* warning    : diagnostics without a measured baseline; logged, never failing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from src.ingestion import DATASETS, ZIP_COLUMNS

from . import geolocation as geo_mod
from .product_flags import ROW_FLAGS, LISTING_FLAGS, PHYSICAL_FLAGS, group_equivalence
from .schema import (BASELINE_DATES, BASELINE_GEOLOCATION, BASELINE_LEADING_ZERO_ZIPS,
                     BASELINE_MONETARY_TOTALS, BASELINE_PRODUCT_FLAGS, BASELINE_ROWS,
                     BASELINE_UNTRANSLATED_PRODUCTS, CATEGORICAL_COLUMNS, CORE_FOREIGN_KEYS,
                     DATE_COLUMNS, DATE_FORMAT, PRIMARY_KEYS)
from .transform import StagedDataset, staged_column_name

BLOCKING, REGRESSION, WARNING = "blocking", "regression", "warning"


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    check_class: str
    description: str
    expected: Any
    observed: Any
    passed: bool | None  # None for warnings (informational)

    def as_dict(self) -> dict:
        d = asdict(self)
        for k in ("expected", "observed"):
            d[k] = _jsonable(d[k])
        return d


def _jsonable(v):
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if hasattr(v, "item"):  # numpy scalar
        return v.item()
    if isinstance(v, pd.Timestamp):
        return v.strftime(DATE_FORMAT)
    return v


def _check(cid, cls, desc, expected, observed, passed=None):
    if cls != WARNING and passed is None:
        passed = expected == observed
    return CheckResult(cid, cls, desc, expected, observed, passed)


# ----------------------------------------------------------------------------- blocking
def expected_staged_columns(name: str) -> list[str]:
    spec = next(s for s in DATASETS if s.name == name)
    return [staged_column_name(name, c) for c in spec.column_names]


def expected_staged_dtype(name: str, raw_column: str, ingested_dtype: str) -> str:
    if raw_column in DATE_COLUMNS.get(name, ()):
        return "datetime64"
    if raw_column in CATEGORICAL_COLUMNS.get(name, ()):
        return "category"
    return ingested_dtype


def _dtype_matches(actual, expected: str) -> bool:
    if expected == "datetime64":
        return pd.api.types.is_datetime64_dtype(actual) and getattr(actual, "tz", None) is None
    return str(actual) == expected


def blocking_checks(ingested: dict[str, pd.DataFrame], staged: dict[str, StagedDataset],
                    geo_rows: pd.DataFrame, geo_points: pd.DataFrame, flags: pd.DataFrame) -> list[CheckResult]:
    out: list[CheckResult] = []

    for spec in DATASETS:
        name, raw, st = spec.name, ingested[spec.name], staged[spec.name]
        df = st.frame
        out.append(_check(f"V02.{name}", BLOCKING, f"{name}: staged rows = ingested rows", len(raw), len(df)))

        cols_ok = list(df.columns) == expected_staged_columns(name)
        bad_types = [c for c in spec.column_names
                     if not _dtype_matches(df[staged_column_name(name, c)].dtype,
                                           expected_staged_dtype(name, c, str(raw[c].dtype)))] if cols_ok else ["<columns differ>"]
        out.append(_check(f"V03.{name}", BLOCKING, f"{name}: staged columns and dtypes match the staging schema",
                          [], bad_types, passed=cols_ok and not bad_types))

        missing_diff = {c: (int(raw[c].isna().sum()), int(df[staged_column_name(name, c)].isna().sum()))
                        for c in spec.column_names}
        missing_diff = {c: v for c, v in missing_diff.items() if v[0] != v[1]}
        out.append(_check(f"V08.{name}", BLOCKING, f"{name}: missing count per column unchanged", {}, missing_diff))

        changed = []
        for c in spec.column_names:
            s_new = df[staged_column_name(name, c)]
            if c in DATE_COLUMNS.get(name, ()):
                text = s_new.dt.strftime(DATE_FORMAT)
                mask = raw[c].notna()
                if not text[mask].astype("string").equals(raw[c][mask].astype("string")):
                    changed.append(c)
            elif not s_new.astype(raw[c].dtype).equals(raw[c]):
                changed.append(c)
        out.append(_check(f"V14.{name}", BLOCKING,
                          f"{name}: every value unchanged (dates re-formatted with the parse format equal the raw text)",
                          [], changed))

        if name in PRIMARY_KEYS:
            key = list(PRIMARY_KEYS[name])
            keyed = [staged_column_name(name, k) for k in key]
            dup = int(df.duplicated(subset=keyed).sum())
            nulls = int(df[keyed].isna().any(axis=1).sum())
            out.append(_check(f"V04.{name}", BLOCKING, f"{name}: primary key {tuple(key)} unique and non-null",
                              (0, 0), (dup, nulls)))

    for name, cols in DATE_COLUMNS.items():
        coerced = staged[name].coerced_dates
        out.append(_check(f"V07.{name}", BLOCKING, f"{name}: 0 date values failed to parse",
                          {c: 0 for c in cols}, {c: coerced.get(c) for c in cols}))

    for name, col in ZIP_COLUMNS.items():
        s = staged[name].frame[col]
        bad = int((~s.str.fullmatch(r"\d{5}").fillna(False)).sum())
        out.append(_check(f"V06.{name}", BLOCKING, f"{name}.{col}: all values are 5-digit strings", 0, bad))

    for child, ccol, parent, pcol in CORE_FOREIGN_KEYS:
        c, p = staged[child].frame[ccol], staged[parent].frame[pcol]
        orphans = int((c.notna() & ~c.isin(p)).sum())
        out.append(_check(f"V05.{child}.{ccol}", BLOCKING, f"{child}.{ccol} -> {parent}.{pcol}: no orphans", 0, orphans))

    n_geo = len(staged["geolocation"].frame)
    out += [
        _check("V12.rows_sum", BLOCKING, "geolocation_rows: sum(occurrence_count) = geolocation rows",
               n_geo, int(geo_rows["occurrence_count"].sum())),
        _check("V12.points_sum", BLOCKING, "geolocation_points: sum(raw_row_count) = geolocation rows",
               n_geo, int(geo_points["raw_row_count"].sum())),
        _check("V12.rows_unique", BLOCKING, "geolocation_rows: no duplicate full rows",
               0, int(geo_rows.duplicated(subset=geo_mod.FULL_ROW).sum())),
        _check("V12.points_unique", BLOCKING, "geolocation_points: no duplicate (zip, lat, lng)",
               0, int(geo_points.duplicated(subset=geo_mod.POINT).sum())),
        _check("V12.points_from_rows", BLOCKING, "geolocation_points: sum(n_full_rows) = geolocation_rows rows",
               len(geo_rows), int(geo_points["n_full_rows"].sum())),
        _check("V12.zip_format", BLOCKING, "geolocation_rows / points: zip prefixes are 5-digit strings", (0, 0),
               (int((~geo_rows[geo_mod.ZIP].str.fullmatch(r"\d{5}")).sum()),
                int((~geo_points[geo_mod.ZIP].str.fullmatch(r"\d{5}")).sum()))),
    ]

    products = staged["products"].frame
    out.append(_check("V02.products_metadata_flags", BLOCKING, "products_metadata_flags: one row per product, same order",
                      True, bool(flags["product_id"].equals(products["product_id"]))))
    return out


# ----------------------------------------------------------------------------- regression
def regression_checks(staged: dict[str, StagedDataset], geo_rows: pd.DataFrame,
                      geo_points: pd.DataFrame, flags: pd.DataFrame) -> list[CheckResult]:
    out: list[CheckResult] = []
    for name, n in BASELINE_ROWS.items():
        out.append(_check(f"R.rows.{name}", REGRESSION, f"{name}: row count (NB01)", n, len(staged[name].frame)))

    for (name, col), (n, lo, hi) in BASELINE_DATES.items():
        s = staged[name].frame[col]
        out.append(_check(f"R.dates.{name}.{col}", REGRESSION, f"{name}.{col}: non-missing, min, max (NB03 4.1)",
                          (n, lo, hi), (int(s.notna().sum()), s.min().strftime(DATE_FORMAT), s.max().strftime(DATE_FORMAT))))

    for name, n in BASELINE_LEADING_ZERO_ZIPS.items():
        s = staged[name].frame[ZIP_COLUMNS[name]]
        out.append(_check(f"R.zip_leading_zero.{name}", REGRESSION, f"{name}: zip prefixes starting with '0' (NB03 4.3)",
                          n, int(s.str.startswith("0").sum())))

    for (name, col), total in BASELINE_MONETARY_TOTALS.items():
        out.append(_check(f"R.total.{name}.{col}", REGRESSION, f"{name}.{col}: sum (NB02/NB03)",
                          total, round(float(staged[name].frame[col].sum()), 2)))

    products, translation = staged["products"].frame, staged["translation"].frame
    cat = products["product_category_name"]
    out.append(_check("R.untranslated_products", REGRESSION, "products with a category absent from translation (NB02 3.2)",
                      BASELINE_UNTRANSLATED_PRODUCTS,
                      int((cat.notna() & ~cat.isin(translation["product_category_name"])).sum())))

    out += [
        _check("R.geolocation_rows", REGRESSION, "geolocation_rows: distinct full rows (NB02 2.2)",
               BASELINE_GEOLOCATION["geolocation_rows"], len(geo_rows)),
        _check("R.geolocation_points", REGRESSION, "geolocation_points: distinct (zip, lat, lng) (NB02 5.3)",
               BASELINE_GEOLOCATION["geolocation_points"], len(geo_points)),
        _check("R.geolocation_zips", REGRESSION, "distinct zip prefixes in geolocation (NB02 5.3)",
               BASELINE_GEOLOCATION["zip_prefixes"], int(geo_points[geo_mod.ZIP].nunique())),
        _check("R.geolocation_outside_rows", REGRESSION, "raw rows represented by points outside Brazil (NB03 6.5)",
               BASELINE_GEOLOCATION["raw_rows_outside_brazil"],
               int(geo_points.loc[geo_points["dq_outside_brazil"], "raw_row_count"].sum())),
    ]

    for flag, n in BASELINE_PRODUCT_FLAGS.items():
        out.append(_check(f"R.flag.{flag}", REGRESSION, f"products_metadata_flags.{flag} count (strategy 4.3)",
                          n, int(flags[flag].sum())))
    for group, (any_n, all_n) in group_equivalence(flags).items():
        out.append(_check(f"R.flag_equivalence.{group}", REGRESSION,
                          f"{group} fields: products with ANY missing = products with ALL missing (strategy 4.3)",
                          any_n, all_n))
    return out


# ----------------------------------------------------------------------------- warnings
def warning_checks(geo_points: pd.DataFrame) -> list[CheckResult]:
    return [
        _check("W02", WARNING, "distinct (zip, lat, lng) points flagged outside Brazil (not measured in NB01-03)",
               None, int(geo_points["dq_outside_brazil"].sum())),
        _check("W.geo_points_multi_city", WARNING, "points carried by more than one city spelling (explains part of the 18,178 gap)",
               None, int((geo_points["n_city_spellings"] > 1).sum())),
        _check("W.geo_points_multi_state", WARNING, "points carried by more than one state",
               None, int((geo_points["n_states"] > 1).sum())),
    ]


def summarize(checks: list[CheckResult]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for c in checks:
        s = summary.setdefault(c.check_class, {"total": 0, "passed": 0, "failed": 0})
        s["total"] += 1
        if c.passed is True:
            s["passed"] += 1
        elif c.passed is False:
            s["failed"] += 1
    return summary


def failed(checks: list[CheckResult]) -> list[CheckResult]:
    return [c for c in checks if c.passed is False]


__all__ = ["CheckResult", "blocking_checks", "regression_checks", "warning_checks", "summarize", "failed",
           "BLOCKING", "REGRESSION", "WARNING", "ROW_FLAGS", "LISTING_FLAGS", "PHYSICAL_FLAGS"]
