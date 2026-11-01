"""Clean-layer validation (strategy section 11), using the staging check classes.

* blocking   : invariants comparing clean outputs with the staged inputs of the same run
               (V03-V06, V08, V09, V12 clean part, V14, plus lineage and reuse checks).
* regression : baselines of section 11.3 that concern the clean layer; evaluated only
               when the raw files match the approved checksums.
* warning    : W03, W04 and other logged diagnostics.
"""

from __future__ import annotations

import pandas as pd

from src.ingestion import ZIP_COLUMNS
from src.staging import geolocation as geo
from src.staging.product_flags import group_equivalence
from src.staging.schema import (BASELINE_MONETARY_TOTALS, BASELINE_PRODUCT_FLAGS, BASELINE_ROWS, BRAZIL_BBOX,
                                CORE_FOREIGN_KEYS, PRIMARY_KEYS)
from src.staging.validation import BLOCKING, REGRESSION, WARNING, CheckResult
from src.staging.validation import _check as check

from .schema import (ADDED_COLUMNS, BASELINE_CLEAN_FLAGS, BASELINE_DOMAIN_RULES, BASELINE_STRUCTURE, BRAZIL_UFS,
                     OBSERVED_VOCABULARIES, REFERENCE_SCHEMAS, SOURCE_TABLES, STATE_COLUMNS, ZIP_KEY,
                     added_column_names)

MONETARY = tuple(BASELINE_MONETARY_TOTALS)  # ((table, column), ...)


def _dtype_ok(actual, expected: str) -> bool:
    if expected == "category":
        return isinstance(actual, pd.CategoricalDtype)
    return str(actual) == expected  # "string" = StringDtype(na_value=<NA>), as in staging (not pandas' "str")


# ----------------------------------------------------------------------------- blocking
def lineage_checks(inputs: dict, raw_checksums: dict[str, str], filenames: dict[str, str]) -> list[CheckResult]:
    """Staging inputs must come from one staging run built from the current raw files."""
    out = []
    by_file = {filenames[n]: sha for n, sha in raw_checksums.items()}
    for name, inp in inputs.items():
        recorded = inp.lineage.get("source_sha256", {})
        stale = sorted(f for f, sha in recorded.items() if by_file.get(f) != sha)
        ok = bool(recorded) and not stale and inp.lineage.get("layer") == "staging" and inp.lineage.get("dataset") == name
        out.append(check(f"S01.{name}", BLOCKING, f"staging {name}: lineage present and its source SHA-256 equals the current raw file",
                         [], stale if recorded else ["<no lineage>"], passed=ok))
    run_ids = sorted({inp.lineage.get("run_id") for inp in inputs.values()}, key=str)
    out.append(check("S02", BLOCKING, "all staging inputs come from one staging run", 1, len(run_ids)))
    return out


def blocking_checks(staged: dict[str, pd.DataFrame], clean: dict[str, pd.DataFrame]) -> list[CheckResult]:
    out: list[CheckResult] = []

    for t in SOURCE_TABLES:
        st, cl = staged[t], clean[t]
        out.append(check(f"V14.{t}.rows", BLOCKING, f"{t}: clean rows = staged rows (none added or removed)", len(st), len(cl)))
        expected_cols = list(st.columns) + added_column_names(t)
        cols_ok = list(cl.columns) == expected_cols
        bad = ([c for c, spec in ADDED_COLUMNS[t] if not _dtype_ok(cl[c].dtype, spec.dtype)]
               + [c for c in st.columns if cl[c].dtype != st[c].dtype]) if cols_ok else ["<columns differ>"]
        out.append(check(f"V03.{t}", BLOCKING, f"{t}: columns = staged columns + declared additions, with declared dtypes",
                         [], bad, passed=cols_ok and not bad))
        same_rows = len(st) == len(cl)
        changed = [c for c in st.columns if not (c in cl and same_rows and cl[c].dtype == st[c].dtype
                                                  and cl[c].reset_index(drop=True).equals(st[c].reset_index(drop=True)))]
        out.append(check(f"V14.{t}", BLOCKING, f"{t}: every source column identical to staging (values, order, dtype)", [], changed))
        miss = {c: (int(st[c].isna().sum()), int(cl[c].isna().sum())) for c in st.columns if c in cl}
        out.append(check(f"V08.{t}", BLOCKING, f"{t}: missing count per source column = staging", {},
                         {c: v for c, v in miss.items() if v[0] != v[1]}))
        flag_cols = [c for c, spec in ADDED_COLUMNS[t] if spec.dtype == "bool" and c in cl]
        out.append(check(f"F01.{t}", BLOCKING, f"{t}: every flag column is boolean with no missing value", [],
                         [c for c in flag_cols if cl[c].dtype != bool or cl[c].isna().any()]))
        if t in PRIMARY_KEYS:
            key = list(PRIMARY_KEYS[t])
            out.append(check(f"V04.{t}", BLOCKING, f"{t}: primary key {tuple(key)} unique and non-null", (0, 0),
                             (int(cl.duplicated(subset=key).sum()), int(cl[key].isna().any(axis=1).sum()))))

    for name, cols in REFERENCE_SCHEMAS.items():
        df = clean[name]
        cols_ok = list(df.columns) == [c for c, _ in cols]
        bad = [c for c, d in cols if not _dtype_ok(df[c].dtype, d)] if cols_ok else ["<columns differ>"]
        out.append(check(f"V03.{name}", BLOCKING, f"{name}: columns and dtypes match the reference schema", [], bad,
                         passed=cols_ok and not bad))

    for child, ccol, parent, pcol in CORE_FOREIGN_KEYS:
        c, p = clean[child][ccol], clean[parent][pcol]
        out.append(check(f"V05.{child}.{ccol}", BLOCKING, f"{child}.{ccol} -> {parent}.{pcol}: no orphans in clean", 0,
                         int((c.notna() & ~c.isin(p)).sum())))

    for t, col in {**ZIP_COLUMNS, "zip_lookup": ZIP_KEY}.items():
        s = clean[t][col]
        out.append(check(f"V06.{t}", BLOCKING, f"{t}.{col}: all values are 5-digit strings", 0,
                         int((~s.str.fullmatch(r"\d{5}").fillna(False)).sum())))

    for t, col in MONETARY:
        out.append(check(f"V09.{t}.{col}", BLOCKING, f"{t}.{col}: clean sum = staged sum (same run)",
                         float(staged[t][col].sum()), float(clean[t][col].sum())))

    out += zip_lookup_checks(staged, clean)
    out += reuse_checks(staged, clean)
    return out


def zip_lookup_checks(staged, clean) -> list[CheckResult]:
    """V12 (clean part) plus the D-10 consistency and plausibility conditions."""
    zl, points = clean["zip_lookup"], staged["geolocation_points"]
    out = [
        check("V12.zip_key_unique", BLOCKING, "zip_lookup: zip key unique and non-null", (0, 0),
              (int(zl[ZIP_KEY].duplicated().sum()), int(zl[ZIP_KEY].isna().sum()))),
        check("V12.zip_set", BLOCKING, "zip_lookup: exactly the zip prefixes of geolocation_points", True,
              set(zl[ZIP_KEY]) == set(points[geo.ZIP])),
        check("V12.customers_join_rows", BLOCKING, "customers keep their row count through the zip_lookup join",
              len(staged["customers"]), len(clean["customers"])),
        check("V12.sellers_join_rows", BLOCKING, "sellers keep their row count through the zip_lookup join",
              len(staged["sellers"]), len(clean["sellers"])),
    ]
    per_zip = points.groupby(geo.ZIP).size()
    accounted = zl.set_index(ZIP_KEY)
    diff = int(((accounted["n_points_used"] + accounted["n_points_excluded_outside_brazil"])
                - per_zip.reindex(accounted.index).fillna(0)).abs().sum())
    out.append(check("V12.points_accounted", BLOCKING,
                     "per zip, n_points_used + n_points_excluded_outside_brazil = its distinct points (total = points rows)",
                     (0, len(points)), (diff, int(accounted["n_points_used"].sum() + accounted["n_points_excluded_outside_brazil"].sum()))))
    has = zl["lat_centroid"].notna()
    out.append(check("V12.no_valid_point_consistency", BLOCKING,
                     "centroids are missing exactly where dq_zip_no_valid_point (both axes together)", True,
                     bool((has == ~zl["dq_zip_no_valid_point"]).all() and (has == zl["lng_centroid"].notna()).all())))
    valid = points[~points["dq_outside_brazil"]]
    bounds = valid.groupby(geo.ZIP).agg(lat_min=(geo.LAT, "min"), lat_max=(geo.LAT, "max"),
                                         lng_min=(geo.LNG, "min"), lng_max=(geo.LNG, "max"))
    z = zl[has].join(bounds, on=ZIP_KEY)
    implausible = int((~(z["lat_centroid"].between(*BRAZIL_BBOX["lat"]) & z["lng_centroid"].between(*BRAZIL_BBOX["lng"])
                         & z["lat_centroid"].between(z["lat_min"], z["lat_max"])
                         & z["lng_centroid"].between(z["lng_min"], z["lng_max"]))).sum())
    out.append(check("V12.centroid_plausible", BLOCKING,
                     "every centroid lies in the bounding box and within its zip's used points on each axis", 0, implausible))
    return out


def reuse_checks(staged, clean) -> list[CheckResult]:
    """Products carry the staging metadata flags unchanged; category_map covers products."""
    flags = staged["products_metadata_flags"]
    cols = [c for c in flags.columns if c != "product_id"]
    prod = clean["products"]
    same = bool(prod["product_id"].reset_index(drop=True).equals(flags["product_id"].reset_index(drop=True))
                and all(prod[c].reset_index(drop=True).equals(flags[c].reset_index(drop=True)) for c in cols))
    cmap = clean["category_map"]
    cats = prod["product_category_name"].dropna()
    n_matches = cats.map(cmap["product_category_name"].value_counts()).fillna(0)
    return [
        check("F02.products_metadata_flags", BLOCKING, "clean products carry the staging products_metadata_flags unchanged",
              True, same),
        check("V04.category_map", BLOCKING, "category_map: key unique and non-null", (0, 0),
              (int(cmap["product_category_name"].duplicated().sum()), int(cmap["product_category_name"].isna().sum()))),
        check("M01.category_map_coverage", BLOCKING, "every non-missing product category maps to exactly one category_map row",
              0, int((n_matches != 1).sum())),
    ]


# ----------------------------------------------------------------------------- regression
def domain_rule_counts(clean: dict[str, pd.DataFrame]) -> dict[str, int]:
    it, pay, rev, prod = clean["order_items"], clean["payments"], clean["reviews"], clean["products"]
    n = lambda mask: int(mask.fillna(False).sum()) if hasattr(mask, "fillna") else int(mask.sum())  # noqa: E731
    counts = {
        "price_le_0": n(it["price"] <= 0),
        "freight_lt_0": n(it["freight_value"] < 0),
        "freight_eq_0": n(it["freight_value"] == 0),
        "payment_lt_0": n(pay["payment_value"] < 0),
        "payment_eq_0": n(pay["payment_value"] == 0),
        "installments_lt_1": n(pay["payment_installments"] < 1),
        "installments_gt_1_non_credit_card": n((pay["payment_type"].astype("string") != "credit_card")
                                               & (pay["payment_installments"] > 1)),
        "review_score_outside_1_5": n(~rev["review_score"].isin([1, 2, 3, 4, 5])),
        "weight_le_0": n(prod["product_weight_g"] <= 0),
        "any_dimension_le_0": n((prod[["product_length_cm", "product_height_cm", "product_width_cm"]] <= 0)
                                .fillna(False).any(axis=1)),
        "photos_lt_1": n(prod["product_photos_qty"] < 1),
    }
    for t, col in STATE_COLUMNS.items():
        s = clean[t][f"{col}_std"].astype("string")
        counts[f"{t}_state_not_uf"] = int((s.notna() & ~s.isin(BRAZIL_UFS)).sum())
    for (t, col), vocab in OBSERVED_VOCABULARIES.items():
        s = clean[t][col].astype("string")
        counts[f"{col}_outside_vocabulary"] = int((s.notna() & ~s.isin(vocab)).sum())
    return counts


def structure_counts(staged, clean) -> dict[str, int]:
    prod, tr, zl = clean["products"], clean["translation"], clean["zip_lookup"]
    cat = prod["product_category_name"]
    return {
        "distinct_review_id": int(clean["reviews"]["review_id"].nunique()),
        "distinct_customer_unique_id": int(clean["customers"]["customer_unique_id"].nunique()),
        "untranslated_products_before_map": int((cat.notna() & ~cat.isin(tr["product_category_name"])).sum()),
        "untranslated_products_after_map": int((cat.notna() & prod["category_en_clean"].isna()).sum()),
        "category_map_rows": len(clean["category_map"]),
        "missing_category_products": int(cat.isna().sum()),
        "zip_lookup_rows": len(zl),
        "customers_zip_absent": int((~clean["customers"]["customer_zip_code_prefix"].isin(zl[ZIP_KEY])).sum()),
        "sellers_zip_absent": int((~clean["sellers"]["seller_zip_code_prefix"].isin(zl[ZIP_KEY])).sum()),
        "city_key_distinct_customers": int(clean["customers"]["city_key"].nunique()),
        "city_key_distinct_sellers": int(clean["sellers"]["city_key"].nunique()),
        "city_key_distinct_geolocation": int(clean["geolocation"]["city_key"].nunique()),
    }


def regression_checks(staged, clean) -> list[CheckResult]:
    out: list[CheckResult] = []
    for t, n in BASELINE_ROWS.items():
        out.append(check(f"R.rows.{t}", REGRESSION, f"{t}: clean row count (NB01)", n, len(clean[t])))
    for (t, flag), n in BASELINE_CLEAN_FLAGS.items():
        out.append(check(f"R.flag.{flag}", REGRESSION, f"{t}.{flag} count (strategy 11.3.1)", n, int(clean[t][flag].sum())))
    for flag, n in BASELINE_PRODUCT_FLAGS.items():
        out.append(check(f"R.flag.{flag}", REGRESSION, f"products.{flag} count (strategy 4.3 / 11.3.1)", n,
                         int(clean["products"][flag].sum())))
    for group, (any_n, all_n) in group_equivalence(clean["products"]).items():
        out.append(check(f"R.flag_equivalence.{group}", REGRESSION,
                         f"{group} fields: products with ANY missing = products with ALL missing (strategy 4.3)", any_n, all_n))

    counts = domain_rule_counts(clean)
    for rule, n in BASELINE_DOMAIN_RULES.items():
        out.append(check(f"R.domain.{rule}", REGRESSION, f"domain rule {rule} (strategy 11.3.2)", n, counts[rule]))
    prod, pay = clean["products"], clean["payments"]
    out += [
        check("R.domain.weight_le_0_rows", REGRESSION, "rows with weight <= 0 are exactly the dq_weight_zero rows", True,
              bool(((prod["product_weight_g"] <= 0).fillna(False) == prod["dq_weight_zero"]).all())),
        check("R.domain.payment_eq_0_rows", REGRESSION, "rows with payment_value = 0 are exactly the dq_payment_zero rows", True,
              bool(((pay["payment_value"] == 0) == pay["dq_payment_zero"]).all())),
        check("R.domain.installments_lt_1_rows", REGRESSION,
              "rows with installments < 1 are exactly the dq_installments_zero rows, all credit_card", True,
              bool(((pay["payment_installments"] < 1).fillna(False) == pay["dq_installments_zero"]).all()
                   and (pay.loc[pay["dq_installments_zero"], "payment_type"].astype("string") == "credit_card").all())),
    ]

    for (t, col), total in BASELINE_MONETARY_TOTALS.items():
        out.append(check(f"R.total.{t}.{col}", REGRESSION, f"{t}.{col}: sum (NB02/NB03)", total,
                         round(float(clean[t][col].sum()), 2)))
    for name, n in structure_counts(staged, clean).items():
        out.append(check(f"R.structure.{name}", REGRESSION, f"{name} (strategy 11.3.3 / D-07 / D-11 / D-12)",
                         BASELINE_STRUCTURE[name], n))
    return out


# ----------------------------------------------------------------------------- warnings
def warning_checks(clean, notes: list[str]) -> list[CheckResult]:
    zl = clean["zip_lookup"]
    share = {}
    for t in ("customers", "sellers"):
        df = clean[t]
        with_c = int((~df["dq_no_geolocation"]).sum())
        share[t] = {"with_centroid": with_c, "dq_no_geolocation": int(df["dq_no_geolocation"].sum()),
                    "share_pct": round(with_c / len(df) * 100, 3) if len(df) else None}
    return [
        check("W03", WARNING, "zips with dq_zip_no_valid_point (centroid missing; not measured in NB01-03)", None,
              int(zl["dq_zip_no_valid_point"].sum())),
        check("W04", WARNING, "customers / sellers that receive a centroid via zip_lookup", None, share),
        check("W.review_before_delivery", WARNING,
              "reviews flagged dq_review_before_delivery (informational; NB03 5.5 measured 5,127)", None,
              int(clean["reviews"]["dq_review_before_delivery"].sum())),
        check("W.category_overrides_not_applied", WARNING, "category_map overrides with no matching source row", None,
              list(notes)),
    ]
